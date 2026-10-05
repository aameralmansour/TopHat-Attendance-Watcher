import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import config
from auth import browser, verify, SessionExpired
from detectors import NetworkDetector, DomDetector, load_rules
from notifiers import deliver
from terminal_status import TerminalStatus
from detectors.presence import PresenceDetector

log = logging.getLogger(__name__)

class CoursePage:
    def __init__(self, page, course, rules):
        self.page, self.course, self.rules = page, course, rules
        self.network = NetworkDetector(rules.get('network', []))
        self.presence = PresenceDetector(rules.get('presence', {}))
        self.present_items = set()
        self.tasks = set()
        self.errors = []
        self.ready = False
        self.expiry = None
        page.on('response', self.response)
        page.on('websocket', self.websocket)

    def expire_at(self, end):
        if self.expiry:
            self.expiry.cancel()
        async def close_at_end():
            await asyncio.sleep(max(0, (end - datetime.now(timezone.utc)).total_seconds()))
            if not self.page.is_closed():
                await self.page.close()
        self.expiry = asyncio.create_task(close_at_end())

    def response(self, response):
        async def parse():
            if response.request.resource_type not in ('xhr', 'fetch'):
                return
            if not self.presence.matches(response.url) and not any(__import__('re').search(r['url_pattern'], response.url) for r in self.network.rules):
                return
            try:
                if response.status >= 400:
                    raise RuntimeError('Matching network response failed')
                body = await response.json()
                self.network.feed(response.url, body)
                self.presence.feed(response.url, body)
            except Exception as exc:
                self.errors.append(type(exc).__name__)
        task = asyncio.create_task(parse())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def websocket(self, socket):
        def receive(payload):
            try:
                self.network.feed(socket.url, json.loads(payload))
            except (ValueError, TypeError):
                pass
        socket.on('framereceived', receive)

    async def check_missing_presence(self):
        # Only query IDs just observed in this page's active-attendance response.
        # This GET reads the student's saved response; it never submits a code.
        if not self.rules.get('presence', {}).get('check_missing_responses', False):
            return
        for identity in sorted(self.presence.active - self.presence.responses_seen):
            if not identity.isascii() or not identity.isdigit():
                continue
            if self.page.is_closed():
                raise RuntimeError('Watch window ended before presence check')
            url = f'https://app.tophat.com/api/v3/attendance/{identity}/response/'
            response = await self.page.context.request.get(url, timeout=10000, max_redirects=0)
            try:
                if response.status in (301, 302, 303, 307, 308, 401, 403):
                    raise SessionExpired('Top Hat session expired – run --login')
                if response.status != 200:
                    raise RuntimeError('Attendance confirmation check failed')
                body = await response.json()
                if not isinstance(body, dict) or type(body.get('correct')) is not bool:
                    raise RuntimeError('Attendance confirmation response has changed')
                self.presence.feed(url, body)
            finally:
                await response.dispose()

    async def poll(self):
        self.network.reset()
        self.presence.reset()
        self.present_items.clear()
        self.errors.clear()
        if self.ready and not self.page.is_closed():
            await self.page.reload(wait_until='domcontentloaded', timeout=25000)
        else:
            await self.page.goto(self.course['course_url'], wait_until='domcontentloaded', timeout=25000)
            self.ready = True
        await self.page.wait_for_timeout(self.rules.get('settle_ms', 3000))
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*list(self.tasks)), timeout=10)
        await verify(self.page, self.course['course_url'], self.rules)
        await self.check_missing_presence()
        strategy = self.rules['strategy']
        items = await self.network.detect(self.page) if strategy in ('network', 'both') else set()
        if strategy in ('dom', 'both'):
            items |= await DomDetector(self.rules.get('dom', [])).detect(self.page)
        self.present_items = self.presence.confirmed()
        items -= self.present_items
        if self.present_items:
            items.discard(None)  # A generic popup need not alert once presence is confirmed.
        if not items and not self.present_items and (self.errors or (strategy == 'network' and not self.network.matched)):
            raise RuntimeError('No recognized network signal; inspect discovery/rules')
        return items

    async def close(self):
        if self.expiry:
            self.expiry.cancel()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if not self.page.is_closed():
            await self.page.close()

async def send_once(state, notifier, course, key, message):
    if state.seen(course, key):
        return True
    if await deliver(notifier, message):
        state.mark(course, key)
        return True
    return False

async def run(args, state, notifier, relogin=None):
    sessions, next_poll, failures = {}, {}, {}
    manager = context = None
    context_closed = asyncio.Event()
    status = TerminalStatus()
    print("Watcher started. Status every 60 seconds; press Control+C to stop.", flush=True)
    reporter = asyncio.create_task(status.report())
    try:
        while True:
            try:
                if context_closed.is_set():
                    raise RuntimeError('Browser context closed')
                cfg = config.load(args.config)
                rules = load_rules(args.detection)
                status.cfg = cfg
                status.problem = None
                now = datetime.now(timezone.utc)
                active = {c['name']: (c, config.active(cfg, c, now)) for c in cfg['courses']}
                active = {name: pair for name, pair in active.items() if pair[1]}
                for name in list(sessions):
                    if name not in active or sessions[name].course != active[name][0] or sessions[name].rules != rules:
                        await sessions.pop(name).close()
                        next_poll.pop(name, None)
                if not active and context is not None:
                    await manager.__aexit__(None, None, None)
                    manager = context = None
                    context_closed.clear()
                if active and context is None:
                    manager = browser(args.profile, args.headed)
                    context = await manager.__aenter__()
                    context_closed.clear()
                    context.on('close', lambda *_: context_closed.set())
                    for page in list(context.pages):
                        await page.close()
                for name, (course, wins) in active.items():
                    if time.monotonic() < next_poll.get(name, 0):
                        continue
                    # Recheck before navigation: previous course/notification work may cross an end boundary.
                    if not config.active(cfg, course, datetime.now(timezone.utc)):
                        continue
                    status.checking.add(name)
                    try:
                        if name not in sessions:
                            sessions[name] = CoursePage(await context.new_page(), course, rules)
                        sessions[name].expire_at(max(w.end for w in wins))
                        try:
                            items = await sessions[name].poll()
                        except SessionExpired:
                            if relogin is None or not config.active(cfg, course, datetime.now(timezone.utc)):
                                raise
                            status.results[name] = (datetime.now(timezone.utc), 'session expired; attempting automatic login')
                            await relogin.recover(sessions[name].page, course['course_url'], rules)
                            if not config.active(cfg, course, datetime.now(timezone.utc)):
                                raise SessionExpired('Window ended during login recovery')
                            sessions[name].ready = False
                            items = await sessions[name].poll()
                        present = sessions[name].present_items
                        result = 'marked present' if present else 'open' if items else 'not open'
                        log.info('poll', extra={'course': name, 'result': result})
                        status.checking.discard(name)
                        status.results[name] = (datetime.now(timezone.utc), 'attendance OPEN detected' if items else 'no open-attendance signal detected')
                        failures[name] = 0
                        if present:
                            for win in wins:
                                status.present.setdefault((name, win.key), set()).update(present)
                            status.results[name] = (datetime.now(timezone.utc), 'MARKED PRESENT confirmed; item(s): ' + ', '.join(sorted(present)))
                        for item in sorted(present):
                            await send_once(state, notifier, name, f'present:item:{item}', f'Top Hat attendance PRESENT: {name} – You have been marked present – {course["attendance_url"]}')
                        for item in items:
                            keys = [f'item:{item}'] if item is not None else [f'window:{w.key}' for w in wins]
                            # Overlapping windows describe one fallback attendance episode.
                            if not any(state.seen(name, k) for k in keys):
                                if await send_once(state, notifier, name, keys[0], f'Top Hat attendance OPEN: {name} – {course["attendance_url"]}'):
                                    for key in keys[1:]:
                                        state.mark(name, key)
                            else:
                                for key in keys:
                                    state.mark(name, key)
                    except Exception as exc:
                        status.checking.discard(name)
                        status.results[name] = (datetime.now(timezone.utc), f'ERROR ({type(exc).__name__}); backing off')
                        failures[name] = failures.get(name, 0) + 1
                        log.error('poll', extra={'course': name, 'result': 'error', 'error_type': type(exc).__name__})
                        expired = isinstance(exc, SessionExpired)
                        if (expired or cfg['notify_errors']) and config.active(cfg, course, datetime.now(timezone.utc)):
                            message = 'Top Hat session expired – run --login' if expired else f'Top Hat watcher error: {name} – check logs and --check'
                            keys = [('session:' if expired else 'error:') + w.key for w in wins]
                            if any(state.seen(name, k) for k in keys):
                                for key in keys:
                                    state.mark(name, key)
                            elif await send_once(state, notifier, name, keys[0], message):
                                for key in keys[1:]:
                                    state.mark(name, key)
                        if name in sessions:
                            await sessions.pop(name).close()
                    next_poll[name] = time.monotonic() + min(cfg['poll_interval_seconds'] * 2 ** min(failures.get(name, 0), 4), 900)
            except Exception as exc:
                status.problem = type(exc).__name__
                status.checking.clear()
                log.error('cycle_error', extra={'error_type': type(exc).__name__})
                # Close browsers when config is invalid: stale windows must not keep making requests.
                for session in list(sessions.values()):
                    try:
                        await session.close()
                    except Exception:
                        pass
                sessions.clear()
                if manager is not None:
                    try:
                        await manager.__aexit__(None, None, None)
                    except Exception:
                        pass
                context = manager = None
                context_closed.clear()
                await asyncio.sleep(30)
            # Config refresh every second; no browser exists outside windows.
            await asyncio.sleep(1)
    finally:
        reporter.cancel()
        await asyncio.gather(reporter, return_exceptions=True)
        print("Watcher stopped.", flush=True)
        if manager:
            await manager.__aexit__(None, None, None)

async def check(args, course, rules):
    async with browser(args.profile, args.headed) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        session = CoursePage(page, course, rules)
        try:
            items = await session.poll()
            print(json.dumps({'course': course['name'], 'result': 'marked present' if session.present_items else 'open' if items else 'not open', 'item_ids': sorted(items, key=str), 'present_item_ids': sorted(session.present_items)}))
        finally:
            await session.close()

def clean_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))

def redact(obj):
    import re
    if isinstance(obj, dict):
        return {k: '[REDACTED]' if re.search('token|password|secret|cookie|authorization|email', k, re.I) else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj

async def discover(args, course):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = Path('discovery') / stamp
    folder.mkdir(parents=True, mode=0o700)
    tasks = set()
    async with browser(args.profile, True) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        def write(record):
            record['timestamp'] = datetime.now(timezone.utc).isoformat()
            with (folder / 'network.jsonl').open('a') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        async def capture(response):
            record = {'url': clean_url(response.url), 'method': response.request.method, 'status': response.status}
            try:
                body = redact(await response.json())
                text = json.dumps(body, ensure_ascii=False)
                record.update(json_body=text[:12000], truncated=len(text) > 12000)
            except Exception:
                record['json_body'] = None
            write(record)
        def on_response(response):
            task = asyncio.create_task(capture(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        page.on('response', on_response)
        def socket(ws):
            def received(payload):
                try:
                    body = json.dumps(redact(json.loads(payload)))
                except (ValueError, TypeError):
                    body = '[non-JSON frame]'
                write({'url': clean_url(ws.url), 'method': 'WEBSOCKET', 'status': None, 'json_body': body[:12000], 'truncated': len(body) > 12000})
            ws.on('framereceived', received)
        page.on('websocket', socket)
        await page.goto(course['course_url'], wait_until='domcontentloaded')
        print(f'Discovery recording to {folder}. Close the browser or press Ctrl+C to stop. Keep captures private.')
        try:
            while not page.is_closed():
                stamp = datetime.now(timezone.utc).strftime('%H%M%S%f')
                try:
                    (folder / f'dom-{stamp}.html').write_text(await page.content())
                    await page.wait_for_timeout(10000)
                except Exception:
                    if page.is_closed():
                        break
                    raise
        finally:
            if tasks:
                await asyncio.gather(*list(tasks), return_exceptions=True)

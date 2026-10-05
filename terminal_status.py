"""Console heartbeat; reads local state without making browser requests."""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import config

class TerminalStatus:
    def __init__(self):
        self.cfg = None
        self.problem = None
        self.checking = set()
        self.results = {}
        self.present = {}

    def lines(self, now=None):
        now = now or datetime.now(timezone.utc)
        zone = ZoneInfo(self.cfg['timezone']) if self.cfg else timezone.utc
        stamp = now.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S %Z')
        prefix = f'[{stamp}] Watcher running'
        if self.problem:
            return [f'{prefix} | Monitoring interrupted: {self.problem}; retrying. See logs/watcher.jsonl.']
        if self.cfg is None:
            return [f'{prefix} | Initializing.']
        lines = []
        for course in self.cfg['courses']:
            wins = config.active(self.cfg, course, now)
            if not wins:
                continue
            name = course['name']
            windows = ', '.join(f'{w.start.astimezone(zone):%H:%M}–{w.end.astimezone(zone):%H:%M}' for w in wins)
            current = 'checking now' if name in self.checking else 'waiting for next check'
            last = self.results.get(name)
            detail = f'last check {last[0].astimezone(zone):%H:%M:%S}: {last[1]}' if last else 'no completed check yet'
            confirmations = set().union(*(self.present.get((name, w.key), set()) for w in wins))
            if confirmations:
                detail += ' | MARKED PRESENT confirmed this window: ' + ', '.join(sorted(confirmations))
            lines.append(f'  {name} | active window {windows} | {current} | {detail}')
        return [f'{prefix} | {len(lines)} active course window(s)'] + lines if lines else [f'{prefix} | No active watch windows. Idle; no Top Hat polling.']

    async def report(self):
        while True:
            print('\n'.join(self.lines()), flush=True)
            await asyncio.sleep(60)

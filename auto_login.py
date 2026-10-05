"""Optional direct Top Hat login. Credentials remain in memory unless user supplies env/.env."""
import asyncio
import getpass
import logging
import os
import sys
import time
from urllib.parse import urlparse
from auth import SessionExpired, verify

log = logging.getLogger(__name__)


def trusted_login_page(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname == 'app.tophat.com' and p.port in (None, 443) and not p.username and not p.password and p.path.rstrip('/') == '/login'


def credentials():
    if os.getenv('TOPHAT_AUTO_LOGIN', '').lower() not in ('true', '1', 'yes'):
        return None
    email = os.getenv('TOPHAT_EMAIL', '')
    password = os.getenv('TOPHAT_PASSWORD', '')
    if not email or not password:
        if not sys.stdin.isatty():
            raise ValueError('Auto-login needs TOPHAT_EMAIL and TOPHAT_PASSWORD for non-interactive runs')
        if not email:
            email = input('Top Hat email (kept in memory): ').strip()
        if not password:
            password = getpass.getpass('Top Hat password (hidden; kept in memory): ')
    if not email or not password:
        raise ValueError('Auto-login requires a nonempty email and password')
    return email, password


class AutoLogin:
    def __init__(self, state, email, password, school='Michigan State University'):
        self.email, self.password, self.school = email, password, school
        self.state = state
        self.failures = 0
        self.lock = asyncio.Lock()
        with state.db:
            state.db.execute('CREATE TABLE IF NOT EXISTS login_attempt (id INTEGER PRIMARY KEY CHECK(id=1), attempted REAL NOT NULL)')

    def allowed(self):
        row = self.state.db.execute('SELECT attempted FROM login_attempt WHERE id=1').fetchone()
        return self.failures < 3 and (not row or time.time() - row[0] >= 900)

    async def recover(self, page, course_url, rules):
        async with self.lock:
            if not self.allowed():
                raise SessionExpired('Automatic login paused; cooldown or manual login required')
            with self.state.db:
                self.state.db.execute('INSERT OR REPLACE INTO login_attempt VALUES (1,?)', (time.time(),))
            print('Session expired. Attempting automatic Top Hat login…', flush=True)
            log.info('auto_login_started')
            try:
                await asyncio.wait_for(self._login(page, course_url, rules), timeout=60)
            except Exception:
                self.failures += 1
                # No browser exception text: it can contain filled-field values.
                log.warning('auto_login_failed')
                print('Automatic login failed; manual --login is available. Retry no sooner than 15 minutes.', flush=True)
                raise SessionExpired('Automatic Top Hat login failed – run --login') from None
            self.failures = 0
            print('Top Hat login restored. Resuming attendance checks.', flush=True)
            log.info('auto_login_restored')

    async def _login(self, page, course_url, rules):
        await page.goto('https://app.tophat.com/login/', wait_until='domcontentloaded', timeout=20000)
        await page.get_by_role('main', name='Log in to Top Hat').wait_for(state='visible', timeout=10000)
        if not trusted_login_page(page.url):
            raise SessionExpired('Unexpected login destination')
        password = page.get_by_label('Password', exact=True)
        if not await password.is_visible():
            school = page.get_by_role('combobox')
            await school.fill(self.school, timeout=5000)
            # School suggestions are asynchronous; some sessions restore selection automatically.
            for _ in range(20):
                if not trusted_login_page(page.url):
                    raise SessionExpired('Unexpected login destination')
                if await password.is_visible():
                    break
                option = page.get_by_role('option', name=self.school, exact=True)
                if await option.count() == 1 and await option.is_visible():
                    await option.click(timeout=3000)
                    break
                await asyncio.sleep(0.25)
        await password.wait_for(state='visible', timeout=10000)
        if not trusted_login_page(page.url):
            raise SessionExpired('Unexpected login destination')
        await page.get_by_label('Email', exact=True).fill(self.email, timeout=5000)
        if not trusted_login_page(page.url):
            raise SessionExpired('Unexpected login destination')
        await password.fill(self.password, timeout=5000)
        if not trusted_login_page(page.url):
            raise SessionExpired('Unexpected login destination')
        await page.get_by_role('button', name='Login', exact=True).click(timeout=5000)
        await page.wait_for_url(lambda url: urlparse(str(url)).hostname == 'app.tophat.com' and urlparse(str(url)).path.rstrip('/') != '/login', timeout=15000)
        await page.goto(course_url, wait_until='domcontentloaded', timeout=20000)
        # Positive logged-in marker observed in the user's captured course DOM.
        await page.locator('[data-click-id="user-menu"]').wait_for(state='visible', timeout=10000)
        await verify(page, course_url, rules)

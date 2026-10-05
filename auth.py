from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import re
from urllib.parse import urlparse

class SessionExpired(RuntimeError):
    pass

@asynccontextmanager
async def browser(profile, headed=False):
    from playwright.async_api import async_playwright
    Path(profile).mkdir(parents=True, exist_ok=True, mode=0o700)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(Path(profile).resolve()), headless=not headed)
        try:
            yield context
        finally:
            await context.close()

async def verify(page, course_url, rules):
    session = rules.get('session', {})
    expected = urlparse(course_url).hostname
    actual = urlparse(page.url).hostname
    if actual != expected or any(re.search(p, page.url, re.I) for p in session.get('login_url_patterns', [r'/login', r'/signin', r'/sso'])):
        raise SessionExpired('Top Hat session expired – run --login')
    for selector in session.get('login_selectors', ['input[type="password"]']):
        for element in await page.locator(selector).all():
            if await element.is_visible():
                raise SessionExpired('Top Hat session expired – run --login')
    if session.get('authenticated_selector') and not await page.locator(session['authenticated_selector']).first.is_visible():
        raise RuntimeError('Authenticated UI marker missing; inspect discovery')

async def login(profile, url):
    async with browser(profile, True) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url)
        print('Complete your Top Hat login manually, then close all browser windows.')
        done = asyncio.Event()
        context.on('close', lambda *_: done.set())
        await done.wait()

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from auto_login import AutoLogin, credentials, trusted_login_page
from auth import SessionExpired
from state import State

class AutoLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.login = AutoLogin(self.state, 'test@example.invalid', 'fake-test-password')
    def tearDown(self):
        self.state.close()

    async def test_success_and_cooldown(self):
        self.login._login = AsyncMock()
        await self.login.recover(None, 'https://app.tophat.com/e/1', {})
        with self.assertRaises(SessionExpired):
            await self.login.recover(None, 'https://app.tophat.com/e/2', {})
        self.login._login.assert_awaited_once()

    async def test_failure_sanitized(self):
        self.login._login = AsyncMock(side_effect=RuntimeError('secret-filled-browser-log'))
        with self.assertRaises(SessionExpired) as caught:
            await self.login.recover(None, '', {})
        self.assertNotIn('secret', str(caught.exception))
        self.assertFalse(self.login.allowed())

    async def test_cooldown_survives_restart(self):
        self.login._login = AsyncMock()
        await self.login.recover(None, '', {})
        another = AutoLogin(self.state, 'different', 'fake')
        self.assertFalse(another.allowed())

    async def test_failure_cap(self):
        self.login.failures = 3
        self.assertFalse(self.login.allowed())

    async def test_direct_form_flow(self):
        page = MagicMock()
        page.url = 'https://app.tophat.com/login/'
        page.goto = AsyncMock()
        page.wait_for_url = AsyncMock()
        fields = {}
        def role(kind, **kwargs):
            key = kwargs.get('name', kind)
            fields.setdefault(key, MagicMock())
            x = fields[key]
            x.wait_for = AsyncMock()
            x.is_visible = AsyncMock(return_value=True)
            x.fill = AsyncMock()
            x.click = AsyncMock()
            return x
        page.get_by_role.side_effect = role
        page.get_by_label.side_effect = lambda name, **kwargs: role("textbox", name=name)
        page.locator.return_value.wait_for = AsyncMock()
        with patch('auto_login.verify', new_callable=AsyncMock) as verify:
            await self.login._login(page, 'https://app.tophat.com/e/1', {})
        fields['Email'].fill.assert_awaited_once_with('test@example.invalid', timeout=5000)
        fields['Password'].fill.assert_awaited_once_with('fake-test-password', timeout=5000)
        fields['Login'].click.assert_awaited_once()
        verify.assert_awaited_once()

    async def test_foreign_origin_never_gets_credentials(self):
        page = MagicMock()
        page.url = 'https://example.invalid/login'
        page.goto = AsyncMock()
        page.get_by_role.return_value.wait_for = AsyncMock()
        with self.assertRaises(SessionExpired):
            await self.login._login(page, '', {})
        self.assertEqual(page.get_by_role.call_count, 1)

    def test_origin_validation(self):
        self.assertTrue(trusted_login_page('https://app.tophat.com/login/?next=course'))
        for url in ['http://app.tophat.com/login/', 'https://app.tophat.com.evil.invalid/login/', 'https://app.tophat.com/course/', 'https://app.tophat.com:8080/login/']:
            self.assertFalse(trusted_login_page(url))

    def test_credentials_opt_in_and_noninteractive(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(credentials())
        with patch.dict(os.environ, {'TOPHAT_AUTO_LOGIN':'true'}, clear=True), patch('auto_login.sys.stdin.isatty', return_value=False):
            with self.assertRaises(ValueError):
                credentials()

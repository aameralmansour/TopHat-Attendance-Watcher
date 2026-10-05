import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from detectors import load_rules
from watcher import CoursePage
from auth import SessionExpired

class PresenceFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.is_closed.return_value = False
        self.page.url = 'https://app.tophat.com/e/671500'
        self.page.goto = AsyncMock()
        self.page.wait_for_timeout = AsyncMock()
        self.response = MagicMock(status=200)
        self.response.json = AsyncMock(return_value={'correct':True, 'attended':None})
        self.response.dispose = AsyncMock()
        self.page.context.request.get = AsyncMock(return_value=self.response)
        self.rules = load_rules('detection.yaml')
        self.session = CoursePage(self.page, {'course_url':self.page.url}, self.rules)
        self.session.presence.active = {'631169077'}

    async def test_missing_response_read_only_and_confirmed(self):
        await self.session.check_missing_presence()
        self.page.context.request.get.assert_awaited_once_with('https://app.tophat.com/api/v3/attendance/631169077/response/',timeout=10000,max_redirects=0)
        self.assertEqual(self.session.presence.confirmed(), {'631169077'})
        self.response.dispose.assert_awaited_once()

    async def test_existing_response_not_requested_again(self):
        self.session.presence.responses_seen.add('631169077')
        await self.session.check_missing_presence()
        self.page.context.request.get.assert_not_awaited()

    async def test_no_active_items_no_request(self):
        self.session.presence.active.clear()
        await self.session.check_missing_presence()
        self.page.context.request.get.assert_not_awaited()

    async def test_logged_out_and_invalid_response(self):
        self.response.status = 401
        with self.assertRaises(SessionExpired):
            await self.session.check_missing_presence()
        self.response.status = 200
        self.response.json.return_value = {'correct':'true'}
        with self.assertRaises(RuntimeError):
            await self.session.check_missing_presence()
        self.assertEqual(self.session.presence.confirmed(), set())

    async def test_window_closed_no_request(self):
        self.page.is_closed.return_value = True
        with self.assertRaises(RuntimeError):
            await self.session.check_missing_presence()
        self.page.context.request.get.assert_not_awaited()

    async def test_present_wins_over_popup(self):
        async def arrive(*args, **kwargs):
            self.session.presence.active = {'631169077'}
        self.page.goto.side_effect = arrive
        with patch('watcher.verify',new_callable=AsyncMock), patch('watcher.DomDetector.detect',new_callable=AsyncMock,return_value={None}):
            result = await self.session.poll()
        self.assertEqual(result,set())
        self.assertEqual(self.session.present_items,{'631169077'})

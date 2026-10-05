from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from auth import verify, SessionExpired
from detectors import DomDetector, NetworkDetector
from notifiers import deliver
from state import State
from watcher import CoursePage, send_once

class RuntimeTests(IsolatedAsyncioTestCase):
    async def test_dom_visible_pattern_and_id(self):
        elements = []
        for visible, text, identity in [(False, 'Enter attendance code', 'hidden'), (True, 'Past attendance', 'old'), (True, 'Enter attendance code', 'live')]:
            element = MagicMock()
            element.is_visible = AsyncMock(return_value=visible)
            element.inner_text = AsyncMock(return_value=text)
            element.get_attribute = AsyncMock(return_value=identity)
            elements.append(element)
        locator = MagicMock()
        locator.count = AsyncMock(return_value=3)
        locator.nth.side_effect = elements
        page = MagicMock()
        page.locator.return_value = locator
        detector = DomDetector([{'selector':'input', 'text_pattern':'enter.*code', 'id_attribute':'data-id'}])
        self.assertEqual(await detector.detect(page), {'live'})

    async def test_session_redirect(self):
        page = MagicMock()
        page.url = 'https://login.msu.edu/'
        with self.assertRaises(SessionExpired):
            await verify(page, 'https://app.tophat.com/course', {})

    async def test_session_missing_authenticated_marker(self):
        page = MagicMock()
        page.url = 'https://app.tophat.com/course'
        page.locator.return_value.first.is_visible = AsyncMock(return_value=False)
        with self.assertRaises(RuntimeError):
            await verify(page, page.url, {'session': {'login_selectors': [], 'authenticated_selector': '#course'}})

    async def test_send_failure_not_marked_and_backoff(self):
        state = State(':memory:')
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError('failure')
        with patch('notifiers.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertFalse(await send_once(state, notifier, 'A', 'item:1', 'test'))
        self.assertEqual(notifier.send.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [2, 4])
        self.assertFalse(state.seen('A', 'item:1'))
        state.close()

    async def test_send_transient_retry(self):
        notifier = MagicMock()
        notifier.send.side_effect = [RuntimeError(), None]
        with patch('notifiers.asyncio.sleep', new_callable=AsyncMock):
            self.assertTrue(await deliver(notifier, 'test'))
        self.assertEqual(notifier.send.call_count, 2)

    async def test_tab_reused(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = 'https://app.tophat.com/course'
        page.goto = AsyncMock()
        page.reload = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.close = AsyncMock()
        page.locator.return_value.all = AsyncMock(return_value=[])
        session = CoursePage(page, {'course_url':page.url}, {'strategy':'dom', 'dom': []})
        self.assertEqual(await session.poll(), set())
        self.assertEqual(await session.poll(), set())
        page.goto.assert_awaited_once()
        page.reload.assert_awaited_once()
        await session.close()

    async def test_window_expiry_closes_tab(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.close = AsyncMock()
        session = CoursePage(page, {}, {})
        session.expire_at(datetime.now(timezone.utc) - timedelta(seconds=1))
        await session.expiry
        page.close.assert_awaited_once()

    async def test_empty_and_closed_network_signals(self):
        detector = NetworkDetector([{'url_pattern':'/attendance', 'items_path':'data.items.*', 'state_path':'state', 'id_path':'id', 'open_values':['open']}])
        detector.feed('/attendance', {'data': {'items': []}})
        self.assertTrue(detector.matched)
        self.assertEqual(await detector.detect(None), set())
        detector.feed('/attendance', {'data': {'items': [{'id':'1', 'state':'open'}]}})
        self.assertEqual(await detector.detect(None), {'1'})
        detector.feed('/attendance', {'data': {'items': [{'id':'1', 'state':'closed'}]}})
        self.assertEqual(await detector.detect(None), set())

    async def test_unknown_network_shape_is_not_closed(self):
        detector = NetworkDetector([{'url_pattern':'/attendance', 'items_path':'data.items.*', 'state_path':'state', 'open_values':['open']}])
        detector.feed('/attendance', {'data': {'items': [{'renamed_state': 'open'}]}})
        self.assertFalse(detector.matched)

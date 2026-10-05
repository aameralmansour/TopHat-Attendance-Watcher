import asyncio
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from detectors import load_rules
from detectors.presence import PresenceDetector
from state import State
from watcher import send_once

class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.d = PresenceDetector(load_rules('detection.yaml')['presence'])
        self.active_url = 'https://app.tophat.com/api/v3/attendance/get_active_attendance/'
        self.response_url = 'https://app.tophat.com/api/v3/attendance/631169077/response/'
        self.active = [{'id':631169077, 'status':'active_visible'}]

    def test_confirmed_capture_and_response_order(self):
        self.d.feed(self.response_url, {'correct':True, 'attended':None})
        self.assertEqual(self.d.confirmed(), set())
        self.d.feed(self.active_url, self.active)
        self.assertEqual(self.d.confirmed(), {'631169077'})

    def test_unanswered_wrong_and_old(self):
        self.d.feed(self.active_url, self.active)
        for value in (False, None, 'true', 1):
            self.d.feed(self.response_url, {'correct':value})
            self.assertEqual(self.d.confirmed(), set())
        self.d.feed(self.response_url.replace('631169077','123'), {'correct':True})
        self.assertEqual(self.d.confirmed(), set())

    def test_closed_and_reset(self):
        self.d.feed(self.active_url, self.active)
        self.d.feed(self.response_url, {'correct':True})
        self.d.feed(self.active_url, [])
        self.assertEqual(self.d.confirmed(), set())
        self.d.reset()
        self.assertEqual(self.d.correct, set())

    def test_present_notification_separate_and_deduplicated(self):
        state = State(':memory:')
        state.mark('CBL', 'item:631169077')
        notifier = MagicMock()
        async def exercise():
            for _ in range(2):
                await send_once(state, notifier, 'CBL', 'present:item:631169077', 'present')
            await send_once(state, notifier, 'CBL', 'present:item:other', 'present')
        asyncio.run(exercise())
        self.assertEqual(notifier.send.call_count, 2)
        state.close()

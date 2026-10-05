import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import yaml
import config
from detectors import NetworkDetector, DomDetector, load_rules, values
from state import State
from watcher import send_once

ROOT = Path(__file__).resolve().parents[1]

def instant(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.c = config.load(ROOT / 'config.example.yaml')
        self.course = self.c['courses'][0]
        self.c['skip_dates'] = []

    def test_half_open(self):
        self.assertTrue(config.active(self.c, self.course, instant('2026-10-05T09:00:00-04:00')))
        self.assertFalse(config.active(self.c, self.course, instant('2026-10-05T10:00:00-04:00')))

    def test_dst_offsets(self):
        a = config.active(self.c, self.course, instant('2026-10-26T09:30:00-04:00'))[0]
        b = config.active(self.c, self.course, instant('2026-11-02T09:30:00-05:00'))[0]
        self.assertEqual((a.start.hour, b.start.hour), (13, 14))

    def test_dst_fall_fold(self):
        self.course['windows'] = [{'days':['Sun'], 'start':'01:15', 'end':'01:45'}]
        for offset in ('-04:00', '-05:00'):
            self.assertTrue(config.active(self.c, self.course, instant('2026-11-01T01:30:00' + offset)))

    def test_dst_spring_gap(self):
        self.course['windows'] = [{'days':['Sun'], 'start':'02:15', 'end':'04:00'}]
        self.assertFalse(config.active(self.c, self.course, instant('2026-03-08T03:10:00-04:00')))
        self.assertTrue(config.active(self.c, self.course, instant('2026-03-08T03:20:00-04:00')))

    def test_overnight(self):
        self.course['windows'] = [{'days':['Mon'], 'start':'23:00', 'end':'01:00'}]
        now = instant('2026-10-06T00:30:00-04:00')
        self.assertTrue(config.active(self.c, self.course, now))
        self.course['skip_dates'] = ['2026-10-06']
        self.assertFalse(config.active(self.c, self.course, now))

    def test_skip_global_and_course(self):
        now = instant('2026-10-05T09:30:00-04:00')
        for target in (self.c, self.course):
            target['skip_dates'] = ['2026-10-05']
            self.assertFalse(config.active(self.c, self.course, now))
            target['skip_dates'] = []

    def test_config_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'config.yaml'
            p.write_text(yaml.safe_dump(self.c))
            self.assertTrue(config.load(p)['courses'][0]['enabled'])
            self.c['courses'][0]['enabled'] = False
            p.write_text(yaml.safe_dump(self.c))
            self.assertFalse(config.load(p)['courses'][0]['enabled'])
            self.c['poll_interval_seconds'] = 29
            p.write_text(yaml.safe_dump(self.c))
            with self.assertRaises(ValueError):
                config.load(p)

    def test_example_rules_refused(self):
        with self.assertRaises(ValueError):
            load_rules(ROOT / 'detection.example.yaml')

    def test_network_fixture(self):
        rules = yaml.safe_load((ROOT / 'detection.example.yaml').read_text())
        detector = NetworkDetector(rules['network'])
        body = json.loads((ROOT / 'tests/fixtures/attendance.json').read_text())
        detector.feed('https://test/EXAMPLE/attendance', body)
        self.assertEqual(detector.items, {'a1', 'a3'})
        detector.reset()
        detector.feed('https://test/other', body)
        self.assertFalse(detector.items)
        self.assertFalse(detector.matched)
        detector.feed('https://test/EXAMPLE/attendance', {'data': {'items': [{'state': 'open'}]}})
        self.assertEqual(detector.items, {None})

    def test_paths(self):
        self.assertEqual(values({'a':[{'v':True},{'v':False}]}, 'a.*.v'), [True, False])
        self.assertEqual(values({}, 'a.*.v'), [])

    def test_duplicate_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'state.db')
            state = State(path)
            state.mark('A', 'item:1')
            state.close()
            state = State(path)
            self.assertTrue(state.seen('A', 'item:1'))
            self.assertFalse(state.seen('A', 'item:2'))
            self.assertFalse(state.seen('B', 'item:1'))
            state.mark('A', 'window:2026-10-05:09:00-10:00')
            self.assertFalse(state.seen('A', 'window:2026-10-07:09:00-10:00'))
            state.close()

    def test_delivery_duplicate(self):
        class Fake:
            calls = 0
            def send(self, message):
                self.calls += 1
        notifier = Fake()
        state = State(':memory:')
        async def exercise():
            await send_once(state, notifier, 'A', 'item:1', 'hello')
            await send_once(state, notifier, 'A', 'item:1', 'hello')
            await send_once(state, notifier, 'A', 'item:2', 'hello')
        asyncio.run(exercise())
        self.assertEqual(notifier.calls, 2)
        state.close()

if __name__ == '__main__':
    unittest.main()

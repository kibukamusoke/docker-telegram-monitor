import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import detector


class DetectorTests(unittest.TestCase):
    def wrap(self, text):
        return json.dumps({'log': text, 'time': '2026-10-13T07:50:17Z'}).encode() + b'\n'

    def test_real_502_without_secret(self):
        raw = self.wrap('msg="\'502 Bad Gateway\' caused by: dial tcp 172.21.0.12:3000: connect: no route to host" Request="secret=private"')
        categories = detector.classify(raw, detector.PATTERNS)
        self.assertEqual(set(categories), {'http_502', 'network_unreachable'})
        self.assertNotIn('private', str(categories))

    def test_request_headers_are_not_error_evidence(self):
        self.assertEqual(detector.classify(self.wrap('msg="Forwarding this request" Request="502 Bad Gateway connection refused"'), detector.PATTERNS), [])

    def test_access_status_only(self):
        self.assertEqual(detector.classify(self.wrap(json.dumps({'DownstreamStatus': 502, 'RequestPath': '/private'})), detector.PATTERNS), ['http_502'])
        self.assertEqual(detector.classify(self.wrap(json.dumps({'DownstreamStatus': 200, 'RequestPath': '/502 Bad Gateway'})), detector.PATTERNS), [])

    def test_common_errors(self):
        for text, category in [('Unable to connect to the database', 'database_connection'), ('getaddrinfo EAI_AGAIN', 'dns_failure'), ('no space left on device', 'disk_full'), ('Router defined multiple times with different configurations', 'router_collision')]:
            self.assertIn(category, detector.classify(self.wrap(text), detector.PATTERNS))
        self.assertEqual(detector.classify(b'incomplete', detector.PATTERNS), [])

    def test_restart_window_and_exclusion(self):
        history = []
        self.assertFalse(detector.restart_burst(history, 1000, 2, 300, 3))
        self.assertTrue(detector.restart_burst(history, 1100, 1, 300, 3))
        self.assertFalse(detector.restart_burst(history, 1500, 0, 300, 3))
        self.assertFalse(detector.selected('tracksolAmplify', {'exclude_containers': ['tracksolAmplify'], 'include_containers': []}))

    def test_tail_resume_rotation_and_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'abc'
            folder.mkdir()
            log = folder / 'abc-json.log'
            log.write_bytes(self.wrap('502 Bad Gateway'))
            state = {}
            config = {'log_categories': list(detector.PATTERNS)}
            d = detector.Detector(config, state)
            d.sources = {'abc': 'tracksolerp2'}
            with patch.object(detector, 'LOGS', root):
                self.assertTrue(d.read_logs())
                self.assertFalse(state.get('pending'))  # Skip old incident.
                event = self.wrap('502 Bad Gateway')
                with log.open('ab') as out:
                    out.write(event[:-1])
                d.read_logs()
                self.assertFalse(state.get('pending'))
                with log.open('ab') as out:
                    out.write(b'\n')
                d.read_logs()
                self.assertEqual(state['pending']['tracksolerp2: http_502'], 1)
                d.files['abc'].close()
                d.files.clear()
                d.read_logs()  # Resume without re-counting.
                self.assertEqual(state['pending']['tracksolerp2: http_502'], 1)
                log.rename(folder / 'old.log')
                log.write_bytes(event)
                d.read_logs()
                d.read_logs()
                self.assertEqual(state['pending']['tracksolerp2: http_502'], 2)
                for handle in d.files.values():
                    handle.close()

    def test_failed_delivery_retained_and_cooldown(self):
        state = {}
        d = detector.Detector({'cooldown_seconds': 300}, state)
        d.event('tracksolerp2', 'http_502')
        with patch.object(detector, 'send', return_value=False) as send:
            d.deliver(1000)
            self.assertTrue(state['pending'])
            d.deliver(1010)
            self.assertEqual(send.call_count, 1)
        with patch.object(detector, 'send', return_value=True), patch.object(detector, 'persist'):
            d.deliver(1031)
            self.assertFalse(state['pending'])
            d.event('tracksolerp2', 'http_502')
            d.deliver(1040)
            self.assertTrue(state['pending'])
            d.deliver(1331)
            self.assertFalse(state['pending'])


if __name__ == '__main__':
    unittest.main()

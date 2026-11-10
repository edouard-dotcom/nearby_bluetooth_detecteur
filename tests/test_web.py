"""API HTTP et scanner simulé : aucun accès au matériel Bluetooth."""

import asyncio
from http.client import HTTPConnection
import json
import sqlite3
from unittest.mock import patch
from threading import Thread
import time
import unittest

from bluetooth.scanner import Observation
from web.server import DetectorServer, DetectorService


async def fake_scan(callback, stop, started):
    started()
    callback(Observation('AA', '<script>alert(1)</script>', -60, time.monotonic()))
    callback(Observation('BB', 'Faible', -85, time.monotonic()))
    while not stop.is_set():
        await asyncio.sleep(0.01)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.service = DetectorService(fake_scan, storage_path=":memory:")
        self.server = DetectorServer(0, self.service)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.thread.join(3)

    def request(self, path, payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        defaults = {'Origin': self.server.origin, 'X-Detector-Token': self.server.token,
                    'Content-Type': 'application/json'}
        defaults.update(headers or {})
        body = None if payload is None else json.dumps(payload)
        try:
            connection.request('GET' if payload is None else 'POST', path, body, defaults)
            response = connection.getresponse()
            data = response.read()
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(condition())

    def test_assets_and_status(self):
        for path in ['/', '/app.js', '/style.css', '/lab.js']:
            status, data, headers = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(data)
            if path == "/style.css":
                self.assertTrue(data.lstrip().startswith(b":root"), "La feuille CSS doit contenir des styles, pas un script.")
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(json.loads(self.request('/api/status')[1])['state'], 'stopped')
        self.assertEqual(self.request('/../../.ssh/id_rsa')[0], 404)

    def test_start_filter_stop_and_restart(self):
        self.assertEqual(self.request('/api/start', {})[0], 200)
        self.wait_for(lambda: self.service.snapshot()['count'] == 1)
        self.assertEqual(self.service.snapshot()['state'], 'scanning')
        identity = json.loads(self.request('/api/status')[1])['devices'][0]['identity']
        self.assertEqual(identity['appearances'], 1)
        self.assertEqual(identity['device_type'], 'unknown')
        self.assertEqual(self.request('/api/threshold', {'threshold': -90})[0], 200)
        self.assertEqual(self.service.snapshot()['count'], 2)
        self.request('/api/threshold', {'threshold': -50})
        self.assertEqual(self.service.snapshot()['count'], 0)
        thread = self.service.thread
        self.request('/api/start', {})
        self.assertIs(self.service.thread, thread)
        self.request('/api/stop', {})
        self.wait_for(lambda: not thread.is_alive())
        self.assertEqual(self.service.snapshot()['state'], 'stopped')
        self.request('/api/start', {})
        self.wait_for(lambda: self.service.snapshot()['state'] == 'scanning')
        self.assertIsNot(self.service.thread, thread)

    def test_foreign_origin_host_and_missing_token_rejected(self):
        for headers in [{'Origin': 'https://example.com'}, {'Host': 'evil.test'},
                        {'X-Detector-Token': ''}, {'Origin': ''}]:
            self.assertEqual(self.request('/api/start', {}, headers)[0], 403)
        self.assertEqual(self.service.snapshot()['state'], 'stopped')
        self.assertEqual(self.request('/api/session', headers={'Host': 'evil.test'})[0], 403)

    def test_history_endpoint_and_bounds(self):
        self.request('/api/start', {})
        self.wait_for(lambda: self.service.history()['total'] == 2)
        status, body, _ = self.request('/api/history?limit=1&offset=1')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['total'], 2)
        self.assertEqual(len(data['devices']), 1)
        self.assertEqual(self.request('/api/history?limit=501')[0], 400)
        self.assertEqual(self.request('/api/history?offset=-1')[0], 400)
        self.assertEqual(self.request('/api/history?limit=abc')[0], 400)
        self.assertEqual(self.request('/api/history', headers={'Origin': 'https://example.com'})[0], 403)

    def test_storage_failure_stops_scanning_and_is_reported(self):
        with patch.object(self.service.storage, 'record', side_effect=sqlite3.OperationalError('disque indisponible')):
            self.request('/api/start', {})
            self.wait_for(lambda: self.service.snapshot()['state'] == 'error')
            self.assertTrue(self.service.stop_event.is_set())
            self.assertIn('Historique SQLite', self.service.snapshot()['error'])
            self.assertEqual(self.service.snapshot()['count'], 0)

    def test_presence_expires_at_5_seconds_without_erasing_history(self):
        now = [0.0]
        self.service.counter.clock = lambda: now[0]
        item = Observation('AA', 'Test', -50, 0)
        self.service.counter.observe(item)
        self.service.storage.record(item, 'test')
        now[0] = 4.999
        self.assertEqual(json.loads(self.request('/api/status')[1])['count'], 1)
        now[0] = 5
        current = json.loads(self.request('/api/status')[1])
        self.assertEqual(current['count'], 0)
        self.assertEqual(current['devices'], [])
        self.assertEqual(json.loads(self.request('/api/history')[1])['total'], 1)

    def test_presence_settings_endpoint_and_defaults(self):
        initial = json.loads(self.request('/api/status')[1])
        self.assertEqual(initial['config']['refresh_seconds'], 1)
        self.assertEqual(initial['config']['min_rssi'], -80)
        self.assertEqual(self.request('/api/config', {'presence_seconds': 400})[0], 400)
        self.assertEqual(self.service.counter.window, 5)
        self.assertEqual(self.request('/api/config', {'presence_seconds': 15, 'retention_seconds': 120, 'period_seconds': 900})[0], 200)
        self.assertEqual(self.service.counter.retention, 120)
        self.assertEqual(self.service.snapshot()['sessions']['period_seconds'], 900)
        self.assertEqual(self.request('/api/config', {'refresh_seconds': 3}, {'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.service.config.refresh_seconds, 1)

    def test_new_detection_badge_is_distinct_from_returning_address(self):
        now = [1000.0]
        self.service.storage.clock = lambda: now[0]
        known = Observation('AA', None, -50, 0)
        self.service.storage.record(known, 'old')
        now[0] += 400
        self.service.storage.record(known, 'return')
        self.service.storage.record(Observation('BB', None, -50, 0), 'new')
        recent = json.loads(self.request('/api/status')[1])['new_detections']
        self.assertEqual(recent['total'], 1)
        self.assertEqual(recent['devices'][0]['address'], 'BB')
        self.assertEqual(self.service.history()['total'], 2)

    def test_statistics_continue_without_browser_requests(self):
        self.service.start()
        self.wait_for(lambda: self.service.state == 'scanning')
        self.wait_for(lambda: self.service.storage.statistics(self.service.config)['samples'] >= 1)
        self.assertTrue(self.service.measurements.series.points)
        self.assertEqual(self.service.storage.statistics(self.service.config)['maximum'], 1)

    def test_measurement_actions_validate_controls_and_origin(self):
        self.assertEqual(self.request('/api/lab/calibration-start', {'address': 'AA', 'distance': 1})[0], 400)
        self.request('/api/start', {})
        self.wait_for(lambda: self.service.snapshot()['state'] == 'scanning')
        self.assertEqual(self.request('/api/lab/calibration-apply', {})[0], 400)
        self.assertEqual(self.request('/api/lab/experiment-start', {'reference_count': 1, 'duration': 10, 'controlled': False})[0], 400)
        self.assertEqual(self.request('/api/lab/alerts-config', {'activity_threshold': 2, 'silence_seconds': 5}, {'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.request('/api/lab/alerts-config', {'activity_threshold': 2, 'silence_seconds': 5})[0], 200)
        self.assertEqual(self.request('/api/lab/experiment-start', {'reference_count': 1, 'duration': 10, 'controlled': True})[0], 200)
        self.assertEqual(self.request('/api/threshold', {'threshold': -70})[0], 400)
        self.assertEqual(self.request('/api/lab/experiment-cancel', {})[0], 200)
        self.assertEqual(self.service.storage.experiments()[0]['state'], 'cancelled')
        self.assertEqual(self.request('/api/lab/nonexistent', {})[0], 400)

    def test_threshold_rejects_invalid_values(self):
        for value in [-128, 1, None, True, '-70', -70.5]:
            self.assertEqual(self.request('/api/threshold', {'threshold': value})[0], 400)
        self.assertEqual(self.service.counter.min_rssi, -80)
        self.assertEqual(self.request('/api/start', [1])[0], 400)

    def test_backend_error_is_reported_and_restartable(self):
        async def broken(*args):
            raise RuntimeError('Adaptateur absent')
        self.service.scan_function = broken
        self.request('/api/start', {})
        self.wait_for(lambda: self.service.snapshot()['state'] == 'error')
        self.wait_for(lambda: not self.service.thread.is_alive())
        self.assertEqual(self.service.snapshot()['error'], 'Adaptateur absent')
        self.service.scan_function = fake_scan
        self.request('/api/start', {})
        self.wait_for(lambda: self.service.snapshot()['state'] == 'scanning')
        self.assertIsNone(self.service.snapshot()['error'])

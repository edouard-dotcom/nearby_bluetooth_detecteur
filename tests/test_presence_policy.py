"""Présence, conservation bornée et agrégats de sessions, avec horloges simulées."""

import unittest
from dataclasses import replace

from config import DetectorConfig
from bluetooth.scanner import Observation
from compteur.counter import DeviceCounter
from database.storage import Storage
from web.server import DetectorService


class PresencePolicyTests(unittest.TestCase):
    def test_defaults_and_invalid_settings(self):
        config = DetectorConfig()
        self.assertEqual((config.min_rssi, config.presence_seconds, config.retention_seconds,
                          config.refresh_seconds, config.period_seconds), (-80, 5, 300, 1, 300))
        for changes in [{'presence_seconds': 0}, {'retention_seconds': 4},
                        {'presence_seconds': float('nan')}, {'refresh_seconds': 0.1},
                        {'period_seconds': True}, {'min_rssi': -128}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(config, **changes)

    def test_active_expired_then_purged_without_losing_aggregates(self):
        now = [0.0]
        service = DetectorService(storage_path=':memory:', config=DetectorConfig(presence_seconds=30))
        service.counter.clock = lambda: now[0]
        service.storage.clock = lambda: now[0]
        try:
            item = Observation('AA', None, -80, 0)
            service.storage.record(item, 'one')
            service.counter.observe(item)
            for age, active, expired, temporary in [(29.99, 1, 0, 1), (30, 0, 1, 1),
                                                    (299.99, 0, 1, 1), (300, 0, 0, 0)]:
                now[0] = age
                service.maintenance()
                snapshot = service.snapshot()
                self.assertEqual(snapshot['count'], active)
                self.assertEqual(snapshot['metrics']['expired_addresses'], expired)
                self.assertEqual(snapshot['metrics']['temporary_addresses'], temporary)
                self.assertEqual(service.history()['total'], 1)
                self.assertEqual(service.storage.get('AA')['total_detections'], 1)
            self.assertEqual(service.counter._first_seen, {})
            self.assertEqual(service.storage.get('AA')['appearances'], 1)
        finally:
            service.close()

    def test_repeated_announcements_and_quick_restart_do_not_add_sessions(self):
        now = [1000.0]
        store = Storage(':memory:', clock=lambda: now[0])
        try:
            item = Observation('AA', None, -50, 0)
            store.record(item, 'scan-1')
            now[0] += 5
            store.record(item, 'scan-1')
            now[0] += 5
            store.record(item, 'scan-2')
            self.assertEqual(store.get('AA')['appearances'], 1)
            self.assertEqual(store.get('AA')['total_detections'], 3)
            now[0] += 30
            store.record(item, 'scan-2')
            self.assertEqual(store.get('AA')['appearances'], 2)
            self.assertEqual(store.summary(300)['sessions_observed'], 2)
            now[0] += 10
            self.assertEqual(store.summary(15)['sessions_observed'], 1)
            now[0] += 10
            self.assertEqual(store.summary(15)['sessions_observed'], 0)
            self.assertEqual(store.get('AA')['appearances'], 2)
        finally:
            store.close()

    def test_configurable_delays_and_separate_addresses(self):
        now = [0.0]
        counter = DeviceCounter(window=10, retention=20, min_rssi=-80, clock=lambda: now[0])
        for address in ['AA', 'BB']:
            counter.observe(Observation(address, 'Même nom', -60, 0))
        self.assertEqual(counter.metrics()['active_addresses'], 2)
        now[0] = 10
        self.assertEqual(counter.metrics()['expired_addresses'], 2)
        counter.observe(Observation('AA', None, -60, 10))
        self.assertEqual(counter.metrics()['active_addresses'], 1)
        self.assertEqual(counter.metrics()['expired_addresses'], 1)
        self.assertEqual(counter.presence_times('AA')['first_seen_age'], 0)
        now[0] = 20
        self.assertEqual(counter.metrics()['temporary_addresses'], 1)
        self.assertEqual(counter.metrics()['expired_addresses'], 1)

    def test_arrival_alert_lasts_ten_seconds_without_refresh_on_repeated_packets(self):
        now = [0.0]
        service = DetectorService(storage_path=':memory:')
        service.counter.clock = lambda: now[0]
        service.storage.clock = lambda: now[0]
        service.session_id = 'test'
        try:
            item = Observation('AA', 'Téléphone de Jean', -60, 0)
            service.observe(item)
            first = service.snapshot()['recent_arrivals'][0]
            self.assertEqual(first['identity']['name'], 'Téléphone de Jean')
            self.assertEqual(first['remaining_seconds'], 10)
            now[0] = 3
            service.observe(replace(item, last_seen=3))
            self.assertEqual(service.snapshot()['recent_arrivals'][0]['remaining_seconds'], 7)
            for timestamp in [6, 9]:
                now[0] = timestamp
                service.observe(replace(item, last_seen=timestamp))
            now[0] = 10
            self.assertEqual(service.snapshot()['recent_arrivals'], [])
            self.assertEqual(service.snapshot()['count'], 1)
            self.assertEqual(service.storage.get('AA')['appearances'], 1)
            now[0] = 33
            service.observe(replace(item, last_seen=33))
            second = service.snapshot()['recent_arrivals'][0]
            self.assertNotEqual(first['event_id'], second['event_id'])
            self.assertEqual(second['remaining_seconds'], 10)
            service.observe(Observation('BB', None, -95, 33))
            self.assertEqual(len(service.snapshot()['recent_arrivals']), 1)
            now[0] = 34
            service.observe(Observation('BB', None, -60, 34))
            self.assertEqual(len(service.snapshot()['recent_arrivals']), 2)
        finally:
            service.close()

    def test_configuration_validation_is_atomic_and_preserves_history(self):
        service = DetectorService(storage_path=':memory:')
        try:
            previous = service.config
            for payload in [{'presence_seconds': 400}, {'refresh_seconds': False},
                            {'presence_seconds': '30'}, {'unknown': 1}, {}]:
                with self.assertRaises(ValueError):
                    service.configure(payload)
                self.assertEqual(service.config, previous)
            service.configure({'presence_seconds': 15, 'retention_seconds': 120,
                               'refresh_seconds': 3, 'period_seconds': 900})
            self.assertEqual(service.counter.window, 15)
            self.assertEqual(service.counter.retention, 120)
            self.assertEqual(service.storage.gap_seconds, 15)
            self.assertEqual(service.snapshot()['config']['refresh_seconds'], 3)
        finally:
            service.close()

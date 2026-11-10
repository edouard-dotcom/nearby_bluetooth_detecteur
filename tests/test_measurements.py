"""Validation déterministe V2 ; aucune affirmation d’expérience physique réelle."""

import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict

from bluetooth.scanner import Observation
from bluetooth.identifier import identify
from config import DetectorConfig
from database.storage import Storage
from measurements.calibration import Calibration
from measurements.series import LiveSeries
from measurements.experiment import Experiment
from measurements.alerts import AlertMonitor
from web.server import DetectorService


class MeasurementTests(unittest.TestCase):
    def test_calibration_four_distances_statistics_and_recommendation(self):
        now = [0.0]
        calibration = Calibration(lambda: now[0])
        for distance, rssi in [(1, -40), (3, -55), (5, -65), (10, -77)]:
            calibration.start('AA', distance, 5)
            for _ in range(6):
                calibration.observe(Observation('BB', None, -99, now[0]))
                calibration.observe(Observation('AA', None, rssi, now[0]))
            now[0] += 5
            calibration.tick()
        result = calibration.snapshot()
        self.assertEqual(result['recommended_threshold'], -80)
        self.assertEqual(result['results'][-1]['samples'], 6)
        self.assertEqual(result['results'][-1]['mean'], -77)
        self.assertEqual(result['results'][-1]['stdev'], 0)
        with self.assertRaises(ValueError):
            calibration.start('CC', 1, 5)

    def test_calibration_no_data_cancel_and_validation(self):
        now = [0.0]
        calibration = Calibration(lambda: now[0])
        for args in [('AA', 2, 5), ('AA', True, 5), ('AA', 1, float('nan')), ('', 1, 5)]:
            with self.assertRaises(ValueError):
                calibration.start(*args)
        calibration.start('AA', 1, 5)
        with self.assertRaises(ValueError):
            calibration.start('AA', 3, 5)
        now[0] = 5
        result = calibration.snapshot()
        self.assertEqual(result['state'], 'insufficient_data')
        self.assertIsNone(result['results'][0]['mean'])
        self.assertIsNone(result['recommended_threshold'])
        calibration.reset()
        self.assertEqual(calibration.snapshot()['results'], [])

    def test_series_is_bounded_and_pauses_are_not_zero_measurements(self):
        series = LiveSeries(capacity=3)
        self.assertIsNone(series.sample(0.5, 0, [], False)['active'])
        self.assertIsNone(series.sample(0.9, 0, [], True))
        for value in [2, 3, 4]:
            series.sample(value, value, [], True)
        self.assertEqual(len(series.snapshot()), 3)
        self.assertTrue(all(p['active'] == 0 for p in series.snapshot()))

    def test_experiment_relative_error_and_zero_reference(self):
        now = [0.0]
        experiment = Experiment(lambda: now[0])
        experiment.start(2, 10, asdict(DetectorConfig()), True)
        for value in range(10):
            now[0] = value
            experiment.tick(3)
        now[0] = 10
        experiment.tick(0)
        result = experiment.snapshot()['last_result']
        self.assertEqual(result['sample_count'], 10)
        self.assertEqual(result['mean_active'], 3)
        self.assertEqual(result['relative_error_percent'], 50)
        experiment.start(0, 10, asdict(DetectorConfig()), True)
        experiment.tick(2)
        now[0] += 10
        experiment.tick(0)
        self.assertIsNone(experiment.last_result['relative_error_percent'])
        self.assertEqual(experiment.last_result['absolute_error'], 2)

    def test_experiment_requires_controlled_reference(self):
        experiment = Experiment()
        for reference, duration, controlled in [(1, 10, False), (-1, 10, True), (True, 10, True), (1, 0, True)]:
            with self.assertRaises(ValueError):
                experiment.start(reference, duration, asdict(DetectorConfig()), controlled)

    def test_standard_appearance_and_conflicting_clues(self):
        for appearance, expected in [(0x40, 'phone'), (0x83, 'computer'), (0x87, 'tablet'), (0xC2, 'smartwatch'), (0x941, 'headphones'), (0x942, 'headset')]:
            with self.subTest(appearance=appearance):
                result = identify(Observation('AA', None, -60, 0, appearance=appearance))
                self.assertEqual(result.device_type, expected)
                self.assertIn('Appearance', result.reason)
        self.assertEqual(identify(Observation('AA', 'iPhone', -60, 0, appearance=0xC2)).device_type, 'unknown')
        self.assertEqual(identify(Observation('AA', None, -60, 0, appearance=0xFFFF)).device_type, 'unknown')

    def test_alert_threshold_silence_recovery_no_repeated_events(self):
        alerts = AlertMonitor()
        alerts.configure(3, 5)
        first = alerts.update('scanning', None, 3, 1, 1, 0, 1)
        self.assertEqual(first['current'][0]['code'], 'high_activity')
        second = alerts.update('scanning', None, 4, 2, 2, 0, 2)
        self.assertEqual(len(second['events']), 1)
        silence = alerts.update('scanning', None, 0, 7, 7, 0, 2)
        self.assertEqual(silence['current'][0]['code'], 'radio_silence')
        restored = alerts.update('scanning', None, 0, 8, 8, 0, 8)
        self.assertFalse(restored['current'])
        self.assertTrue(restored['events'][0]['resolved'])
        error = alerts.update('error', 'Adaptateur absent', 0, 9, 9, 0, 8)
        self.assertEqual(error['current'][0]['code'], 'scanner_error')
        self.assertFalse(alerts.update('stopped', None, 0, 100, 100, 0, 8)['current'])

    def test_persistent_minute_and_hourly_aggregates_separate_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'statistics.sqlite3'
            store = Storage(path, clock=lambda: 4000)
            config = DetectorConfig()
            store.save_minute_sample(dict(timestamp=3700, active=2, rssi_sum=-120, rssi_count=2), config)
            store.save_minute_sample(dict(timestamp=3701, active=4, rssi_sum=-60, rssi_count=1), config)
            stats = store.statistics(config)
            self.assertEqual(stats['samples'], 2)
            self.assertEqual(stats['mean_active'], 3)
            self.assertEqual(stats['maximum'], 4)
            self.assertEqual(stats['points'][0]['mean_rssi'], -60)
            self.assertEqual(stats['hourly'][0]['mean_active'], 3)
            self.assertEqual(store.statistics(DetectorConfig(min_rssi=-70))['samples'], 0)
            store.close()
            reopened = Storage(path, clock=lambda: 4000)
            self.assertEqual(reopened.statistics(config)['samples'], 2)
            reopened.close()

    def test_statistic_retention_and_persistent_experiment_results(self):
        now = [0.0]
        store = Storage(':memory:', clock=lambda: now[0])
        try:
            config = DetectorConfig()
            store.save_minute_sample(dict(timestamp=0, active=2, rssi_sum=-120, rssi_count=2), config)
            now[0] = 31 * 86400
            store.save_minute_sample(dict(timestamp=now[0], active=1, rssi_sum=-60, rssi_count=1), config)
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM minute_statistics').fetchone()[0], 1)
            store.save_experiment({'id': 'test', 'state': 'complete', 'reference_count': 1, 'mean_active': 2})
            self.assertEqual(store.experiments()[0]['id'], 'test')
            self.assertNotIn('address', json.dumps(store.experiments()))
        finally:
            store.close()

    def test_bulk_identity_preserves_appearance(self):
        store = Storage(':memory:')
        try:
            store.record(Observation('AA', None, -60, 0, appearance=0xC2), 'test')
            self.assertEqual(store.get_many(['AA', 'BB'])['AA']['device_type'], 'smartwatch')
            self.assertEqual(store.history()['devices'][0]['appearance'], 0xC2)
        finally:
            store.close()


class MeasurementServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.service = DetectorService(storage_path=':memory:')
        self.service.counter.clock = lambda: self.now
        self.service.storage.clock = lambda: 1000 + self.now
        self.service.session_id = 'test'
        self.service.state = 'scanning'
        self.service.measurements.scan_started = 0

    def tearDown(self):
        self.service.close()

    def test_experiment_parameter_freeze_result_persistence_and_estimate_invalidation(self):
        lab = self.service.measurements
        lab.action('experiment-start', {'reference_count': 1, 'duration': 10, 'controlled': True})
        for value in range(1, 10):
            self.now = value
            self.service.observe(Observation('AA', None, -60, value))
            self.service.maintenance()
        with self.assertRaises(ValueError):
            self.service.set_threshold(-60)
        with self.assertRaises(ValueError):
            self.service.configure({'presence_seconds': 10})
        self.now = 10
        self.service.maintenance()
        result = self.service.storage.experiments()[0]
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['sample_count'], 10)
        lab.action('estimate-use', {'id': result['id']})
        self.assertIsNotNone(self.service.snapshot()['measurements']['estimate']['value'])
        self.service.set_threshold(-60)
        self.assertIsNone(self.service.snapshot()['measurements']['estimate']['value'])
        self.assertEqual(self.service.storage.history()['total'], 1)

    def test_failed_scanner_cancels_experiment(self):
        self.service.measurements.action('experiment-start', {'reference_count': 1, 'duration': 10, 'controlled': True})
        self.service.state = 'error'
        self.service.error = 'Radio absente'
        self.service.maintenance()
        self.assertEqual(self.service.storage.experiments()[0]['state'], 'cancelled')
        self.assertEqual(self.service.snapshot()['measurements']['alerts']['current'][0]['code'], 'scanner_error')

    def test_calibration_requires_real_samples_and_does_not_change_threshold_automatically(self):
        lab = self.service.measurements
        self.service.observe(Observation('AA', None, -60, 0))
        with self.assertRaises(ValueError):
            lab.action('calibration-apply', {})
        for distance, rssi in [(1, -40), (3, -55), (5, -65), (10, -77)]:
            lab.action('calibration-start', {'address': 'AA', 'distance': distance, 'duration': 5})
            for _ in range(6):
                self.service.observe(Observation('AA', None, rssi, self.now))
            self.now += 5
            self.service.maintenance()
        self.assertEqual(len(self.service.storage.calibrations()), 4)
        self.assertEqual(self.service.counter.min_rssi, -80)
        self.assertEqual(lab.calibration.snapshot()['recommended_threshold'], -80)
        self.assertNotIn('AA', json.dumps(self.service.storage.calibrations()))

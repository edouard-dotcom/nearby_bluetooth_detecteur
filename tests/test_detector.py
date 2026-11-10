"""Tests synthétiques ; aucun accès à la radio Bluetooth."""

import asyncio
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from bluetooth.scanner import Observation, scan
from compteur.counter import DeviceCounter
from interface.window import DetectorWindow


class CounterTests(unittest.TestCase):
    def test_dedup_refresh_expire_and_name_retention(self):
        now = [0.0]
        counter = DeviceCounter(clock=lambda: now[0])
        counter.observe(Observation('aa', 'Téléphone', -50, 0))
        now[0] = 20
        counter.observe(Observation('AA', None, -65, 20))
        counter.observe(Observation('BB', None, None, 20))
        counter.observe(Observation('AA', 'Ancien', -99, 10))
        self.assertEqual(len(counter), 2)
        self.assertEqual(counter.snapshot()[0].name, 'Téléphone')
        self.assertEqual(counter.snapshot()[0].rssi, -65)
        now[0] = 49.9
        self.assertEqual(len(counter), 2)
        now[0] = 50
        self.assertEqual(len(counter), 0)
        counter.observe(Observation('CC', None, -60, 0))
        self.assertEqual(len(counter), 0)


class PresenceTests(unittest.TestCase):
    def test_rotation_expires_and_new_packet_refreshes_presence(self):
        now = [0.0]
        counter = DeviceCounter(clock=lambda: now[0])
        counter.observe(Observation('AA', 'Ancienne adresse', -50, 0))
        now[0] = 20
        counter.observe(Observation('BB', 'Nouvelle adresse', -50, 20))
        self.assertEqual(len(counter), 2)  # Pas de fusion spéculative.
        now[0] = 29.999
        self.assertEqual(len(counter), 2)
        now[0] = 30
        self.assertEqual([item.address for item in counter.snapshot()], ['BB'])
        now[0] = 45
        counter.observe(Observation('BB', None, -50, 45))
        now[0] = 50
        self.assertEqual(len(counter), 1)
        now[0] = 75
        self.assertEqual(len(counter), 0)
        # Une nouvelle annonce purge les anciens éléments avant tout affichage.
        counter.observe(Observation('CC', None, -50, 75))
        self.assertEqual([item.address for item in counter.snapshot()], ['CC'])


class MetadataTests(unittest.TestCase):
    def test_partial_advertisement_preserves_previous_hints(self):
        counter = DeviceCounter(clock=lambda: 20)
        counter.observe(Observation('AA', 'iPhone', -50, 10, ('180f',), {76: b'abc'}))
        counter.observe(Observation('AA', None, -65, 20))
        item = counter.snapshot()[0]
        self.assertEqual(item.name, 'iPhone')
        self.assertEqual(item.service_uuids, ('180f',))
        self.assertEqual(item.manufacturer_data, {76: b'abc'})
        self.assertEqual(item.rssi, -65)


class RangeTests(unittest.TestCase):
    def test_threshold_boundary_missing_rssi_and_live_changes(self):
        counter = DeviceCounter(clock=lambda: 10, min_rssi=-70)
        for address, rssi in [('AA', -69), ('BB', -70), ('CC', -71), ('DD', None)]:
            counter.observe(Observation(address, None, rssi, 10))
        self.assertEqual([item.address for item in counter.snapshot()], ['AA', 'BB'])
        counter.observe(Observation('AA', None, -80, 10))
        self.assertEqual([item.address for item in counter.snapshot()], ['BB'])
        counter.min_rssi = -80
        self.assertEqual(len(counter), 3)
        counter.min_rssi = -60
        self.assertEqual(len(counter), 0)


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_and_context_cleanup(self):
        stop = Event()
        observations = []
        lifecycle = []

        class Backend:
            def __init__(self, detection_callback):
                self.callback = detection_callback
            async def __aenter__(self):
                lifecycle.append('start')
                self.callback(SimpleNamespace(address='aa', name='Fallback', details={'props': {'AddressType': 'random', 'Appearance': 194}}),
                              SimpleNamespace(local_name='Annonce', rssi=-42, service_uuids=['180F'], manufacturer_data={76: b'abc'}))
            async def __aexit__(self, *args):
                lifecycle.append('stop')

        await scan(observations.append, stop, stop.set, scanner_factory=Backend)
        self.assertEqual(lifecycle, ['start', 'stop'])
        self.assertEqual(observations[0].address, 'AA')
        self.assertEqual(observations[0].address_type, 'random')
        self.assertEqual(observations[0].appearance, 194)
        self.assertEqual(observations[0].name, 'Annonce')
        self.assertEqual(observations[0].rssi, -42)
        self.assertEqual(observations[0].service_uuids, ('180f',))
        self.assertEqual(observations[0].manufacturer_data, {76: b'abc'})

    async def test_backend_failure_propagates(self):
        def broken(**kwargs):
            raise RuntimeError('Adaptateur absent')
        with self.assertRaisesRegex(RuntimeError, 'Adaptateur absent'):
            await scan(lambda value: None, Event(), scanner_factory=broken)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_start_stop_restart_and_close(self):
        async def fake_scan(callback, stop, started):
            started()
            callback(Observation('AA', 'Test', -50, time.monotonic()))
            while not stop.is_set():
                await asyncio.sleep(0.01)

        with patch('interface.window.scan', fake_scan):
            window = DetectorWindow(storage_path=":memory:")
            window.show()
            try:
                window.start_button.click()
                self.wait_for(lambda: len(window.counter) == 1)
                window.refresh()
                self.assertEqual(window.count_label.text(), '1')
                self.assertEqual(window.table.item(0, 0).text(), 'Test')
                self.assertIn('inconnu', window.table.item(0, 3).text())
                self.assertEqual(window.table.item(0, 5).text(), '1')
                self.assertEqual(window.history_table.rowCount(), 1)
                self.assertFalse(window.start_button.isEnabled())
                window.stop_button.click()
                self.wait_for(lambda: window.worker is None)
                self.assertTrue(window.start_button.isEnabled())
                window.start_button.click()
                self.wait_for(lambda: len(window.counter) == 1)
                window.refresh()
                self.assertEqual(window.table.item(0, 5).text(), '1')
                window.close()
                self.wait_for(lambda: window.worker is None)
                self.assertFalse(window.isVisible())
            finally:
                window.close()
                self.wait_for(lambda: window.worker is None)

    def test_threshold_updates_table_and_count_together(self):
        window = DetectorWindow(storage_path=":memory:")
        try:
            self.assertEqual(window.rssi_threshold.value(), -80)
            now = time.monotonic()
            window.counter.observe(Observation('AA', 'Proche', -60, now))
            window.counter.observe(Observation('BB', 'Faible', -85, now))
            window.refresh()
            self.assertEqual(window.table.rowCount(), 1)
            self.assertEqual(window.count_label.text(), '1')
            window.rssi_threshold.setValue(-85)
            self.assertEqual(window.table.rowCount(), 2)
            self.assertEqual(window.count_label.text(), '2')
            window.rssi_threshold.setValue(-50)
            self.assertEqual(window.table.rowCount(), 0)
            self.assertEqual(window.count_label.text(), '0')
        finally:
            window.close()

    def test_expired_address_leaves_current_view_but_stays_in_history(self):
        now = [0.0]
        counter = DeviceCounter(clock=lambda: now[0])
        window = DetectorWindow(counter=counter, storage_path=":memory:")
        try:
            item = Observation('AA', 'Test', -50, 0)
            window.storage.record(item, 'session')
            counter.observe(item)
            window.refresh()
            self.assertEqual(window.count_label.text(), '1')
            now[0] = 30
            window.refresh()
            self.assertEqual(window.count_label.text(), '0')
            self.assertEqual(window.table.rowCount(), 0)
            self.assertEqual(window.history_table.rowCount(), 1)
        finally:
            window.close()

    def test_presence_controls_and_metrics(self):
        window = DetectorWindow(storage_path=":memory:")
        now = [0.0]
        window.counter.clock = lambda: now[0]
        window.storage.clock = lambda: now[0]
        try:
            self.assertEqual(window.timer.interval(), 1000)
            item = Observation('AA', None, -60, 0)
            window.counter.observe(item)
            window.storage.record(item, 'test')
            window.refresh()
            self.assertEqual(window.sessions_label.text(), 'Sessions observées : 1')
            now[0] = 30
            window.refresh()
            self.assertEqual(window.expired_label.text(), 'Adresses expirées : 1')
            now[0] = 300
            window.refresh()
            self.assertEqual(window.expired_label.text(), 'Adresses expirées : 0')
            self.assertEqual(window.history_table.rowCount(), 1)
            window.presence_input.setValue(15)
            window.retention_input.setValue(120)
            window.refresh_input.setValue(3)
            window.apply_presence_settings()
            self.assertEqual(window.counter.window, 15)
            self.assertEqual(window.timer.interval(), 3000)
        finally:
            window.close()

    def test_scan_error_recovers_buttons(self):
        async def broken(*args):
            raise RuntimeError('Radio désactivée')
        with patch('interface.window.scan', broken):
            window = DetectorWindow(storage_path=":memory:")
            try:
                window.start_scan()
                self.wait_for(lambda: window.worker is None)
                self.assertIn('Radio désactivée', window.status.text())
                self.assertTrue(window.start_button.isEnabled())
                self.assertFalse(window.stop_button.isEnabled())
            finally:
                window.close()

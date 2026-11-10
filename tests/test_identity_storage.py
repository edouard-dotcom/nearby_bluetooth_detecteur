"""Classification prudente et historique SQLite synthétique isolé."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from bluetooth.identifier import identify, identify_brand
from bluetooth.scanner import Observation
from database.storage import Storage


class IdentifierTests(unittest.TestCase):
    def test_explicit_names_are_only_indicative(self):
        for name, expected in [('iPhone de Camille', 'smartphone'), ('Pixel 9', 'smartphone'),
                               ('Galaxy S24', 'smartphone'), ('Galaxy Watch 6', 'smartwatch'),
                               ('Apple Watch', 'smartwatch'), ('AirPods Pro', 'headphones'),
                               ('Galaxy Buds', 'headphones'), ('MacBook Pro', 'computer')]:
            with self.subTest(name=name):
                result = identify(Observation('AA', name, -60, 0))
                self.assertEqual(result.device_type, expected)
                self.assertEqual(result.confidence, 'indicatif')
                self.assertIn('nom', result.reason)

    def test_documented_cdp_type_without_a_device_name(self):
        for sku, expected in [(6, 'smartphone'), (7, 'tablet'), (9, 'computer'), (15, 'computer')]:
            for version in [0, 0x20]:
                payload = bytes([1, version | sku, 0x20, 0]) + bytes(23)
                result = identify(Observation('AA', None, -60, 0, (), {6: payload}))
                self.assertEqual(result.device_type, expected)
                self.assertIn('CDP', result.reason)
        for payload in [b'', b'\x01\x09', bytes([3, 9, 0x20, 0])+bytes(23),
                        bytes([1, 9, 0xFF, 0])+bytes(23), bytes([1, 0xA9, 0x20, 0])+bytes(23)]:
            self.assertEqual(identify(Observation('AA', None, -60, 0, (), {6: payload})).device_type, 'unknown')
        conflict = bytes([1, 9, 0x20, 0])+bytes(23)
        self.assertEqual(identify(Observation('AA', 'iPhone', -60, 0, (), {6: conflict})).device_type, 'unknown')

    def test_observed_oneplus_and_product_name_regressions(self):
        for name, expected in [('Téléphone de Jean', 'phone'), ('Headset', 'headset'),
                               ('OnePlus Buds Pro 2', 'headphones'),
                               ('LE_OnePlus_Buds_Pro_2', 'headphones'),
                               ('AirPods Max', 'headset'), ('AirPods Pro', 'headphones'),
                               ('PC_JEAN', 'computer'), ('iPad Pro', 'tablet'),
                               ('Galaxy Z Fold 6', 'smartphone'), ('OnePlus 12', 'smartphone')]:
            with self.subTest(name=name):
                self.assertEqual(identify(Observation('AA', name, -60, 0)).device_type, expected)
        self.assertEqual(identify_brand('OnePlus Buds Pro 2').brand, 'OnePlus')
        self.assertEqual(identify(Observation('AA', 'OnePlus', -60, 0)).device_type, 'unknown')
        self.assertEqual(identify_brand(None, [76]).confidence, 'format')
        self.assertEqual(identify(Observation('AA', None, -60, 0, (), {76: b'abc'})).device_type, 'unknown')

    def test_real_sony_name_with_low_energy_prefix(self):
        for name, expected in [('LE_WH-1000XM6', 'headset'), ('LE_WH-1000XM5', 'headset'),
                               ('LE_WF-1000XM5', 'headphones')]:
            observation = Observation('AA', name, -60, 0)
            self.assertEqual(identify(observation).device_type, expected)
            self.assertEqual(identify_brand(name).brand, 'Sony')
        self.assertEqual(identify(Observation('AA', None, -60, 0, (), {301: b'abc'})).device_type, 'unknown')

    def test_manufacturer_services_and_ambiguous_names_do_not_prove_type(self):
        for name in [None, 'Samsung', 'Apple', 'iPhone AirPods']:
            result = identify(Observation('AA', name, -60, 0, ('180d',), {76: b'abc'}))
            self.assertEqual(result.device_type, 'unknown')
            self.assertEqual(result.confidence, 'inconnu')
            self.assertIn('180d', result.reason)
            self.assertIn('0x004C', result.reason)


class BrandTests(unittest.TestCase):
    def test_company_ids_and_explicit_product_names(self):
        for name, ids, expected in [(None, [76], 'Apple'), (None, [117], 'Samsung'),
                                    ('Pixel 9', [], 'Google'), ('WH-1000XM5', [], 'Sony'),
                                    ('ThinkPad', [], 'Lenovo'), (None, [224, 398], 'Google')]:
            with self.subTest(name=name, ids=ids):
                result = identify_brand(name, ids)
                self.assertEqual(result.brand, expected)
                self.assertEqual(result.confidence, 'indicatif' if name else 'format')

    def test_unknown_conflicting_and_chip_company_ids(self):
        for name, ids in [(None, []), (None, [0xFFFF]), ('iPhone', [117]), (None, [76, 117])]:
            self.assertEqual(identify_brand(name, ids).brand, 'Marque inconnue')
        self.assertEqual(identify(Observation('AA', None, -60, 0, (), {76: b'abc'})).device_type, 'unknown')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.store = Storage(':memory:', clock=lambda: self.now)
        self.observation = Observation('aa', 'iPhone', -60, 999999.0,
                                       ('180f',), {76: b'not-persisted'})

    def tearDown(self):
        self.store.close()

    def test_announcements_are_not_separate_appearances(self):
        first_id = self.store.record(self.observation, 'session-1')
        for _ in range(5):
            self.now += 1
            self.assertEqual(self.store.record(self.observation, 'session-1'), first_id)
        value = self.store.get('AA')
        self.assertEqual(value['appearances'], 1)
        self.assertEqual(value['total_detections'], 6)
        self.assertEqual(self.store.history()['devices'][0]['total_detections'], 6)
        self.assertFalse(value['previously_seen'])
        self.assertEqual(value['first_seen'], '1970-01-01T00:16:40.000+00:00')
        self.assertEqual(value['last_seen'], '1970-01-01T00:16:45.000+00:00')
        self.assertEqual(self.store.connection.execute('SELECT announcements FROM appearances').fetchone()[0], 6)

    def test_return_after_gap_and_new_session(self):
        self.store.record(self.observation, 'first')
        self.now += 29.9
        self.store.record(self.observation, 'first')
        self.assertEqual(self.store.get('AA')['appearances'], 1)
        self.now += 30
        self.store.record(self.observation, 'first')
        self.assertEqual(self.store.get('AA')['appearances'], 2)
        self.now += 1
        self.store.record(self.observation, 'second')
        value = self.store.get('AA')
        self.assertEqual(value['appearances'], 2)
        self.assertEqual(value['total_detections'], 4)
        self.assertTrue(value['previously_seen'])

    def test_addresses_are_not_merged_even_with_same_name_and_data(self):
        first_id = self.store.record(self.observation, 'first')
        second_id = self.store.record(replace(self.observation, address='BB'), 'first')
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.store.history()['total'], 2)

    def test_missing_name_preserves_metadata_and_sql_is_parameterized(self):
        self.store.record(self.observation, 'first')
        self.store.record(Observation('AA', None, -65, 10), 'first')
        value = self.store.get('AA')
        self.assertEqual(value['name'], 'iPhone')
        self.assertEqual(value['device_type'], 'smartphone')
        self.assertEqual(value['service_uuids'], ['180f'])
        self.assertEqual(value['manufacturer_ids'], [76])
        self.assertEqual(value['brand'], 'Apple')
        self.assertEqual(value['brand_confidence'], 'indicatif')
        self.assertNotIn('manufacturer_data', value)
        malicious = "Robert'); DROP TABLE devices;--"
        self.store.record(Observation('BB', malicious, -60, 10), 'first')
        self.assertEqual(self.store.get('BB')['name'], malicious)
        self.assertEqual(self.store.history()['total'], 2)

    def test_old_unknown_sony_entry_is_reclassified_without_losing_history(self):
        self.store.record(replace(self.observation, name='LE_WH-1000XM6'), 'first')
        before = self.store.get('AA')
        with self.store.connection:
            self.store.connection.execute("UPDATE devices SET device_type='unknown', confidence='inconnu', reason='Ancienne règle'")
        after = self.store.get('AA')
        self.assertEqual(after['device_type'], 'headset')
        self.assertEqual(after['label'], 'Casque audio')
        for field in ['id', 'first_seen', 'last_seen', 'appearances']:
            self.assertEqual(before[field], after[field])
        self.assertEqual(self.store.history()['devices'][0]['label'], 'Casque audio')

    def test_protocol_type_survives_partial_advertisements(self):
        payload = bytes([1, 9, 0x20, 0]) + bytes(23)
        self.store.record(Observation('AA', None, -60, 0, (), {6: payload}), 'first')
        self.store.record(Observation('AA', None, -60, 1), 'first')
        self.assertEqual(self.store.get('AA')['device_type'], 'computer')

    def test_old_oneplus_entry_is_reclassified_without_resetting_statistics(self):
        self.store.record(Observation('AA', 'OnePlus Buds Pro 2', -60, 0), 'test')
        before = self.store.get('AA')
        with self.store.connection:
            self.store.connection.execute("UPDATE devices SET device_type='unknown', confidence='inconnu' WHERE address='AA'")
        after = self.store.get('AA')
        self.assertEqual(after['brand'], 'OnePlus')
        self.assertEqual(after['label'], 'Écouteurs')
        for field in ['id', 'first_seen', 'last_seen', 'appearances', 'total_detections']:
            self.assertEqual(after[field], before[field])

    def test_new_detections_use_first_seen_not_repeated_announcements(self):
        self.store.record(self.observation, 'one')
        self.now += 40
        self.store.record(self.observation, 'two')
        self.store.record(replace(self.observation, address='BB'), 'two')
        recent = self.store.new_detections(15)
        self.assertEqual(recent['total'], 1)
        self.assertEqual(recent['devices'][0]['address'], 'BB')
        self.assertEqual(self.store.get('AA')['appearances'], 2)
        self.now += 1
        self.store.record(replace(self.observation, address='CC'), 'two')
        limited = self.store.new_detections(15, limit=1)
        self.assertEqual(limited['total'], 2)
        self.assertEqual(limited['devices'][0]['address'], 'CC')
        self.now += 16
        self.assertEqual(self.store.new_detections(15)['total'], 0)
        self.assertEqual(self.store.history()['total'], 3)

    def test_pagination_and_validation(self):
        for index in range(3):
            self.now += 1
            self.store.record(replace(self.observation, address=str(index)), 'first')
        page = self.store.history(limit=1, offset=1)
        self.assertEqual(page['total'], 3)
        self.assertEqual(page['devices'][0]['address'], '1')
        for limit, offset in [(0, 0), (501, 0), (True, 0), (1, -1)]:
            with self.assertRaises(ValueError):
                self.store.history(limit, offset)

    def test_disk_reopen_preserves_internal_id_and_first_seen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.sqlite3'
            with_store = Storage(path, clock=lambda: self.now)
            try:
                first_id = with_store.record(self.observation, 'first')
                first_seen = with_store.get('AA')['first_seen']
            finally:
                with_store.close()
            reopened = Storage(path, clock=lambda: self.now + 20)
            try:
                self.assertEqual(reopened.record(self.observation, 'second'), first_id)
                value = reopened.get('AA')
                self.assertEqual(value['appearances'], 1)
                self.assertEqual(value['total_detections'], 2)
                self.assertEqual(value['brand'], 'Apple')
                self.assertEqual(value['first_seen'], first_seen)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(b'not-persisted', path.read_bytes())
            finally:
                reopened.close()

"""Pas d’inférence de fabricant sur adresses BLE aléatoires ou non typées."""

from dataclasses import replace
import unittest

from bluetooth.address import describe_address, brand_from_vendor
from bluetooth.scanner import Observation
from database.storage import Storage


class AddressTests(unittest.TestCase):
    def test_public_apple_prefix_without_inventing_hardware_type(self):
        info = describe_address('F0:EE:7A:00:00:01', 'public')
        self.assertEqual(info['vendor'], 'Apple, Inc.')
        self.assertEqual(brand_from_vendor(info['vendor']), 'Apple')
        store = Storage(':memory:')
        try:
            item = Observation('F0:EE:7A:00:00:01', None, -60, 0, address_type='public')
            store.record(item, 'test')
            value = store.get(item.address)
            self.assertEqual(value['brand'], 'Apple')
            self.assertEqual(value['device_type'], 'unknown')
            self.assertEqual(value['address_info']['type'], 'public')
            store.record(replace(item, address_type=None), 'test')
            self.assertEqual(store.history()['devices'][0]['address_info']['type'], 'public')
            store.record(replace(item, address_type='random'), 'test')
            value = store.get(item.address)
            self.assertIsNone(value['address_info']['vendor'])
            self.assertEqual(value['brand'], 'Marque inconnue')
        finally:
            store.close()

    def test_random_or_unknown_never_uses_ieee_prefix(self):
        for address_type in ['random', None, 'invalid']:
            info = describe_address('F0:EE:7A:00:00:01', address_type)
            self.assertIsNone(info['vendor'])
        self.assertEqual(describe_address('40:00:00:00:00:01', 'random')['label'], 'Privée résolvable')
        self.assertEqual(describe_address('C0:00:00:00:00:01', 'random')['label'], 'Aléatoire statique')
        self.assertEqual(describe_address('00:00:00:00:00:01', 'random')['label'], 'Privée non résolvable')

    def test_unknown_prefix_invalid_identifier_and_interface_vendor(self):
        self.assertIsNone(describe_address('FF:FF:FF:00:00:01', 'public')['vendor'])
        self.assertEqual(describe_address('not-a-mac', 'public')['type'], 'unknown')
        self.assertIsNone(brand_from_vendor('Intel Corporate'))

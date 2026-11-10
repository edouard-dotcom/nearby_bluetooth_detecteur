"""Préfixes IEEE des adresses Bluetooth explicitement publiques uniquement."""

from functools import lru_cache
import json
from pathlib import Path
import re


@lru_cache(maxsize=1)
def vendor_prefixes():
    return json.loads((Path(__file__).with_name('data') / 'oui.json').read_text())["prefixes"]


def describe_address(address, address_type=None):
    if not isinstance(address, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
        return {"type": "unknown", "label": "Identifiant non MAC", "vendor": None,
                "reason": "Cet identifiant ne permet pas une recherche de préfixe MAC."}
    if address_type == "random":
        bits = int(address[:2], 16) >> 6
        label = {0: "Privée non résolvable", 1: "Privée résolvable", 3: "Aléatoire statique"}.get(bits, "Aléatoire")
        return {"type": "random", "label": label, "vendor": None,
                "reason": "Adresse aléatoire déclarée par BlueZ : aucun fabricant déduit du préfixe. Elle ne permet pas de reconnaître un appareil physique après changement d’adresse."}
    if address_type != "public":
        return {"type": "unknown", "label": "Type d’adresse inconnu", "vendor": None,
                "reason": "Le système n’a pas indiqué si l’adresse est publique ou aléatoire. Aucun fabricant déduit du préfixe."}
    vendor = vendor_prefixes().get(address.replace(':', '').upper()[:6])
    return {"type": "public", "label": "Publique", "vendor": vendor,
            "reason": (f"Préfixe MA-L du registre IEEE : {vendor}. Organisation enregistrée pour l’interface ; ne distingue pas un téléphone d’un ordinateur." if vendor else
                       "Adresse publique ; préfixe non présent dans l’extrait local du registre IEEE MA-L. Aucun modèle ni type déduit.")}


def brand_from_vendor(vendor):
    if not vendor:
        return None
    for pattern, brand in [(r'\bapple\b', 'Apple'), (r'\bsamsung\b', 'Samsung'),
                           (r'\blenovo\b', 'Lenovo'), (r'\bdell\b', 'Dell'),
                           (r'hewlett|\bhp inc', 'HP'), (r'asustek', 'ASUS'),
                           (r'\bacer\b', 'Acer'), (r'\bsony\b', 'Sony'),
                           (r'\bmicrosoft\b', 'Microsoft'), (r'\bgoogle\b', 'Google'),
                           (r'\bhuawei\b', 'Huawei'), (r'\bxiaomi\b', 'Xiaomi')]:
        if re.search(pattern, vendor, re.IGNORECASE):
            return brand
    return None

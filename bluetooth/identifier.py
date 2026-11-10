"""Classification indicative, sans requête distante ni connexion à l’appareil."""

from dataclasses import dataclass
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bluetooth.scanner import Observation


@dataclass(frozen=True)
class Identification:
    device_type: str
    label: str
    confidence: str
    reason: str


def normalize_name(name):
    value = unicodedata.normalize("NFKC", name or "")
    for dash in "‐‑‒–—−":
        value = value.replace(dash, "-")
    return re.sub(r"\s+", " ", value.replace("_", " ")).strip()


# Noms explicites seulement. Un nom reste modifiable et n’est jamais une preuve.
RULES = (
    ("headset", "Casque audio", r"\b(?:casque|headset|headphones|airpods\s*max|wh-1000xm\d)\b"),
    ("smartwatch", "Montre connectée", r"\b(?:apple\s*watch|galaxy\s*watch|pixel\s*watch|smartwatch|amazfit|huawei\s*watch|garmin\s+(?:venu|fenix|forerunner)|fitbit\s+(?:versa|sense))\b"),
    ("headphones", "Écouteurs", r"\b(?:airpods(?!\s*max)|one\s*plus\s+buds|redmi\s*buds|galaxy\s*buds|pixel\s*buds|earbuds|écouteurs|ecouteurs|wf-1000xm\d)\b"),
    ("computer", "Ordinateur", r"\b(?:macbook|imac|thinkpad|laptop|ordinateur)\b|\b(?:desktop|pc)[ -][\w-]+"),
    ("tablet", "Tablette", r"\b(?:ipad|galaxy\s*tab|pixel\s*tablet|one\s*plus\s*pad)\b"),
    ("phone", "Téléphone", r"\b(?:téléphone|telephone|phone)\b"),
    ("smartphone", "Smartphone", r"\b(?:iphone|smartphone)\b|\bone\s*plus\s+\d+[rt]?\b|\bgalaxy\s+z\s*(?:fold|flip)\s*\d+\b|\bpixel\s+[0-9]+\w*\b|\bgalaxy\s+(?:s|a|z|note)\s*[0-9]+\w*\b"),
)
LABELS = {kind: label for kind, label, _ in RULES} | {"unknown": "Appareil inconnu", "tablet": "Tablette", "console": "Console de jeux"}


def identify(observation: "Observation") -> Identification:
    name = normalize_name(observation.name)
    matches = [(kind, label) for kind, label, pattern in RULES
               if re.search(pattern, name, flags=re.IGNORECASE)]
    protocol = microsoft_device_type(observation.manufacturer_data.get(6, b""))
    if protocol:
        matches.append((protocol, LABELS[protocol]))
    appearance = getattr(observation, 'appearance', None)
    appearance_kind = appearance_device_type(appearance)
    if appearance_kind:
        matches.append((appearance_kind, LABELS[appearance_kind]))
    if any(kind == 'smartphone' for kind, _ in matches):
        matches = [(kind, label) for kind, label in matches if kind != 'phone']
    matches = list(dict.fromkeys(matches))
    clues = []
    if observation.service_uuids:
        clues.append("Services annoncés : " + ", ".join(observation.service_uuids))
    if observation.manufacturer_data:
        clues.append("Données fabricant : " + ", ".join(
            f"0x{key:04X} ({len(value)} octets)"
            for key, value in sorted(observation.manufacturer_data.items())))
    if len(matches) == 1:
        kind, label = matches[0]
        reason = (f"Type indicatif selon le champ Appearance Bluetooth 0x{appearance:04X}." if appearance_kind else
                  "Type indicatif selon le champ appareil de la balise Microsoft CDP." if protocol else
                  "Type probable selon le nom annoncé ; ce nom peut être modifié.")
        if clues:
            reason += " " + "; ".join(clues)
        return Identification(kind, label, "indicatif", reason)
    reason = ("Indices de type contradictoires." if len(matches) > 1 else
              "Aucun indice de type suffisamment explicite.")
    if clues:
        reason += " " + "; ".join(clues) + ". Ces informations seules ne prouvent pas un type."
    return Identification("unknown", LABELS["unknown"], "inconnu", reason)


# Sous-ensemble vérifié du registre Bluetooth SIG Company Identifiers.
# https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/company_identifiers/company_identifiers.yaml
COMPANY_BRANDS = {
    0x0006: "Microsoft",
    0x004C: "Apple",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x012D: "Sony",
    0x018E: "Google",
    0x02C5: "Lenovo",
}
BRAND_NAMES = (
    ("OnePlus", r"\bone\s*plus\b"),
    ("Apple", r"\b(?:apple|iphone|ipad|ipod|airpods|macbook|imac)\b"),
    ("Samsung", r"\b(?:samsung|galaxy)\b"),
    ("Google", r"\b(?:google|pixel)\b"),
    ("Sony", r"\bsony\b|\b(?:wh|wf)-1000xm\d\b"),
    ("Lenovo", r"\b(?:lenovo|thinkpad)\b"),
    ("Microsoft", r"\b(?:microsoft|surface)\b"),
    ("Bose", r"\bbose\b"),
    ("JBL", r"\bjbl\b"),
    ("Garmin", r"\bgarmin\b"),
    ("Xiaomi", r"\b(?:xiaomi|redmi)\b"),
    ("Huawei", r"\bhuawei\b"),
    ("Amazfit", r"\bamazfit\b"),
)


@dataclass(frozen=True)
class BrandIdentification:
    brand: str
    confidence: str
    reason: str


def identify_brand(name: str | None, manufacturer_ids=()) -> BrandIdentification:
    """Marque indicative ; un identifiant de société n’atteste pas le produit final."""
    candidates = set()
    has_name_clue = False
    evidence = []
    for brand, pattern in BRAND_NAMES:
        if re.search(pattern, normalize_name(name), flags=re.IGNORECASE):
            candidates.add(brand)
            has_name_clue = True
            evidence.append(f"Nom annoncé compatible avec {brand}")
    for company_id in sorted(set(manufacturer_ids)):
        if company_id in COMPANY_BRANDS:
            brand = COMPANY_BRANDS[company_id]
            candidates.add(brand)
            evidence.append(f"Identifiant de société Bluetooth 0x{company_id:04X} : {brand}")
    if len(candidates) == 1:
        return BrandIdentification(candidates.pop(), "indicatif" if has_name_clue else "format", "; ".join(evidence) +
                                   ". Indice de marque uniquement, sans preuve du fabricant du produit ni de son type.")
    if candidates:
        return BrandIdentification("Marque inconnue", "inconnu", "Indices contradictoires : " + "; ".join(evidence))
    ids = ", ".join(f"0x{value:04X}" for value in sorted(set(manufacturer_ids)))
    return BrandIdentification("Marque inconnue", "inconnu",
                               "Aucun indice de marque reconnu." + (f" IDs non répertoriés localement : {ids}." if ids else ""))


def microsoft_device_type(payload: bytes) -> str | None:
    """Champ Device Type MS-CDP ; aucune déduction depuis le seul ID Microsoft.

    Formats v0/v1 documentés, scénario Bluetooth, en-tête et longueur contrôlés.
    Ne lit ni ne conserve le sel ou l’empreinte d’identification de la balise.
    https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cdp/77b446d0-8cea-4821-ad21-fabdf4d9a569
    """
    if len(payload) not in (24, 27) or payload[0] != 1 or payload[2] not in (0x20, 0x21):
        return None
    code = payload[1]
    if code >> 5 == 1:
        sku = code & 0x1F
    elif code >> 6 == 0:
        sku = code & 0x3F
    else:
        return None
    return {1: "console", 6: "smartphone", 7: "tablet", 9: "computer",
            11: "smartphone", 15: "computer", 16: "tablet"}.get(sku)


def appearance_device_type(value):
    """Valeurs Appearance Bluetooth SIG ; aucune lecture GATT ni appairage."""
    if type(value) is not int or not 0 <= value <= 65535:
        return None
    if value == 0x0087:
        return 'tablet'
    if value >> 6 == 1:
        return 'phone'
    if value >> 6 == 2:
        return 'computer'
    if value >> 6 == 3:
        return 'smartwatch'
    return {0x0941: 'headphones', 0x0942: 'headset', 0x0943: 'headset',
            0x0945: 'headphones', 0x0946: 'headphones'}.get(value)

"""Scanner BLE asynchrone, utilisable seul avec python -m bluetooth.scanner."""

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import math
import sys
from threading import Event
import time

from bleak import BleakScanner


@dataclass(frozen=True)
class Observation:
    address: str
    name: str | None
    rssi: int | None
    last_seen: float
    service_uuids: tuple[str, ...] = ()
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    address_type: str | None = None
    appearance: int | None = None


def positive_seconds(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("La durée doit être positive et finie.")
    return result


async def scan(
    on_observation: Callable[[Observation], None],
    stop: Event,
    on_started: Callable[[], None] = lambda: None,
    duration: float | None = None,
    scanner_factory=BleakScanner,
) -> None:
    """Transmet seulement les annonces reçues pendant cette session.

    Le callback s'exécute dans la boucle asyncio du scanner. Les consommateurs
    Qt doivent transmettre les observations via un signal entre les threads.
    """
    def received(device, advertisement):
        if stop.is_set() or not device.address:
            return
        # Adaptation BlueZ pour Bleak 3.1 : ne jamais deviner ce type depuis les bits d’une MAC.
        details = getattr(device, "details", None)
        props = details.get("props", {}) if isinstance(details, dict) else {}
        props = props if isinstance(props, dict) else {}
        address_type = props.get("AddressType")
        if address_type not in ("public", "random"):
            address_type = None
        on_observation(Observation(
            address=device.address.upper(),
            name=advertisement.local_name or device.name or None,
            rssi=advertisement.rssi,
            last_seen=time.monotonic(),
            service_uuids=tuple(sorted({uuid.lower() for uuid in advertisement.service_uuids})),
            manufacturer_data={key: bytes(value) for key, value in advertisement.manufacturer_data.items()},
            address_type=address_type,
            appearance=props.get("Appearance") if type(props.get("Appearance")) is int else None,
        ))

    if stop.is_set():
        return
    async with scanner_factory(detection_callback=received):
        on_started()
        deadline = None if duration is None else time.monotonic() + duration
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Affiche les annonces Bluetooth BLE.")
    parser.add_argument("--duration", type=positive_seconds, help="Durée en secondes ; sinon Ctrl+C.")
    args = parser.parse_args()

    def display(observation):
        rssi = "inconnu" if observation.rssi is None else f"{observation.rssi} dBm"
        from bluetooth.identifier import identify
        identity = identify(observation)
        print(f"{observation.address} | {observation.name or 'Nom inconnu'} | RSSI {rssi} "
              f"| Type : {identity.label} ({identity.confidence}) "
              f"| Services : {', '.join(observation.service_uuids) or 'aucun annoncé'} "
              f"| Fabricants : {', '.join(f'0x{key:04X}' for key in observation.manufacturer_data) or 'inconnus'}", flush=True)

    try:
        asyncio.run(scan(display, Event(), lambda: print("Détection BLE en cours…", flush=True), args.duration))
    except KeyboardInterrupt:
        print("\nDétection arrêtée.")
    except Exception as exc:
        print(f"Détection impossible : {exc}. Vérifiez l’adaptateur, BlueZ et les droits Bluetooth.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

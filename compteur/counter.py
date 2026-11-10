"""Ensemble des identifiants BLE récemment observés, sans historique persistant."""

from dataclasses import replace
import math
import time
from collections.abc import Callable

from bluetooth.scanner import Observation


class DeviceCounter:
    """À utiliser dans un seul thread ; expiration à l'âge de window secondes."""

    def __init__(self, window: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 min_rssi: int | None = None, retention: float = 300.0):
        if not math.isfinite(window) or window <= 0:
            raise ValueError("La fenêtre doit être positive et finie.")
        if not math.isfinite(retention) or retention < window:
            raise ValueError("La conservation doit être finie et au moins égale à la présence.")
        self.retention = retention
        self._first_seen: dict[str, float] = {}
        self.min_rssi = min_rssi
        self.window = window
        self.clock = clock
        self._devices: dict[str, Observation] = {}
        self._next_prune = 0.0

    def _expire(self, now: float, force=True) -> None:
        if not force and now < self._next_prune:
            return
        self._next_prune = now + 1
        self._devices = {key: value for key, value in self._devices.items()
                         if now - value.last_seen < self.retention}
        self._first_seen = {key: value for key, value in self._first_seen.items() if key in self._devices}

    def observe(self, observation: Observation) -> bool:
        now = self.clock()
        self._expire(now, force=False)
        address = observation.address.strip().upper()
        if not address or now - observation.last_seen >= self.window:
            return False
        previous = self._devices.get(address)
        if previous is not None and observation.last_seen < previous.last_seen:
            return False
        new_presence = previous is None or observation.last_seen - previous.last_seen >= self.window
        if new_presence:
            self._first_seen[address] = observation.last_seen
        name = observation.name or (previous.name if previous else None)
        services = tuple(sorted(set(observation.service_uuids) |
                                set(previous.service_uuids if previous else ())))
        manufacturers = dict(previous.manufacturer_data) if previous else {}
        manufacturers.update(observation.manufacturer_data)
        self._devices[address] = replace(observation, address=address, name=name,
                                         service_uuids=services, manufacturer_data=manufacturers)
        return new_presence

    def snapshot(self) -> list[Observation]:
        now = self.clock()
        self._expire(now)
        visible = (value for value in self._devices.values()
                   if now - value.last_seen < self.window and
                   (self.min_rssi is None or
                    (value.rssi is not None and value.rssi >= self.min_rssi)))
        return sorted(visible, key=lambda value: value.address)

    def metrics(self) -> dict:
        now = self.clock()
        self._expire(now)
        active = sum(now - item.last_seen < self.window and
                     (self.min_rssi is None or (item.rssi is not None and item.rssi >= self.min_rssi))
                     for item in self._devices.values())
        expired = sum(now - item.last_seen >= self.window for item in self._devices.values())
        return dict(active_addresses=active, expired_addresses=expired,
                    temporary_addresses=len(self._devices),
                    filtered_addresses=len(self._devices) - active - expired)

    def recent_observations(self, age_limit=30):
        now = self.clock()
        self._expire(now)
        return sorted((item for item in self._devices.values() if now - item.last_seen < age_limit),
                      key=lambda item: item.last_seen, reverse=True)

    def temporary_addresses(self) -> frozenset:
        return frozenset(self._devices)

    def presence_times(self, address) -> dict:
        item = self._devices[address]
        return {"first_seen_age": max(0, self.clock() - self._first_seen[address]),
                "last_seen_age": max(0, self.clock() - item.last_seen)}

    def clear(self) -> None:
        self._devices.clear()
        self._first_seen.clear()
        self._next_prune = 0.0

    def __len__(self) -> int:
        return len(self.snapshot())

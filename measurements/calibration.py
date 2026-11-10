"""Calibration RSSI sur une seule adresse choisie volontairement."""

from collections import deque
import math
import statistics
import time
from uuid import uuid4


class Calibration:
    distances = (1, 3, 5, 10)

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.profile_id = str(uuid4())
        self.address = None
        self.results = {}
        self.current = None
        self.last_state = 'idle'
        self.completed = deque()

    def start(self, address, distance, duration=10):
        self.tick()
        if self.current:
            raise ValueError('Une mesure est déjà en cours.')
        if not isinstance(address, str) or not address.strip() or len(address) > 100:
            raise ValueError('Choisissez une adresse observée.')
        if type(distance) is not int or distance not in self.distances:
            raise ValueError('Distance attendue : 1, 3, 5 ou 10 mètres.')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 5 <= duration <= 120:
            raise ValueError('Durée de mesure : entre 5 et 120 secondes.')
        address = address.strip().upper()
        if self.address and self.address != address:
            raise ValueError('Réinitialisez la calibration avant de changer d’adresse.')
        self.address = address
        self.current = dict(distance=distance, duration=duration, started=self.clock(), values=deque(maxlen=5000))
        self.last_state = 'collecting'

    def observe(self, observation):
        self.tick()
        if (self.current and observation.address.strip().upper() == self.address and
                type(observation.rssi) is int and -127 <= observation.rssi <= 0):
            self.current['values'].append(observation.rssi)

    def tick(self):
        if not self.current or self.clock() - self.current['started'] < self.current['duration']:
            return
        current = self.current
        values = list(current['values'])
        result = dict(distance=current['distance'], duration=current['duration'], samples=len(values),
                      mean=round(statistics.mean(values), 2) if values else None,
                      median=statistics.median(values) if values else None,
                      stdev=round(statistics.pstdev(values), 2) if values else None,
                      minimum=min(values) if values else None, maximum=max(values) if values else None)
        self.results[current['distance']] = result
        self.completed.append(dict(profile_id=self.profile_id, **result))
        self.current = None
        self.last_state = 'complete' if len(values) >= 5 else 'insufficient_data'

    def reset(self):
        self.current = None
        self.address = None
        self.results = {}
        self.profile_id = str(uuid4())
        self.last_state = 'idle'

    def snapshot(self):
        self.tick()
        complete = all(self.results.get(distance, {}).get('samples', 0) >= 5 for distance in self.distances)
        recommendation = max(-127, min(0, round(self.results[10]['median']) - 3)) if complete else None
        return dict(state=self.last_state, address=self.address,
                    remaining_seconds=round(max(0, self.current['duration'] - (self.clock() - self.current['started'])), 1) if self.current else 0,
                    distance=self.current['distance'] if self.current else None,
                    results=[self.results[key] for key in sorted(self.results)],
                    recommended_threshold=recommendation,
                    warning='Une seule adresse est suivie ; si elle change, la mesure peut manquer de données. Les quatre distances et au moins 5 annonces par point sont nécessaires. Seuil proposé : médiane à 10 m moins 3 dBm, sans frontière de distance garantie.')

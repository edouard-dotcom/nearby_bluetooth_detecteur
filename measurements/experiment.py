"""Comparaison contrôlée : adresses actives versus appareils de référence connus."""

import math
import statistics
import time
from uuid import uuid4


class Experiment:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.current = None
        self.last_result = None
        self.completed = []

    def start(self, reference_count, duration, config, controlled):
        if self.current:
            raise ValueError('Une expérience est déjà en cours.')
        if controlled is not True:
            raise ValueError('Confirmez que les appareils de référence et l’environnement de test sont connus.')
        if type(reference_count) is not int or not 0 <= reference_count <= 10000:
            raise ValueError('Le nombre de référence doit être un entier de 0 à 10000.')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 10 <= duration <= 3600:
            raise ValueError('Durée d’expérience : 10 à 3600 secondes.')
        self.current = dict(id=str(uuid4()), reference_count=reference_count, duration=duration,
                            started=self.clock(), config=dict(config), samples=[], last_sample=None,
                            addresses=set(), observations=0)
        return self.current['id']

    def observe(self, observation):
        current = self.current
        if current and self.clock() - current['started'] < current['duration']:
            if observation.rssi is not None and observation.rssi >= current['config']['min_rssi']:
                current['addresses'].add(observation.address.strip().upper())
                current['observations'] += 1

    def tick(self, active_count):
        current = self.current
        if not current:
            return
        elapsed = self.clock() - current['started']
        if elapsed < current['duration'] and (current['last_sample'] is None or self.clock() - current['last_sample'] >= 1):
            current['samples'].append(active_count)
            current['last_sample'] = self.clock()
        if elapsed >= current['duration']:
            self.finish('complete')

    def finish(self, state='cancelled'):
        current = self.current
        if not current:
            return
        values = current['samples']
        average = statistics.mean(values) if values else None
        reference = current['reference_count']
        self.last_result = dict(id=current['id'], state=state, reference_count=reference,
                                requested_duration=current['duration'], elapsed_seconds=round(self.clock() - current['started'], 2),
                                sample_count=len(values), mean_active=round(average, 3) if average is not None else None,
                                maximum_active=max(values) if values else None, minimum_active=min(values) if values else None,
                                relative_error_percent=round(abs(average - reference) / reference * 100, 2) if reference > 0 and average is not None else None,
                                absolute_error=round(abs(average - reference), 3) if average is not None else None,
                                distinct_addresses=len(current['addresses']), observations=current['observations'],
                                config=current['config'], controlled=True,
                                interpretation='Écart entre adresses actives moyennes et nombre d’appareils de référence. Ni précision d’identification des appareils ni comptage de personnes.')
        self.completed.append(self.last_result)
        self.current = None

    def snapshot(self):
        current = self.current
        return dict(state='running' if current else 'idle',
                    remaining_seconds=round(max(0, current['duration'] - (self.clock() - current['started'])), 1) if current else 0,
                    sample_count=len(current['samples']) if current else 0,
                    last_result=self.last_result)

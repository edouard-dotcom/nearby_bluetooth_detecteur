"""Alertes globales : état du scanner, silence radio et activité."""

from collections import deque
import math


class AlertMonitor:
    def __init__(self):
        self.activity_threshold = 30
        self.silence_seconds = 30.0
        self.events = deque(maxlen=100)
        self.current = []
        self.previous_codes = set()

    def configure(self, activity_threshold, silence_seconds):
        if type(activity_threshold) is not int or not 1 <= activity_threshold <= 10000:
            raise ValueError('Seuil d’activité : entier de 1 à 10000.')
        if isinstance(silence_seconds, bool) or not isinstance(silence_seconds, (int, float)) or not math.isfinite(silence_seconds) or not 5 <= silence_seconds <= 3600:
            raise ValueError('Délai de silence : 5 à 3600 secondes.')
        self.activity_threshold = activity_threshold
        self.silence_seconds = silence_seconds

    def update(self, state, error, active, now, timestamp, scan_started, last_packet):
        alerts = []
        if error:
            alerts.append(dict(code='scanner_error', level='error', message=f'Erreur du scanner : {error}'))
        if state == 'scanning':
            last = last_packet if last_packet is not None else scan_started
            if last is not None and now - last >= self.silence_seconds:
                alerts.append(dict(code='radio_silence', level='warning', message=f'Aucune annonce reçue depuis au moins {self.silence_seconds:g} s. Vérifier l’environnement et l’adaptateur ; cela ne prouve pas une panne.'))
            if active >= self.activity_threshold:
                alerts.append(dict(code='high_activity', level='info', message=f'Activité élevée : {active} adresses actives, seuil {self.activity_threshold}.'))
        codes = {item['code'] for item in alerts}
        for alert in alerts:
            if alert['code'] not in self.previous_codes:
                self.events.appendleft(dict(timestamp=timestamp, resolved=False, **alert))
        for code in self.previous_codes - codes:
            self.events.appendleft(dict(timestamp=timestamp, code=code, level='info', resolved=True, message='Condition revenue à la normale.'))
        self.previous_codes = codes
        self.current = alerts
        return dict(current=alerts, events=list(self.events)[:10],
                    activity_threshold=self.activity_threshold, silence_seconds=self.silence_seconds)

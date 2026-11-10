"""Coordination des mesures ; appelé sous le verrou du service web."""

from dataclasses import asdict
from .calibration import Calibration
from .series import LiveSeries
from .experiment import Experiment
from .alerts import AlertMonitor


class MeasurementController:
    def __init__(self, service):
        self.service = service
        self.clock = lambda: service.counter.clock()
        self.calibration = Calibration(self.clock)
        self.series = LiveSeries()
        self.experiment = Experiment(self.clock)
        self.alerts = AlertMonitor()
        self.scan_started = None
        self.last_packet = None
        self.estimation_source = None
        self.analytics_cache = None
        self.last_alerts = dict(current=[], events=[], activity_threshold=30, silence_seconds=30)

    def observe(self, observation):
        self.last_packet = self.clock()
        self.calibration.observe(observation)
        self.experiment.observe(observation)

    def tick(self):
        service = self.service
        now = self.clock()
        timestamp = service.storage.clock()
        devices = service.counter.snapshot()
        if service.state != 'scanning':
            if self.calibration.current:
                self.calibration.current = None
                self.calibration.last_state = 'cancelled'
            self.experiment.finish('cancelled')
        self.calibration.tick()
        self.experiment.tick(len(devices))
        point = self.series.sample(now, timestamp, devices, service.state == 'scanning')
        if point is not None and point['active'] is not None:
            service.storage.save_minute_sample(point, service.config)
        while self.calibration.completed:
            service.storage.save_calibration(self.calibration.completed.popleft())
        while self.experiment.completed:
            service.storage.save_experiment(self.experiment.completed.pop(0))
        self.last_alerts = self.alerts.update(service.state, service.error, len(devices), now,
                                             timestamp, self.scan_started, self.last_packet)

    def analytics(self):
        config = self.service.config
        key = (config.min_rssi, config.presence_seconds)
        now = self.clock()
        if self.analytics_cache is None or self.analytics_cache[0] != key or now - self.analytics_cache[1] >= 10:
            self.analytics_cache = (key, now, self.service.storage.statistics(config))
        return self.analytics_cache[2]

    def estimate(self, count):
        source = self.estimation_source
        config = self.service.config
        if source is None or any(source['config'][key] != getattr(config, key) for key in ['min_rssi', 'presence_seconds']):
            return dict(value=None, source=None, label='Non calibré')
        ratio = source['mean_active'] / source['reference_count']
        return dict(value=round(count / ratio, 1), source=source['id'], label='Estimation expérimentale, pas un comptage de personnes')

    def action(self, name, payload):
        service = self.service
        if name == 'calibration-start':
            if service.state != 'scanning':
                raise ValueError('Démarrez le scanner avant une calibration.')
            address = payload.get('address', '').strip().upper() if isinstance(payload.get('address'), str) else ''
            if address not in service.counter.temporary_addresses():
                raise ValueError('Choisissez une adresse récemment observée.')
            self.calibration.start(address, payload.get('distance'), payload.get('duration', 10))
        elif name == 'calibration-reset':
            self.calibration.reset()
        elif name == 'calibration-apply':
            threshold = self.calibration.snapshot()['recommended_threshold']
            if threshold is None:
                raise ValueError('Mesurez les quatre distances avec au moins 5 annonces par point.')
            service.set_threshold(threshold)
        elif name == 'experiment-start':
            if service.state != 'scanning':
                raise ValueError('Démarrez le scanner avant une expérience.')
            self.experiment.start(payload.get('reference_count'), payload.get('duration', 60),
                                  asdict(service.config), payload.get('controlled'))
            self.experiment.tick(len(service.counter.snapshot()))
        elif name == 'experiment-cancel':
            self.experiment.finish('cancelled')
        elif name == 'estimate-use':
            result = next((r for r in service.storage.experiments(1000) if r['id'] == payload.get('id')), None)
            if not result or result['state'] != 'complete' or not result['sample_count'] or result['reference_count'] <= 0 or not result['mean_active']:
                raise ValueError('Choisissez une expérience complète avec référence et moyenne strictement positives.')
            if any(result['config'][key] != getattr(service.config, key) for key in ['min_rssi', 'presence_seconds']):
                raise ValueError('Les réglages RSSI et présence doivent correspondre à ceux de l’essai.')
            self.estimation_source = result
        elif name == 'estimate-clear':
            self.estimation_source = None
        elif name == 'alerts-config':
            self.alerts.configure(payload.get('activity_threshold'), payload.get('silence_seconds'))
        else:
            raise ValueError('Action de mesure inconnue.')
        self.tick()

    def snapshot(self, count):
        return dict(calibration=self.calibration.snapshot(), series=self.series.snapshot(),
                    candidates=[dict(address=item.address, name=item.name, rssi=item.rssi)
                                for item in self.service.counter.recent_observations()],
                    experiment=self.experiment.snapshot(), experiments=self.service.storage.experiments(),
                    saved_calibrations=self.service.storage.calibrations(), analytics=self.analytics(),
                    alerts=self.last_alerts, estimate=self.estimate(count))

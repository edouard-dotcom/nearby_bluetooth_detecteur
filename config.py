"""Paramètres de présence partagés par les interfaces web et Qt."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DetectorConfig:
    min_rssi: int = -80
    presence_seconds: float = 5.0
    retention_seconds: float = 300.0
    refresh_seconds: float = 1.0
    period_seconds: float = 300.0

    def __post_init__(self):
        if type(self.min_rssi) is not int or not -127 <= self.min_rssi <= 0:
            raise ValueError("Le RSSI doit être un entier entre −127 et 0 dBm.")
        for name in ['presence_seconds', 'retention_seconds', 'refresh_seconds', 'period_seconds']:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 86400:
                raise ValueError(f"{name} doit être une durée positive et finie, au plus 86400 s.")
        if self.retention_seconds < self.presence_seconds:
            raise ValueError("La conservation temporaire doit être au moins égale au délai de présence.")
        if not 0.5 <= self.refresh_seconds <= 60:
            raise ValueError("L’actualisation doit être comprise entre 0,5 et 60 secondes.")


def add_config_arguments(parser):
    for flag, kind, default in [('rssi', int, -80), ('presence-seconds', float, 5),
                                 ('retention-seconds', float, 300), ('refresh-seconds', float, 1),
                                 ('period-seconds', float, 300)]:
        parser.add_argument('--' + flag, type=kind, default=default)


def config_from_args(args):
    return DetectorConfig(args.rssi, args.presence_seconds, args.retention_seconds,
                          args.refresh_seconds, args.period_seconds)

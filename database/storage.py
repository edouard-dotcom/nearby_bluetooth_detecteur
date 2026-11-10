"""SQLite local : identités observables et périodes de présence, sans fusion d’adresses."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

from bluetooth.identifier import identify, identify_brand, LABELS
from bluetooth.scanner import Observation
from bluetooth.address import describe_address, brand_from_vendor


def default_database_path():
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return root / "nearby_radio_detecteur" / "history.sqlite3"


def utc_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, path=None, gap_seconds=30.0, clock=time.time):
        if not math.isfinite(gap_seconds) or gap_seconds <= 0:
            raise ValueError("Le délai d’apparition doit être positif et fini.")
        self.path = str(default_database_path() if path is None else path)
        if self.path != ":memory:":
            destination = Path(self.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Création privée, sans écraser une base existante.
            descriptor = os.open(destination, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
        self.clock = clock
        self.gap_seconds = gap_seconds
        self.lock = RLock()
        self._last_statistics_cleanup = None
        self.connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    address TEXT NOT NULL UNIQUE,
                    name TEXT,
                    device_type TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    service_uuids TEXT NOT NULL,
                    manufacturer_ids TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS address_metadata (
                    address TEXT PRIMARY KEY,
                    address_type TEXT NOT NULL CHECK(address_type IN ('public', 'random'))
                );
                CREATE TABLE IF NOT EXISTS appearance_metadata (
                    address TEXT PRIMARY KEY,
                    appearance INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS appearances (
                    id INTEGER PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(id),
                    session_id TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    announcements INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minute_statistics (
                    minute INTEGER NOT NULL, rssi_threshold INTEGER NOT NULL, presence_seconds REAL NOT NULL,
                    samples INTEGER NOT NULL, active_sum INTEGER NOT NULL, active_max INTEGER NOT NULL,
                    rssi_sum REAL NOT NULL, rssi_samples INTEGER NOT NULL,
                    PRIMARY KEY(minute, rssi_threshold, presence_seconds)
                );
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id TEXT PRIMARY KEY, saved_at REAL NOT NULL, result TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calibration_results (
                    profile_id TEXT NOT NULL, distance INTEGER NOT NULL, saved_at REAL NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(profile_id, distance)
                );
                CREATE INDEX IF NOT EXISTS appearances_device ON appearances(device_id, id);
                CREATE INDEX IF NOT EXISTS appearances_started ON appearances(first_seen);
                CREATE INDEX IF NOT EXISTS devices_first_seen ON devices(first_seen DESC);
                CREATE INDEX IF NOT EXISTS devices_recent ON devices(last_seen DESC);
            """)

    def record(self, observation, session_id):
        """Chaque annonce prolonge une apparition, sans en créer une à chaque callback."""
        address = observation.address.strip().upper()
        if not address:
            raise ValueError("Identifiant observable vide.")
        now = self.clock()  # UTC persisté ; jamais l’horloge monotone du scanner.
        with self.lock, self.connection:
            if observation.address_type in ("public", "random"):
                self.connection.execute(
                    "INSERT INTO address_metadata VALUES (?, ?) ON CONFLICT(address) DO UPDATE SET address_type=excluded.address_type",
                    (address, observation.address_type))
            if type(observation.appearance) is int and 0 <= observation.appearance <= 65535:
                self.connection.execute(
                    "INSERT INTO appearance_metadata VALUES (?, ?) ON CONFLICT(address) DO UPDATE SET appearance=excluded.appearance",
                    (address, observation.appearance))
            old = self.connection.execute("SELECT * FROM devices WHERE address=?", (address,)).fetchone()
            effective = replace(observation, name=observation.name or (old["name"] if old else None))
            identity = identify(effective)
            if (old and identity.device_type == "unknown" and not observation.name
                    and observation.appearance is None and old["device_type"] != "unknown"):
                from bluetooth.identifier import Identification
                identity = Identification(old["device_type"], LABELS[old["device_type"]],
                                          old["confidence"], old["reason"])
            device_id = old["id"] if old else str(uuid4())
            # Préserve les services/IDs déjà annoncés lorsque l’annonce suivante est partielle.
            services = sorted(set(observation.service_uuids) | set(json.loads(old["service_uuids"]) if old else []))
            manufacturers = sorted(set(observation.manufacturer_data) | set(json.loads(old["manufacturer_ids"]) if old else []))
            self.connection.execute("""
                INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    name=excluded.name, device_type=excluded.device_type,
                    confidence=excluded.confidence, reason=excluded.reason,
                    service_uuids=excluded.service_uuids, manufacturer_ids=excluded.manufacturer_ids,
                    last_seen=MAX(devices.last_seen, excluded.last_seen)
                """, (device_id, address, effective.name, identity.device_type, identity.confidence,
                      identity.reason, json.dumps(services), json.dumps(manufacturers), now, now))
            previous = self.connection.execute(
                "SELECT * FROM appearances WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
            if previous is None or now - previous["last_seen"] >= self.gap_seconds:
                self.connection.execute(
                    "INSERT INTO appearances(device_id, session_id, first_seen, last_seen, announcements) VALUES (?, ?, ?, ?, 1)",
                    (device_id, session_id, now, now))
            else:
                self.connection.execute(
                    "UPDATE appearances SET last_seen=MAX(last_seen, ?), announcements=announcements+1 WHERE id=?",
                    (now, previous["id"]))
        return device_id

    @staticmethod
    def _device(row):
        value = dict(row)
        value["first_seen"] = utc_timestamp(value["first_seen"])
        value["last_seen"] = utc_timestamp(value["last_seen"])
        value["service_uuids"] = json.loads(value["service_uuids"])
        value["manufacturer_ids"] = json.loads(value["manufacturer_ids"])
        # Réévalue les anciens « inconnus » avec les règles actuelles, sans
        # modifier leurs identifiants, dates ni compteurs de présence.
        identity = identify(Observation(value["address"], value["name"], None, 0,
                                        tuple(value["service_uuids"]), appearance=value.get("appearance")))
        if identity.device_type != "unknown":
            value["device_type"] = identity.device_type
            value["confidence"] = identity.confidence
            value["reason"] = identity.reason
        value["label"] = LABELS[value["device_type"]]
        brand = identify_brand(value["name"], value["manufacturer_ids"])
        address_info = describe_address(value["address"], value["address_type"])
        value["address_info"] = address_info
        oui_brand = brand_from_vendor(address_info["vendor"])
        if brand.confidence == "inconnu" and brand.reason.startswith("Aucun indice") and oui_brand:
            from bluetooth.identifier import BrandIdentification
            brand = BrandIdentification(oui_brand, "indicatif", address_info["reason"])
        value["brand"] = brand.brand
        value["brand_confidence"] = brand.confidence
        value["brand_reason"] = brand.reason
        value["previously_seen"] = value["appearances"] > 1
        return value

    def get(self, address):
        with self.lock:
            row = self.connection.execute("""
                SELECT d.*, (SELECT COUNT(*) FROM appearances a WHERE a.device_id=d.id) AS appearances,
                    (SELECT COALESCE(SUM(a.announcements), 0) FROM appearances a WHERE a.device_id=d.id) AS total_detections,
                    (SELECT m.address_type FROM address_metadata m WHERE m.address=d.address) AS address_type,
                    (SELECT a.appearance FROM appearance_metadata a WHERE a.address=d.address) AS appearance
                FROM devices d WHERE address=?
                """, (address.strip().upper(),)).fetchone()
            return None if row is None else self._device(row)

    def history(self, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise ValueError("Pagination invalide.")
        with self.lock:
            rows = self.connection.execute("""
                SELECT d.*, (SELECT COUNT(*) FROM appearances a WHERE a.device_id=d.id) AS appearances,
                    (SELECT COALESCE(SUM(a.announcements), 0) FROM appearances a WHERE a.device_id=d.id) AS total_detections,
                    (SELECT m.address_type FROM address_metadata m WHERE m.address=d.address) AS address_type,
                    (SELECT a.appearance FROM appearance_metadata a WHERE a.address=d.address) AS appearance
                FROM devices d ORDER BY d.last_seen DESC, d.address LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()
            total = self.connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            return {"devices": [self._device(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def new_detections(self, period_seconds=300, limit=100):
        if not math.isfinite(period_seconds) or period_seconds <= 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Période ou limite invalide.")
        now = self.clock()
        since = now - period_seconds
        with self.lock:
            rows = self.connection.execute("""
                SELECT d.*, (SELECT COUNT(*) FROM appearances a WHERE a.device_id=d.id) AS appearances,
                    (SELECT COALESCE(SUM(a.announcements), 0) FROM appearances a WHERE a.device_id=d.id) AS total_detections,
                    (SELECT m.address_type FROM address_metadata m WHERE m.address=d.address) AS address_type,
                    (SELECT a.appearance FROM appearance_metadata a WHERE a.address=d.address) AS appearance
                FROM devices d WHERE d.first_seen>=? AND d.first_seen<=?
                ORDER BY d.first_seen DESC, d.address LIMIT ?
                """, (since, now, limit)).fetchall()
            total = self.connection.execute(
                "SELECT COUNT(*) FROM devices WHERE first_seen>=? AND first_seen<=?", (since, now)).fetchone()[0]
            return {"devices": [self._device(row) for row in rows], "total": total,
                    "limit": limit, "since": utc_timestamp(since), "until": utc_timestamp(now),
                    "period_seconds": period_seconds}

    def summary(self, period_seconds=300):
        if not math.isfinite(period_seconds) or period_seconds <= 0:
            raise ValueError("La période doit être positive et finie.")
        now = self.clock()
        with self.lock:
            count = self.connection.execute(
                "SELECT COUNT(*) FROM appearances WHERE first_seen>=? AND first_seen<=?",
                (now - period_seconds, now)).fetchone()[0]
            return {"sessions_observed": count, "period_seconds": period_seconds}

    def save_minute_sample(self, point, config):
        if point['active'] is None:
            return
        minute = int(point['timestamp'] // 60) * 60
        with self.lock, self.connection:
            self.connection.execute("""
                INSERT INTO minute_statistics VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(minute, rssi_threshold, presence_seconds) DO UPDATE SET
                    samples=samples+1, active_sum=active_sum+excluded.active_sum,
                    active_max=MAX(active_max, excluded.active_max), rssi_sum=rssi_sum+excluded.rssi_sum,
                    rssi_samples=rssi_samples+excluded.rssi_samples
                """, (minute, config.min_rssi, config.presence_seconds, point['active'], point['active'],
                      point['rssi_sum'], point['rssi_count']))
            now = self.clock()
            if self._last_statistics_cleanup is None or now - self._last_statistics_cleanup >= 3600:
                self.connection.execute('DELETE FROM minute_statistics WHERE minute<?', (now - 30 * 86400,))
                self._last_statistics_cleanup = now

    def statistics(self, config, hours=24):
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError('Période statistique : 1 à 168 heures.')
        with self.lock:
            rows = self.connection.execute("""
                SELECT * FROM minute_statistics
                WHERE minute>=? AND rssi_threshold=? AND presence_seconds=? ORDER BY minute
                """, (self.clock() - hours * 3600, config.min_rssi, config.presence_seconds)).fetchall()
        points = []
        hourly = {}
        total_samples = 0
        total_active = 0
        maximum = None
        for row in rows:
            row = dict(row)
            points.append(dict(timestamp=row['minute'], samples=row['samples'],
                               mean_active=round(row['active_sum'] / row['samples'], 2), maximum=row['active_max'],
                               mean_rssi=round(row['rssi_sum'] / row['rssi_samples'], 1) if row['rssi_samples'] else None))
            hour = row['minute'] // 3600 * 3600
            item = hourly.setdefault(hour, dict(timestamp=hour, samples=0, active_sum=0, maximum=0))
            item['samples'] += row['samples']; item['active_sum'] += row['active_sum']
            item['maximum'] = max(item['maximum'], row['active_max'])
            total_samples += row['samples']; total_active += row['active_sum']
            maximum = row['active_max'] if maximum is None else max(maximum, row['active_max'])
        hours_data = [dict(timestamp=v['timestamp'], samples=v['samples'], mean_active=round(v['active_sum']/v['samples'], 2), maximum=v['maximum']) for v in hourly.values()]
        return dict(points=points, hourly=hours_data, samples=total_samples,
                    mean_active=round(total_active / total_samples, 2) if total_samples else None,
                    maximum=maximum, busiest_hour=max(hours_data, key=lambda v: v['mean_active']) if hours_data else None,
                    config=dict(min_rssi=config.min_rssi, presence_seconds=config.presence_seconds),
                    hours=hours, retention_days=30)

    def save_experiment(self, result):
        with self.lock, self.connection:
            self.connection.execute('INSERT OR REPLACE INTO experiment_results VALUES (?, ?, ?)',
                                    (result['id'], self.clock(), json.dumps(result, ensure_ascii=False)))
            self.connection.execute('DELETE FROM experiment_results WHERE id NOT IN (SELECT id FROM experiment_results ORDER BY saved_at DESC LIMIT 1000)')

    def experiments(self, limit=10):
        with self.lock:
            rows = self.connection.execute('SELECT saved_at, result FROM experiment_results ORDER BY saved_at DESC, id DESC LIMIT ?', (limit,)).fetchall()
            return [dict(json.loads(row['result']), saved_at=utc_timestamp(row['saved_at'])) for row in rows]

    def save_calibration(self, result):
        with self.lock, self.connection:
            self.connection.execute('INSERT OR REPLACE INTO calibration_results VALUES (?, ?, ?, ?)',
                                    (result['profile_id'], result['distance'], self.clock(), json.dumps(result, ensure_ascii=False)))
            self.connection.execute('DELETE FROM calibration_results WHERE profile_id NOT IN (SELECT profile_id FROM calibration_results GROUP BY profile_id ORDER BY MAX(saved_at) DESC LIMIT 100)')

    def calibrations(self, limit=20):
        with self.lock:
            rows = self.connection.execute('SELECT saved_at, result FROM calibration_results ORDER BY saved_at DESC LIMIT ?', (limit,)).fetchall()
            return [dict(json.loads(row['result']), saved_at=utc_timestamp(row['saved_at'])) for row in rows]

    def get_many(self, addresses):
        values = sorted(set(address.strip().upper() for address in addresses))
        result = {}
        with self.lock:
            for offset in range(0, len(values), 400):
                chunk = values[offset:offset+400]
                placeholders = ','.join('?' for _ in chunk)
                rows = self.connection.execute(f"""
                    SELECT d.*, (SELECT COUNT(*) FROM appearances a WHERE a.device_id=d.id) AS appearances,
                        (SELECT COALESCE(SUM(a.announcements),0) FROM appearances a WHERE a.device_id=d.id) AS total_detections,
                        (SELECT m.address_type FROM address_metadata m WHERE m.address=d.address) AS address_type,
                        (SELECT a.appearance FROM appearance_metadata a WHERE a.address=d.address) AS appearance
                    FROM devices d WHERE address IN ({placeholders})
                    """, chunk).fetchall()
                for row in rows:
                    result[row['address']] = self._device(row)
        return result

    def close(self):
        with self.lock:
            self.connection.close()

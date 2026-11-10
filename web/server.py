"""Serveur HTTP local : python -m web.server (sans dépendance web supplémentaire)."""

import argparse
from dataclasses import asdict, replace
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import statistics
from threading import Event, RLock, Thread
import time
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from bluetooth.scanner import scan
from compteur.counter import DeviceCounter
from database.storage import Storage
from config import DetectorConfig, add_config_arguments, config_from_args
from measurements.controller import MeasurementController


class DetectorService:
    def __init__(self, scan_function=scan, storage_path=None, config=None):
        self.config = config if config is not None else DetectorConfig()
        self.storage = Storage(storage_path, gap_seconds=self.config.presence_seconds)
        self.session_id = None
        self.scan_function = scan_function
        self.counter = DeviceCounter(window=self.config.presence_seconds, min_rssi=self.config.min_rssi,
                                     retention=self.config.retention_seconds)
        self.lock = RLock()
        self.stop_event = Event()
        self.thread = None
        self.state = "stopped"
        self.error = None
        self.recent_arrivals = {}
        self.arrival_sequence = 0
        self.announced_presences = set()
        self.measurements = MeasurementController(self)

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.counter.clear()
            self.recent_arrivals.clear()
            self.announced_presences.clear()
            self.session_id = str(uuid4())
            self.error = None
            self.stop_event = Event()
            self.state = "starting"
            self.measurements.scan_started = None
            self.measurements.last_packet = None
            self.thread = Thread(target=self._run, daemon=True, name="ble-web-scanner")
            self.thread.start()

    def _run(self):
        def received(observation):
            with self.lock:
                if not self.stop_event.is_set():
                    try:
                        self.observe(observation)
                    except sqlite3.Error as exc:
                        self.error = f"Historique SQLite : {exc}"
                        self.stop_event.set()

        def started():
            with self.lock:
                if not self.stop_event.is_set():
                    self.state = "scanning"
                    self.measurements.scan_started = self.counter.clock()

        try:
            asyncio.run(self.scan_function(received, self.stop_event, started))
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.state = "error" if self.error else "stopped"

    def stop(self):
        with self.lock:
            self.measurements.experiment.finish("cancelled")
            if self.measurements.calibration.current:
                self.measurements.calibration.current = None
                self.measurements.calibration.last_state = "cancelled"
            if self.thread is not None and self.thread.is_alive():
                self.state = "stopping"
                self.stop_event.set()

    def set_threshold(self, value):
        if type(value) is not int or not -127 <= value <= 0:
            raise ValueError("Le seuil doit être un entier entre −127 et 0 dBm.")
        with self.lock:
            if self.measurements.experiment.current:
                raise ValueError("Terminez ou annulez l’expérience avant de modifier les paramètres.")
            self.counter.min_rssi = value
            self.config = replace(self.config, min_rssi=value)

    def configure(self, payload):
        allowed = {"presence_seconds", "retention_seconds", "refresh_seconds", "period_seconds"}
        if not payload or not set(payload) <= allowed:
            raise ValueError("Paramètres de présence inconnus ou absents.")
        with self.lock:
            if self.measurements.experiment.current:
                raise ValueError("Terminez ou annulez l’expérience avant de modifier les paramètres.")
            updated = replace(self.config, **payload)
            self.config = updated
            self.counter.window = updated.presence_seconds
            self.counter.retention = updated.retention_seconds
            self.storage.gap_seconds = updated.presence_seconds
            self.counter.metrics()

    def observe(self, observation):
        """À appeler sous self.lock ; une alerte par nouvelle présence qualifiante."""
        self.storage.record(observation, self.session_id)
        new_presence = self.counter.observe(observation)
        self.measurements.observe(observation)
        address = observation.address.strip().upper()
        if new_presence:
            self.announced_presences.discard(address)
        if (address not in self.announced_presences and observation.rssi is not None and
                observation.rssi >= self.counter.min_rssi):
            self.announced_presences.add(address)
            self.arrival_sequence += 1
            self.recent_arrivals[observation.address.strip().upper()] = {
                "event_id": self.arrival_sequence, "started": observation.last_seen}
        self._prune_arrivals()

    def _prune_arrivals(self):
        self.announced_presences.intersection_update(self.counter.temporary_addresses())
        now = self.counter.clock()
        self.recent_arrivals = {address: item for address, item in self.recent_arrivals.items()
                                if now - item["started"] < 10}

    def arrival_snapshot(self, identities=None):
        self._prune_arrivals()
        now = self.counter.clock()
        return [{"event_id": item["event_id"], "address": address,
                 "identity": identities.get(address) if identities is not None else self.storage.get(address),
                 "remaining_seconds": max(0, 10 - (now - item["started"]))}
                for address, item in sorted(self.recent_arrivals.items(),
                                             key=lambda entry: entry[1]["started"], reverse=True)]

    def maintenance(self):
        with self.lock:
            self.counter.metrics()
            self._prune_arrivals()
            try:
                self.measurements.tick()
            except sqlite3.Error as exc:
                self.error = f"Statistiques SQLite : {exc}"
                self.stop_event.set()
                self.state = "error"

    def snapshot(self):
        with self.lock:
            now = self.counter.clock()
            active = self.counter.snapshot()
            identities = self.storage.get_many({item.address for item in active} | set(self.recent_arrivals))
            devices = [dict(address=item.address, name=item.name, rssi=item.rssi,
                            age=round(max(0, now - item.last_seen), 1),
                            service_uuids=item.service_uuids,
                            manufacturer_ids=sorted(item.manufacturer_data),
                            identity=identities.get(item.address),
                            presence=self.counter.presence_times(item.address))
                       for item in active]
            return dict(state=self.state, error=self.error, count=len(devices),
                        threshold=self.counter.min_rssi, window=self.counter.window,
                        devices=devices, metrics=self.counter.metrics(),
                        sessions=self.storage.summary(self.config.period_seconds),
                        new_detections=self.storage.new_detections(self.config.period_seconds),
                        recent_arrivals=self.arrival_snapshot(identities),
                        mean_rssi=round(statistics.mean(d.rssi for d in active if d.rssi is not None), 1) if active else None,
                        measurements=self.measurements.snapshot(len(devices)),
                        config=asdict(self.config))

    def history(self, limit=100, offset=0):
        with self.lock:
            return self.storage.history(limit, offset)

    def close(self):
        self.stop()
        if self.thread is not None:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Le scanner n’a pas terminé son arrêt après 10 secondes.")
        self.measurements.tick()
        self.storage.close()


STATIC = Path(__file__).with_name("static")


class DetectorServer(ThreadingHTTPServer):
    daemon_threads = True

    def service_actions(self):
        self.service.maintenance()

    def __init__(self, port=8765, service=None):
        self.service = service if service is not None else DetectorService()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.authority}"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass  # Ne pas journaliser les observations ou le jeton local.

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        # Bloque les origines externes et les noms DNS pointant vers loopback.
        if self.headers.get("Host") != self.server.authority:
            self.reply(403, {"error": "Hôte non autorisé. Utilisez l’adresse 127.0.0.1 affichée."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self.reply(403, {"error": "Origine non autorisée."})
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        if self.path == "/api/status":
            self.reply(200, self.server.service.snapshot())
        elif urlsplit(self.path).path == "/api/history":
            try:
                query = parse_qs(urlsplit(self.path).query)
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self.reply(200, self.server.service.history(limit, offset))
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
        elif self.path == "/api/session":
            self.reply(200, {"token": self.server.token})
        elif self.path in ("/", "/app.js", "/style.css", "/lab.js"):
            filename, content_type = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/lab.js": ("lab.js", "text/javascript; charset=utf-8"),
            }[self.path]
            self.reply(200, (STATIC / filename).read_bytes(), content_type)
        else:
            self.reply(404, {"error": "Page inconnue."})

    def do_POST(self):
        if not self.trusted():
            return
        token = self.headers.get("X-Detector-Token", "")
        if (self.headers.get("Origin") != self.server.origin or
                not secrets.compare_digest(token, self.server.token)):
            self.reply(403, {"error": "Session non autorisée. Rechargez la page."})
            return
        if self.path not in ("/api/start", "/api/stop", "/api/threshold", "/api/config") and not self.path.startswith("/api/lab/"):
            self.reply(404, {"error": "Action inconnue."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("Requête JSON invalide.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Objet JSON attendu.")
            if self.path.startswith("/api/lab/"):
                with self.server.service.lock:
                    self.server.service.measurements.action(self.path.removeprefix("/api/lab/"), payload)
            elif self.path == "/api/start":
                self.server.service.start()
            elif self.path == "/api/stop":
                self.server.service.stop()
            elif self.path == "/api/config":
                self.server.service.configure(payload)
            else:
                self.server.service.set_threshold(payload.get("threshold"))
        except (ValueError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})
            return
        self.reply(200, self.server.service.snapshot())


def main():
    parser = argparse.ArgumentParser(description="Interface web locale du détecteur BLE.")
    parser.add_argument("--port", type=int, default=8765)
    add_config_arguments(parser)
    args = parser.parse_args()
    try:
        config = config_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.port <= 65535:
        parser.error("Le port doit être compris entre 1 et 65535.")
    with DetectorServer(args.port, DetectorService(config=config)) as server:
        print(f"Détecteur Bluetooth : {server.origin} — Ctrl+C pour arrêter.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.service.close()


if __name__ == "__main__":
    main()

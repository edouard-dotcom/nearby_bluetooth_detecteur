"""Interface graphique et adaptation du scanner asyncio vers les signaux Qt."""

import asyncio
import sqlite3
from dataclasses import replace
from uuid import uuid4
from threading import Event

from PySide6.QtCore import QThread, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from bluetooth.scanner import Observation, scan
from compteur.counter import DeviceCounter
from database.storage import Storage
from config import DetectorConfig


class ScanWorker(QThread):
    observation = Signal(object)
    scanning = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = Event()

    def run(self):
        try:
            asyncio.run(scan(self.observation.emit, self.stop_event, self.scanning.emit))
        except Exception as exc:
            self.failed.emit(str(exc))

    def stop(self):
        self.stop_event.set()


class DetectorWindow(QWidget):
    def __init__(self, worker_factory=ScanWorker, counter=None, storage_path=None, config=None):
        super().__init__()
        self.config = config if config is not None else DetectorConfig()
        self.storage = Storage(storage_path, gap_seconds=self.config.presence_seconds)
        self.storage_closed = False
        self.session_id = None
        self.worker_factory = worker_factory
        self.counter = counter if counter is not None else DeviceCounter(window=self.config.presence_seconds, min_rssi=self.config.min_rssi, retention=self.config.retention_seconds)
        self.worker = None
        self.closing = False
        self.error = False
        self.setWindowTitle("Détecteur Bluetooth BLE")
        self.resize(1100, 580)
        layout = QVBoxLayout(self)
        title = QLabel(f"Adresses BLE actives — moins de {self.counter.window:g} secondes")
        self.title_label = title
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.count_label = QLabel("0")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet("font-size: 56px; font-weight: bold;")
        layout.addWidget(self.count_label)
        metrics_row = QHBoxLayout()
        self.sessions_label = QLabel("Sessions observées : 0")
        self.expired_label = QLabel("Adresses expirées : 0")
        self.period_select = QComboBox()
        for text, value in [("5 minutes", 300), ("15 minutes", 900), ("1 heure", 3600)]:
            self.period_select.addItem(text, value)
        if self.period_select.findData(self.config.period_seconds) < 0:
            self.period_select.addItem(f"{self.config.period_seconds:g} s", self.config.period_seconds)
        self.period_select.setCurrentIndex(self.period_select.findData(self.config.period_seconds))
        metrics_row.addWidget(self.sessions_label)
        metrics_row.addWidget(self.period_select)
        metrics_row.addWidget(self.expired_label)
        layout.addLayout(metrics_row)
        self.status = QLabel("Détection arrêtée")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        range_note = QLabel("Portée visée : environ 10 m — filtre RSSI à calibrer sur place.")
        range_note.setWordWrap(True)
        layout.addWidget(range_note)
        range_controls = QHBoxLayout()
        range_controls.addWidget(QLabel("Signal minimum :"))
        self.rssi_threshold = QSpinBox()
        self.rssi_threshold.setRange(-127, 0)
        self.rssi_threshold.setSuffix(" dBm")
        if self.counter.min_rssi is None:
            self.counter.min_rssi = self.config.min_rssi
        self.rssi_threshold.setValue(self.counter.min_rssi)
        self.rssi_threshold.setToolTip(
            "Valeur plus proche de 0 : filtre plus strict. "
            "−80 dBm est un point de départ, sans garantie de distance. "
            "Les signaux sans RSSI sont exclus."
        )
        range_controls.addWidget(self.rssi_threshold)
        layout.addLayout(range_controls)
        self.rssi_threshold.valueChanged.connect(self.set_threshold)
        settings_row = QHBoxLayout()
        self.presence_input = QDoubleSpinBox()
        self.retention_input = QDoubleSpinBox()
        self.refresh_input = QDoubleSpinBox()
        for widget, label, value, maximum in [
                (self.presence_input, "Présence", self.counter.window, 86400),
                (self.retention_input, "Mémoire temporaire", self.counter.retention, 86400),
                (self.refresh_input, "Actualisation", self.config.refresh_seconds, 60)]:
            widget.setRange(0.5, maximum)
            widget.setSuffix(" s")
            widget.setValue(value)
            settings_row.addWidget(QLabel(label))
            settings_row.addWidget(widget)
        apply_settings = QPushButton("Appliquer les délais")
        apply_settings.clicked.connect(self.apply_presence_settings)
        settings_row.addWidget(apply_settings)
        layout.addLayout(settings_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Nom", "Adresse observable", "RSSI", "Type probable", "ID interne", "Apparitions", "Reconnaissance"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.table, "Présence actuelle")
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ["Nom", "Type probable", "ID interne", "Adresse observable", "Première détection UTC", "Dernière détection UTC", "Apparitions"])
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabs.addTab(self.history_table, "Historique local (100 derniers identifiants)")
        layout.addWidget(self.tabs)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Démarrer")
        self.stop_button = QPushButton("Arrêter")
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        note = QLabel(f"Présence : {self.counter.window:g} dernières secondes. Les identifiants BLE ne garantissent pas un nombre exact d’appareils physiques.")
        self.presence_note = note
        note.setWordWrap(True)
        layout.addWidget(note)
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.timer = QTimer(self)
        self.timer.setInterval(round(self.config.refresh_seconds * 1000))
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.period_select.currentIndexChanged.connect(self.change_period)

    @Slot()
    def start_scan(self):
        if self.worker is not None or self.closing:
            return
        self.counter.clear()
        self.session_id = str(uuid4())
        self.refresh()
        self.error = False
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Démarrage…")
        self.worker = self.worker_factory(self)
        self.worker.observation.connect(self.receive)
        self.worker.scanning.connect(self.scan_started)
        self.worker.failed.connect(self.scan_failed)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    @Slot(object)
    def receive(self, observation: Observation):
        if self.worker is not None and not self.worker.stop_event.is_set() and not self.error:
            try:
                self.storage.record(observation, self.session_id)
                self.counter.observe(observation)
            except sqlite3.Error as exc:
                self.stop_scan()
                self.scan_failed(f"Historique SQLite : {exc}")

    @Slot()
    def scan_started(self):
        if self.worker is not None and not self.worker.stop_event.is_set():
            self.status.setText("● Détection en cours")

    @Slot()
    def stop_scan(self):
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("Arrêt en cours…")

    @Slot(str)
    def scan_failed(self, message):
        self.error = True
        self.status.setText(f"Erreur Bluetooth : {message}. Vérifiez l’adaptateur, BlueZ et les droits Bluetooth.")

    @Slot()
    def scan_finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if not self.error:
            self.status.setText("Détection arrêtée — les observations expirent automatiquement")
        self.refresh()
        if self.closing:
            self.close()

    @Slot(int)
    def set_threshold(self, value):
        self.counter.min_rssi = value
        self.config = replace(self.config, min_rssi=value)
        self.refresh()

    @Slot()
    def apply_presence_settings(self):
        try:
            updated = replace(self.config, presence_seconds=self.presence_input.value(),
                              retention_seconds=self.retention_input.value(),
                              refresh_seconds=self.refresh_input.value())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.config = updated
        self.counter.window = updated.presence_seconds
        self.counter.retention = updated.retention_seconds
        self.storage.gap_seconds = updated.presence_seconds
        self.timer.setInterval(round(updated.refresh_seconds * 1000))
        self.title_label.setText(f"Adresses BLE actives — moins de {updated.presence_seconds:g} secondes")
        self.presence_note.setText(f"Présence : {updated.presence_seconds:g} s ; mémoire temporaire : {updated.retention_seconds:g} s. Les identifiants ne garantissent pas un nombre exact d’appareils physiques.")
        self.refresh()

    @Slot(int)
    def change_period(self, index):
        self.config = replace(self.config, period_seconds=self.period_select.itemData(index))
        self.refresh()

    @Slot()
    def refresh(self):
        devices = self.counter.snapshot()
        self.count_label.setText(str(len(devices)))
        metrics = self.counter.metrics()
        sessions = self.storage.summary(self.config.period_seconds)
        self.sessions_label.setText(f"Sessions observées : {sessions['sessions_observed']}")
        self.expired_label.setText(f"Adresses expirées : {metrics['expired_addresses']}")
        self.expired_label.setToolTip(f"Adresses sans annonce depuis {self.counter.window:g} s, conservées jusqu’à {self.counter.retention:g} s après la dernière annonce. Hors filtre RSSI.")
        self.sessions_label.setToolTip("Sessions de toutes les annonces reçues, commencées pendant la période sélectionnée. Le filtre RSSI s’applique aux adresses actives.")
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            identity = self.storage.get(device.address)
            values = (device.name or (identity["name"] if identity else None) or "Nom inconnu", device.address,
                      "Inconnu" if device.rssi is None else f"{device.rssi} dBm",
                      identity["label"] + " (" + identity["confidence"] + ")" if identity else "Appareil inconnu",
                      identity["id"][:8] if identity else "—",
                      str(identity["appearances"]) if identity else "—",
                      ("Déjà observé" if identity["previously_seen"] else "Première apparition") if identity else "—")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if identity:
                    item.setToolTip(f"{identity['reason']}\nID : {identity['id']}\nPremière : {identity['first_seen']}\nDernière : {identity['last_seen']}\nMême identifiant observable uniquement.")
                self.table.setItem(row, column, item)
        history = self.storage.history()
        self.tabs.setTabText(1, f"Historique local ({history['total']} identifiants, 100 derniers)")
        self.history_table.setRowCount(len(history["devices"]))
        for row, device in enumerate(history["devices"]):
            values = (device["name"] or "Nom inconnu", device["label"] + " (" + device["confidence"] + ")",
                      device["id"][:8], device["address"], device["first_seen"], device["last_seen"], str(device["appearances"]))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{device['reason']}\nID : {device['id']}\nMême identifiant observable uniquement ; historique hors filtre RSSI.")
                self.history_table.setItem(row, column, item)

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.start_button.setEnabled(False)
            self.stop_scan()
            event.ignore()
        else:
            self.timer.stop()
            if not self.storage_closed:
                self.storage.close()
                self.storage_closed = True
            event.accept()

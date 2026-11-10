"""QA navigateur V2 synthétique, exécution optionnelle : python tests/browser_v2.py."""

import asyncio
from pathlib import Path
import sys
from threading import Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from bluetooth.scanner import Observation
from web.server import DetectorServer, DetectorService

now = [time.monotonic()]
base = now[0]
wall = time.time()
rssi = [-60]
emit = [True]

async def simulated(callback, stop, started):
    started()
    while not stop.is_set():
        if emit[0]:
            callback(Observation('AA', 'iPhone de test', rssi[0], now[0], appearance=0x40))
            callback(Observation('BB', 'OnePlus Buds Pro 2', -60, now[0]))
        await asyncio.sleep(0.08)

service = DetectorService(simulated, storage_path=':memory:')
service.counter.clock = lambda: now[0]
service.storage.clock = lambda: wall + now[0] - base
server = DetectorServer(0, service)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(server.origin)
        assert page.evaluate('getComputedStyle(document.querySelector(".dashboard-layout")).display') == 'grid'
        page.locator('#start').click()
        expect(page.locator('#count')).to_have_text('2')
        expect(page.locator('#mean-rssi')).to_contain_text('dBm')
        expect(page.locator('#estimated-count')).to_have_text('—')
        page.locator('#brand-filter').select_option('Apple')
        expect(page.locator('#devices tr')).to_have_count(1)
        expect(page.locator('#count')).to_have_text('2')
        page.locator('#brand-filter').select_option('')
        expect(page.locator('#devices tr')).to_have_count(2)
        page.locator('#type-filter').select_option('headphones')
        expect(page.locator('#devices tr')).to_have_count(1)
        page.locator('#type-filter').select_option('')
        page.locator('#calibration-address').select_option('AA')
        page.locator('#calibration-duration').fill('5')
        for distance, signal in [(1, -40), (3, -55), (5, -65), (10, -77)]:
            rssi[0] = signal
            page.locator('#calibration-distance').select_option(str(distance))
            expect(page.locator('#calibration-start')).to_be_enabled()
            page.locator('#calibration-start').click()
            expect(page.locator('#calibration-status')).to_contain_text('Mesure à')
            page.wait_for_timeout(700)
            now[0] += 5
            service.maintenance()
            expect(page.locator('#calibration-status')).to_have_text('Point mesuré', timeout=6000)
        expect(page.locator('#calibration-results tr')).to_have_count(4)
        expect(page.locator('#calibration-recommendation')).to_contain_text('-80 dBm')
        page.locator('#calibration-apply').click()
        expect(page.locator('#threshold-value')).to_have_text('-80 dBm')
        page.locator('#experiment-duration').fill('10')
        page.locator('#experiment-reference').fill('2')
        page.locator('#experiment-controlled').check()
        expect(page.locator('#experiment-start')).to_be_enabled()
        page.locator('#experiment-start').click()
        expect(page.locator('#experiment-status')).to_contain_text('En cours')
        expect(page.locator('#threshold')).to_be_disabled()
        for _ in range(10):
            now[0] += 1
            page.wait_for_timeout(120)
            service.maintenance()
        expect(page.locator('#experiment-results tr')).to_have_count(1, timeout=6000)
        expect(page.locator('#experiment-results tr')).to_contain_text('Terminé')
        assert len(service.storage.calibrations()) == 4
        assert service.storage.experiments()[0]['relative_error_percent'] == 0
        page.locator('#estimate-experiment').select_option(service.storage.experiments()[0]['id'])
        page.locator('#estimate-use').click()
        expect(page.locator('#estimated-count')).to_have_text('2')
        page.locator('#alert-threshold').fill('1')
        page.locator('#alert-silence').fill('5')
        page.locator('#alerts-apply').click()
        expect(page.locator('#scanner-alerts')).to_contain_text('Activité élevée')
        emit[0] = False
        now[0] += 6
        service.maintenance()
        expect(page.locator('#scanner-alerts')).to_contain_text('Aucune annonce', timeout=6000)
        emit[0] = True
        page.wait_for_timeout(200)
        now[0] += 1
        service.maintenance()
        expect(page.locator('#scanner-alerts')).not_to_contain_text('Aucune annonce', timeout=6000)
        expect(page.locator('#statistics-hours tr').first).to_be_visible(timeout=6000)
        assert service.measurements.series.points
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path='/tmp/nearby-v2-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/tmp/nearby-v2-mobile.png', full_page=True)
        page.locator('#stop').click()
        expect(page.locator('#start')).to_be_enabled()
        assert not errors, errors
        browser.close()
    print('V2 navigateur : graphique, filtres, 4 calibrations synthétiques, expérience contrôlée, estimation, statistiques, alertes et mobile : OK.')
finally:
    server.shutdown()
    server.server_close()
    service.close()
    thread.join(3)

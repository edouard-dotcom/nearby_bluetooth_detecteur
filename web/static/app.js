"use strict";
const byId = id => document.getElementById(id);
const start = byId("start"), stop = byId("stop"), threshold = byId("threshold");
let token = null, busy = false, connected = false, state = "stopped", lastError = null;
let adjusting = false;
let refreshSeconds = 1, configDirty = false, measurementRunning = false;
let lastDashboard = null;
let historyOffset = 0, historyTotal = 0;
const arrivalAlerts = new Map();
const labels = {starting: "Démarrage…", scanning: "Détection en cours", stopping: "Arrêt en cours…", stopped: "Détection arrêtée", error: "Erreur Bluetooth"};
function buttons() {
  start.disabled = !connected || busy || ["starting", "scanning", "stopping"].includes(state);
  stop.disabled = !connected || busy || !["starting", "scanning"].includes(state);
  threshold.disabled = !connected || busy || measurementRunning;
  byId("apply-config").disabled = !connected || busy || measurementRunning;
  byId("session-period").disabled = !connected || busy || measurementRunning;
}
function notice(message) {
  byId("notice").textContent = message || "";
  byId("notice").hidden = !message;
}
async function request(path, payload) {
  const options = {cache: "no-store", signal: AbortSignal.timeout(5000)};
  if (payload !== undefined) Object.assign(options, {method: "POST", headers: {"Content-Type": "application/json", "X-Detector-Token": token}, body: JSON.stringify(payload)});
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Action impossible.");
  return result;
}
function deviceDisplayName(identity) {
  const brand = identity?.brand_confidence === "format" ? `Signal au format ${identity.brand}` : (identity?.brand || "Marque inconnue");
  const hardware = !identity || identity.device_type === "unknown" ? "type non annoncé" : identity.label;
  return `${brand} · ${hardware}`;
}
function appearanceFrequency(count) {
  if (!Number.isInteger(count) || count < 1) return {className: "frequency-unknown", label: "Non renseignée"};
  if (count <= 2) return {className: "frequency-green", label: "Rare"};
  if (count <= 5) return {className: "frequency-yellow", label: "Occasionnelle"};
  if (count <= 10) return {className: "frequency-orange", label: "Régulière"};
  if (count <= 20) return {className: "frequency-red", label: "Fréquente"};
  return {className: "frequency-purple", label: "Très fréquente"};
}
function appendFrequency(row, appearances) {
  const frequency = appearanceFrequency(appearances);
  row.classList.add(frequency.className);
  const cell = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "frequency-badge";
  badge.textContent = frequency.label;
  badge.title = Number.isInteger(appearances) ? `${appearances} apparition(s), et non un nombre de passages de personnes.` : "Nombre d’apparitions indisponible.";
  cell.append(badge);
  row.append(cell);
}
function appendAddress(cell, address, identity) {
  const text = document.createElement("span");
  text.textContent = address;
  cell.append(text);
  const info = identity?.address_info;
  const note = document.createElement("small");
  note.className = "address-note";
  note.textContent = info ? info.label + (info.vendor ? ` · ${info.vendor}` : "") : "Type d’adresse inconnu";
  cell.append(note);
  cell.title = info?.reason || "Le type public ou aléatoire de cette adresse n’est pas connu.";
}
function isNewDetection(identity, recent) {
  if (!identity?.first_seen || !recent?.since) return false;
  const first = Date.parse(identity.first_seen);
  return first >= Date.parse(recent.since) && first <= Date.parse(recent.until);
}
function renderNewDetections(data) {
  const recent = data.new_detections || {devices: [], total: 0, limit: 100};
  byId("new-count").textContent = recent.total;
  byId("new-list-count").textContent = recent.total;
  byId("new-tab").classList.toggle("has-new", recent.total > 0);
  const period = recent.period_seconds ?? data.config?.period_seconds ?? 300;
  const duration = period % 60 === 0 ? `${period / 60} dernières minutes` : `${period} dernières secondes`;
  byId("new-description").textContent = `Adresses enregistrées pour la première fois pendant les ${duration}. Tous les signaux reçus, même hors filtre RSSI.`;
  const active = new Set(data.devices.map(device => device.identity?.id));
  const rows = document.createDocumentFragment();
  for (const device of recent.devices) {
    const row = document.createElement("tr");
    row.className = "new-detection-row";
    const values = [deviceDisplayName(device), device.address, new Date(device.first_seen).toLocaleString("fr-FR"), active.has(device.id) ? "Active" : "Hors liste active"];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 1) appendAddress(cell, value, device);
      else cell.textContent = value;
      row.append(cell);
    });
    rows.append(row);
  }
  byId("new-devices").replaceChildren(rows);
  byId("new-empty").hidden = recent.total > 0;
  byId("new-limit").textContent = recent.total > recent.devices.length ? `Affichage des ${recent.devices.length} plus récentes sur ${recent.total}. Consultez l’historique pour la liste complète.` : "";
}
function updateArrivalAlerts(arrivals) {
  const now = performance.now();
  for (const arrival of arrivals || []) {
    if (!(arrival.remaining_seconds > 0)) continue;
    for (const [key, existing] of arrivalAlerts) {
      if (existing.address === arrival.address && key !== arrival.event_id) arrivalAlerts.delete(key);
    }
    const previous = arrivalAlerts.get(arrival.event_id);
    arrivalAlerts.set(arrival.event_id, {...arrival,
      expiresAt: Math.min(previous?.expiresAt ?? Infinity, now + arrival.remaining_seconds * 1000)});
  }
  renderArrivalAlerts();
}
function renderArrivalAlerts() {
  const now = performance.now();
  const fragment = document.createDocumentFragment();
  for (const [key, arrival] of [...arrivalAlerts.entries()].sort((a, b) => b[0] - a[0])) {
    if (now >= arrival.expiresAt) { arrivalAlerts.delete(key); continue; }
    const item = document.createElement("li");
    item.className = "arrival-card";
    const name = document.createElement("strong");
    name.textContent = arrival.identity?.name || `${deviceDisplayName(arrival.identity)} · ${arrival.address.slice(-8)}`;
    const kind = document.createElement("span");
    kind.textContent = deviceDisplayName(arrival.identity);
    const address = document.createElement("small");
    address.textContent = arrival.address;
    const countdown = document.createElement("small");
    countdown.className = "arrival-countdown";
    countdown.textContent = `Encore ${Math.ceil((arrival.expiresAt - now) / 1000)} s`;
    item.append(name, kind, address, countdown); fragment.append(item);
  }
  byId("arrival-list").replaceChildren(fragment);
  if (byId("arrival-count").textContent !== String(arrivalAlerts.size)) byId("arrival-count").textContent = arrivalAlerts.size;
  byId("arrival-empty").hidden = arrivalAlerts.size > 0;
}
setInterval(renderArrivalAlerts, 250);
function filterActiveDevices(data) {
  const selectedBrand = byId("brand-filter").value, selectedType = byId("type-filter").value;
  for (const [id, field] of [["brand-filter", "brand"], ["type-filter", "device_type"]]) {
    const select = byId(id), selected = select.value;
    const values = [...new Set(data.devices.map(device => device.identity?.[field] || "unknown"))].sort();
    if (selected && !values.includes(selected)) values.push(selected);
    const fragment = document.createDocumentFragment(), all = document.createElement("option"); all.value = ""; all.textContent = "Tous"; fragment.append(all);
    for (const value of values) { const option = document.createElement("option"); option.value = value; option.textContent = id === "type-filter" ? (data.devices.find(device => device.identity?.device_type === value)?.identity?.label || value) : value; fragment.append(option); }
    select.replaceChildren(fragment); select.value = selected;
  }
  return data.devices.filter(device => (!selectedBrand || device.identity?.brand === selectedBrand) &&
    (!selectedType || device.identity?.device_type === selectedType) &&
    (!byId("only-new-filter").checked || isNewDetection(device.identity, data.new_detections)));
}
for (const id of ["brand-filter", "type-filter", "only-new-filter"]) byId(id).addEventListener("change", () => { if (lastDashboard) render(lastDashboard); });
function render(data) {
  lastDashboard = data;
  measurementRunning = data.measurements?.experiment.state === "running";
  updateArrivalAlerts(data.recent_arrivals);
  state = data.state;
  document.body.classList.toggle("scanning", state === "scanning");
  byId("status").textContent = labels[state] || state;
  byId("status").className = "status" + (state === "scanning" ? " active" : state === "error" ? " error" : "");
  byId("count").textContent = data.count;
  byId("list-count").textContent = data.count;
  const presenceWindow = data.window ?? 5;
  const config = data.config || {presence_seconds: presenceWindow, retention_seconds: 300, refresh_seconds: 1, period_seconds: 300};
  refreshSeconds = config.refresh_seconds;
  byId("presence-duration").textContent = `${presenceWindow} s`;
  byId("live-description").textContent = `Reçues depuis moins de ${presenceWindow} s et correspondant au filtre RSSI.`;
  byId("sessions-count").textContent = data.sessions?.sessions_observed ?? 0;
  byId("expired-count").textContent = data.metrics?.expired_addresses ?? 0;
  byId("expiry-summary").textContent = `Sans annonce depuis ${presenceWindow} s, conservées jusqu’à ${config.retention_seconds} s après leur dernière annonce. Tous les signaux reçus, hors filtre RSSI.`;
  byId("temporary-summary").textContent = `${data.metrics?.temporary_addresses ?? 0} adresses en mémoire temporaire, dont ${data.metrics?.filtered_addresses ?? 0} récentes exclues par le filtre RSSI.`;
  byId("presence-explanation").textContent = `Après ${presenceWindow} s sans annonce, l’adresse quitte le compteur actif ; après ${config.retention_seconds} s elle quitte la mémoire temporaire. Les statistiques agrégées par adresse restent dans SQLite.`;
  const period = byId("session-period");
  if (![...period.options].some(option => Number(option.value) === config.period_seconds)) {
    const option = document.createElement("option"); option.value = config.period_seconds; option.textContent = `${config.period_seconds} dernières secondes`; period.append(option);
  }
  period.value = String(config.period_seconds);
  if (!configDirty) {
    byId("presence-input").value = config.presence_seconds;
    byId("retention-input").value = config.retention_seconds;
    byId("refresh-input").value = config.refresh_seconds;
  }
  byId("presence-window").textContent = `reçues depuis moins de ${presenceWindow} secondes`;
  if (!adjusting) {
    threshold.value = data.threshold;
    byId("threshold-value").textContent = `${data.threshold} dBm`;
  }
  const rows = document.createDocumentFragment();
  const displayed = filterActiveDevices(data);
  byId("list-count").textContent = displayed.length;
  byId("filter-summary").textContent = `${displayed.length} affichées sur ${data.count} adresses actives` ;
  for (const device of displayed) {
    const row = document.createElement("tr");
    const identity = device.identity;
    const recognition = identity ? `${identity.previously_seen ? "Déjà observé" : "Première apparition"} · ${identity.id.slice(0, 8)}` : "—";
    const values = [deviceDisplayName(identity), device.address, recognition, device.rssi === null ? "Inconnu" : `${device.rssi} dBm`, `Il y a ${Math.floor(device.age)} s · expire dans ${Math.max(0, Math.ceil(presenceWindow - device.age))} s`, `${Math.max(0, Math.floor((device.presence?.first_seen_age ?? 0) - (device.presence?.last_seen_age ?? 0)))} s`, String(identity?.total_detections ?? "—"), String(identity?.appearances ?? "—")];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      const element = index === 3 ? document.createElement("span") : cell;
      if (index === 3) { element.className = "signal"; cell.append(element); }
      if (identity && (index === 0 || index === 2)) {
        cell.title = `${identity.name || "Nom Bluetooth inconnu"}\nMarque et type indicatifs.\n${identity.brand_reason}\n${identity.reason}\nID : ${identity.id}\nPremière : ${identity.first_seen}\nDernière : ${identity.last_seen}\nMême identifiant observable uniquement.`;
      }
      if (index === 1) appendAddress(cell, value, identity);
      else element.textContent = value;
      if (index === 0 && isNewDetection(identity, data.new_detections)) {
        const badge = document.createElement("span"); badge.className = "new-device-badge";
        badge.textContent = "Nouveau"; badge.title = "Adresse observée pour la première fois pendant la période sélectionnée.";
        cell.append(badge);
      }
      row.append(cell);
    });
    appendFrequency(row, identity?.appearances);
    rows.append(row);
  }
  byId("devices").replaceChildren(rows);
  renderNewDetections(data);
  byId("empty").hidden = displayed.length > 0;
  const emptyTitle = byId("empty").querySelector("h3"), emptyText = byId("empty").querySelector("p");
  if (state === "scanning") {
    emptyTitle.textContent = "À l’écoute des signaux";
    emptyText.textContent = "Aucun signal récent ne correspond au filtre. Rapprochez un appareil BLE ou élargissez le seuil.";
  } else {
    emptyTitle.textContent = "Prêt à écouter les alentours";
    emptyText.textContent = "Démarrez la détection pour voir les appareils qui diffusent des annonces BLE.";
  }
  byId("updated").textContent = `Actualisé à ${new Date().toLocaleTimeString("fr-FR")}`;
  if (data.error !== lastError) { lastError = data.error; notice(data.error ? `Bluetooth : ${data.error}. Vérifiez l’adaptateur et son activation.` : null); }
  buttons();
  window.renderLab?.(data);
}
async function action(path, payload = {}) {
  if (busy) return;
  busy = true; adjusting = true; buttons(); notice(null);
  try { const data = await request(path, payload); if (path === "/api/config") configDirty = false; render(data); }
  catch (error) { notice(error.message); }
  finally { busy = false; adjusting = false; buttons(); }
}
start.addEventListener("click", () => action("/api/start"));
stop.addEventListener("click", () => action("/api/stop"));
threshold.addEventListener("input", () => { adjusting = true; byId("threshold-value").textContent = `${threshold.value} dBm`; });
threshold.addEventListener("change", () => action("/api/threshold", {threshold: Number(threshold.value)}));
for (const id of ["presence-input", "retention-input", "refresh-input"]) byId(id).addEventListener("input", () => { configDirty = true; });
byId("apply-config").addEventListener("click", () => action("/api/config", {
  presence_seconds: Number(byId("presence-input").value),
  retention_seconds: Number(byId("retention-input").value),
  refresh_seconds: Number(byId("refresh-input").value)
}));
byId("session-period").addEventListener("change", () => action("/api/config", {period_seconds: Number(byId("session-period").value)}));
async function loadHistory() {
  const history = await request(`/api/history?limit=25&offset=${historyOffset}`);
  historyTotal = history.total;
  const rows = document.createDocumentFragment();
  for (const device of history.devices) {
    const row = document.createElement("tr");
    const date = value => new Date(value).toLocaleString("fr-FR");
    const values = [deviceDisplayName(device), device.id.slice(0, 8), device.address, date(device.first_seen), date(device.last_seen), String(device.total_detections ?? "—"), String(device.appearances)];
    for (const [index, value] of values.entries()) {
      const cell = document.createElement("td");
      cell.textContent = value;
      cell.title = `${device.name || "Nom Bluetooth inconnu"}\nMarque et type indicatifs.\n${device.brand_reason}\n${device.reason}\nID : ${device.id}\nServices : ${device.service_uuids.join(", ") || "aucun annoncé"}\nFabricants : ${device.manufacturer_ids.map(id => "0x" + id.toString(16).padStart(4, "0")).join(", ") || "inconnus"}`;
      if (index === 2) { cell.replaceChildren(); appendAddress(cell, value, device); }
      row.append(cell);
    }
    appendFrequency(row, device.appearances);
    rows.append(row);
  }
  byId("history-devices").replaceChildren(rows);
  byId("history-count").textContent = historyTotal;
  byId("history-empty").hidden = historyTotal > 0;
  byId("history-page").textContent = historyTotal ? `${historyOffset + 1}–${Math.min(historyOffset + 25, historyTotal)} sur ${historyTotal}` : "0 identifiant";
  byId("previous-page").disabled = historyOffset === 0;
  byId("next-page").disabled = historyOffset + 25 >= historyTotal;
}
async function showHistory(visible) {
  byId("new-panel").hidden = true;
  byId("new-tab").setAttribute("aria-pressed", "false");
  byId("live-panel").hidden = visible;
  byId("history-panel").hidden = !visible;
  byId("live-tab").classList.toggle("selected", !visible);
  byId("history-tab").classList.toggle("selected", visible);
  byId("live-tab").setAttribute("aria-pressed", String(!visible));
  byId("history-tab").setAttribute("aria-pressed", String(visible));
  if (visible) {
    try { await loadHistory(); } catch (error) { notice(error.message); }
  }
}
byId("new-tab").addEventListener("click", () => {
  if (!byId("new-panel").hidden) { showHistory(false); return; }
  byId("new-panel").hidden = false;
  byId("live-panel").hidden = true;
  byId("history-panel").hidden = true;
  byId("new-tab").setAttribute("aria-pressed", "true");
  for (const id of ["live-tab", "history-tab"]) {
    byId(id).classList.remove("selected"); byId(id).setAttribute("aria-pressed", "false");
  }
  byId("new-panel").scrollIntoView({block: "start"});
});
byId("live-tab").addEventListener("click", () => showHistory(false));
byId("history-tab").addEventListener("click", () => showHistory(true));
let paging = false;
async function changePage(delta) {
  if (paging) return;
  paging = true;
  historyOffset = Math.max(0, historyOffset + delta);
  try { await loadHistory(); } catch (error) { notice(error.message); }
  finally { paging = false; }
}
byId("previous-page").addEventListener("click", () => changePage(-25));
byId("next-page").addEventListener("click", () => changePage(25));
async function poll() {
  try {
    if (!token) {
      const sessionToken = (await request("/api/session")).token;
      if (window.detectorSessionToken && window.detectorSessionToken !== sessionToken) arrivalAlerts.clear();
      window.detectorSessionToken = sessionToken; token = sessionToken;
    }
    const data = await request("/api/status");
    if (!connected) { notice(null); lastError = null; }
    connected = true;
    if (!busy) render(data);
    if (!byId("history-panel").hidden && !paging) await loadHistory();
  } catch (error) {
    connected = false; token = null;
    byId("status").textContent = "Connexion interrompue";
    document.body.classList.remove("scanning");
    byId("updated").textContent = "Données non actualisées";
    notice("Le serveur local est injoignable. Vérifiez qu’il est démarré ; la reconnexion est automatique.");
    buttons();
  } finally { setTimeout(poll, refreshSeconds * 1000); }
}
poll();

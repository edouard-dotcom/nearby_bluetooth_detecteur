"use strict";
(() => {
  let lastData = null, alertDirty = false;
  const el = id => document.getElementById(id);
  for (const id of ["alert-threshold", "alert-silence"]) el(id).addEventListener("input", () => { alertDirty = true; });
  const number = value => value === null || value === undefined ? "—" : String(value);
  const seconds = value => `${Math.ceil(value)} s`;
  function table(id, rows) {
    const fragment = document.createDocumentFragment();
    for (const values of rows) {
      const row = document.createElement("tr");
      for (const value of values) { const cell = document.createElement("td"); cell.textContent = number(value); row.append(cell); }
      fragment.append(row);
    }
    el(id).replaceChildren(fragment);
  }
  function options(id, values) {
    const select = el(id), selected = select.value;
    const first = document.createElement("option"); first.value = ""; first.textContent = id === "calibration-address" ? "Choisir une adresse" : "Choisir un essai complet";
    const fragment = document.createDocumentFragment(); fragment.append(first);
    for (const [value, text] of values) { const option = document.createElement("option"); option.value = value; option.textContent = text; fragment.append(option); }
    if (selected && !values.some(item => item[0] === selected)) { const old = document.createElement("option"); old.value = selected; old.textContent = `${selected} (non observé récemment)`; fragment.append(old); }
    select.replaceChildren(fragment); select.value = selected;
  }
  function drawChart(points, canvasId = "activity-chart", summaryId = "chart-summary") {
    const canvas = el(canvasId), width = Math.max(250, canvas.getBoundingClientRect().width), height = 220;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = height * ratio;
    const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    ctx.font = "11px system-ui"; ctx.fillStyle = "#71847b";
    const valid = points.filter(point => point.active !== null);
    if (!valid.length) { ctx.fillText("Démarrez le scanner pour enregistrer l’activité.", 20, 100); el(summaryId).textContent = "Aucune mesure de scan"; return; }
    const top = 15, bottom = 180, left = 40, right = width - 20;
    const maximum = Math.max(2, ...valid.map(point => point.active));
    const first = points[0].timestamp, last = Math.max(first + 1, points.at(-1).timestamp);
    for (const value of [0, maximum / 2, maximum]) {
      const y = bottom - (value / maximum) * (bottom - top);
      ctx.strokeStyle = "#e0e9e3"; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = "#71847b"; ctx.fillText(String(Math.round(value)), 8, y + 4);
    }
    ctx.strokeStyle = "#168163"; ctx.lineWidth = 2; ctx.beginPath(); let pen = false;
    for (const point of points) {
      if (point.active === null) { pen = false; continue; }
      const x = left + (point.timestamp - first) / (last - first) * (right - left);
      const y = bottom - point.active / maximum * (bottom - top);
      if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y); pen = true;
    }
    ctx.stroke();
    const latest = valid.at(-1);
    ctx.fillStyle = "#168163"; ctx.beginPath(); ctx.arc(left + (latest.timestamp - first) / (last - first) * (right - left), bottom - latest.active / maximum * (bottom - top), 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#71847b"; ctx.fillText(new Date(first * 1000).toLocaleTimeString("fr-FR"), left, 207);
    ctx.fillText(new Date(last * 1000).toLocaleTimeString("fr-FR"), Math.max(left + 90, right - 60), 207);
    el(summaryId).textContent = `${valid.length} mesures · max. ${Math.max(...valid.map(point => point.active))}`;
    canvas.setAttribute("aria-label", `Activité Bluetooth : ${valid.length} mesures, dernière mesure ${latest.active} adresses actives, maximum ${maximum}. Les lacunes correspondent aux arrêts du scan.`);
  }
  function drawHistory(stats) {
    const points = [];
    let previous = null;
    for (const point of stats.points) {
      if (previous !== null && point.timestamp - previous > 60) points.push({timestamp: previous + 60, active: null});
      points.push({timestamp: point.timestamp, active: point.mean_active}); previous = point.timestamp;
    }
    drawChart(points, "history-chart", "history-chart-summary");
  }
  function labAction(name, payload = {}) { return action(`/api/lab/${name}`, payload); }
  el("calibration-start").addEventListener("click", () => labAction("calibration-start", {address: el("calibration-address").value, distance: Number(el("calibration-distance").value), duration: Number(el("calibration-duration").value)}));
  el("calibration-reset").addEventListener("click", () => labAction("calibration-reset"));
  el("calibration-apply").addEventListener("click", () => labAction("calibration-apply"));
  el("experiment-start").addEventListener("click", () => labAction("experiment-start", {reference_count: Number(el("experiment-reference").value), duration: Number(el("experiment-duration").value), controlled: el("experiment-controlled").checked}));
  el("experiment-cancel").addEventListener("click", () => labAction("experiment-cancel"));
  el("estimate-use").addEventListener("click", () => labAction("estimate-use", {id: el("estimate-experiment").value}));
  el("estimate-clear").addEventListener("click", () => labAction("estimate-clear"));
  el("alerts-apply").addEventListener("click", () => labAction("alerts-config", {activity_threshold: Number(el("alert-threshold").value), silence_seconds: Number(el("alert-silence").value)}));
  window.renderLab = data => {
    lastData = data;
    const lab = data.measurements;
    if (!lab) return;
    el("metric-new-count").textContent = data.new_detections?.total ?? 0;
    el("mean-rssi").textContent = data.mean_rssi === null ? "—" : `${data.mean_rssi} dBm`;
    el("estimated-count").textContent = number(lab.estimate.value);
    el("estimate-method").textContent = lab.estimate.value === null ? "Non calibré : aucun nombre d’appareils physiques affirmé." : `${lab.estimate.label} · essai ${lab.estimate.source.slice(0, 8)}`;
    drawChart(lab.series);
    const calibration = lab.calibration, running = lab.experiment.state === "running";
    options("calibration-address", lab.candidates.map(item => [item.address, `${item.name || "Nom inconnu"} · ${item.address} · ${item.rssi ?? "?"} dBm`]));
    el("calibration-status").textContent = calibration.state === "collecting" ? `Mesure à ${calibration.distance} m · ${seconds(calibration.remaining_seconds)}` : {idle: "Prêt", complete: "Point mesuré", insufficient_data: "Données insuffisantes", cancelled: "Mesure annulée"}[calibration.state] || calibration.state;
    el("calibration-help").textContent = calibration.warning;
    table("calibration-results", calibration.results.map(result => [`${result.distance} m`, result.samples, result.mean === null ? "—" : `${result.mean} dBm`, result.median, result.stdev]));
    el("calibration-recommendation").textContent = calibration.recommended_threshold === null ? "Quatre distances et au moins 5 annonces par point sont nécessaires." : `Seuil proposé : ${calibration.recommended_threshold} dBm. Approximation à calibrer sur place.`;
    el("calibration-start").disabled = busy || data.state !== "scanning" || calibration.state === "collecting";
    el("calibration-apply").disabled = busy || running || calibration.recommended_threshold === null;
    el("calibration-reset").disabled = busy;
    el("saved-calibrations").textContent = lab.saved_calibrations.length ? lab.saved_calibrations.slice(0, 8).map(result => `${result.saved_at} · ${result.distance} m · ${result.samples} annonces · moyenne ${number(result.mean)} dBm`).join("\n") : "Aucune mesure sauvegardée.";
    el("experiment-status").textContent = running ? `En cours · ${seconds(lab.experiment.remaining_seconds)} · ${lab.experiment.sample_count} mesures` : "Aucun essai en cours";
    el("experiment-start").disabled = busy || data.state !== "scanning" || running;
    el("experiment-cancel").disabled = busy || !running;
    table("experiment-results", lab.experiments.map(result => [result.id.slice(0, 8), result.state === "complete" ? "Terminé" : "Annulé", result.reference_count, result.mean_active, result.maximum_active, result.relative_error_percent === null ? "Indéfini" : `${result.relative_error_percent} %`, `${result.config.min_rssi} dBm · ${result.config.presence_seconds} s · ${result.sample_count} mesures`]));
    options("estimate-experiment", lab.experiments.filter(result => result.state === "complete" && result.mean_active > 0 && result.reference_count > 0).map(result => [result.id, `${result.id.slice(0, 8)} · référence ${result.reference_count} · ${result.config.min_rssi} dBm / ${result.config.presence_seconds} s`]));
    const stats = lab.analytics;
    drawHistory(stats);
    el("statistics-summary").textContent = stats.samples ? `Moyenne : ${stats.mean_active} adresses actives · maximum : ${stats.maximum} · ${stats.samples} échantillons · heure la plus active : ${new Date(stats.busiest_hour.timestamp * 1000).toLocaleString("fr-FR")}.` : "Aucune mesure enregistrée pour ces paramètres. Le scanner doit être actif ; le résumé se rafraîchit toutes les 10 secondes.";
    table("statistics-hours", stats.hourly.map(hour => [new Date(hour.timestamp * 1000).toLocaleString("fr-FR"), hour.samples, hour.mean_active, hour.maximum]));
    if (Number(el("alert-threshold").value) === lab.alerts.activity_threshold && Number(el("alert-silence").value) === lab.alerts.silence_seconds) alertDirty = false;
    if (!alertDirty) { el("alert-threshold").value = lab.alerts.activity_threshold; el("alert-silence").value = lab.alerts.silence_seconds; }
    const alerts = document.createDocumentFragment();
    for (const alert of lab.alerts.current) { const item = document.createElement("li"); item.className = `scanner-alert ${alert.level}`; item.textContent = alert.message; alerts.append(item); }
    if (!lab.alerts.current.length) { const item = document.createElement("li"); item.textContent = data.state === "scanning" ? "Aucune alerte active." : "Scanner arrêté ou en cours de démarrage."; alerts.append(item); }
    el("scanner-alerts").replaceChildren(alerts);
    const events = document.createDocumentFragment();
    for (const event of lab.alerts.events) { const item = document.createElement("li"); item.textContent = `${new Date(event.timestamp * 1000).toLocaleTimeString("fr-FR")} · ${event.code} · ${event.message}`; events.append(item); }
    el("alert-events").replaceChildren(events);
    el("health-banner").hidden = !lab.alerts.current.length;
    el("health-banner").textContent = lab.alerts.current.map(alert => alert.message).join(" ");
  };
  const observer = new ResizeObserver(() => { if (lastData?.measurements) { drawChart(lastData.measurements.series); drawHistory(lastData.measurements.analytics); } });
  observer.observe(el("activity-chart")); observer.observe(el("history-chart"));
})();

const els = {
  connection: document.getElementById("connection"),
  connectionText: document.getElementById("connectionText"),
  heroCard: document.getElementById("heroCard"),
  statusEyebrow: document.getElementById("statusEyebrow"),
  statusTitle: document.getElementById("statusTitle"),
  statusDetail: document.getElementById("statusDetail"),
  readTime: document.getElementById("readTime"),
  rpmValue: document.getElementById("rpmValue"),
  metricDetail: document.getElementById("metricDetail"),
  actionCard: document.getElementById("actionCard"),
  actionIcon: document.getElementById("actionIcon"),
  actionTitle: document.getElementById("actionTitle"),
  actionText: document.getElementById("actionText"),
  sensorBadge: document.getElementById("sensorBadge"),
  temperatureValue: document.getElementById("temperatureValue"),
  soundValue: document.getElementById("soundValue"),
  bpmValue: document.getElementById("bpmValue"),
  sensorNote: document.getElementById("sensorNote"),
  updatedAt: document.getElementById("updatedAt"),
};

const POLL_INTERVAL_MS = 2000;

function hasNumber(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function formatTimeAgo(iso) {
  if (!iso) return "Esperando la primera lectura";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "Lectura actualizada ahora";
  if (seconds < 60) return `Actualizado hace ${seconds} s`;
  return `Actualizado hace ${Math.floor(seconds / 60)} min`;
}

function setAction(kind, icon, title, text) {
  els.actionCard.className = `action-card ${kind}`;
  els.actionIcon.textContent = icon;
  els.actionTitle.textContent = title;
  els.actionText.textContent = text;
}

function setHero(kind, eyebrow, title, detail, timestamp) {
  els.heroCard.className = `hero-card state-${kind}`;
  els.statusEyebrow.textContent = eyebrow;
  els.statusTitle.textContent = title;
  els.statusDetail.textContent = detail;
  els.readTime.textContent = formatTimeAgo(timestamp);
}

function updateConnection(connected) {
  els.connection.classList.toggle("online", Boolean(connected));
  els.connectionText.textContent = connected ? "Monitor conectado" : "Sin conexión";
}

function updateInference(snapshot) {
  const inference = snapshot.inference || {};
  const state = inference.state;
  const timestamp = inference.last_inference_at;
  const rpm = inference.rpm_estimate;

  els.rpmValue.textContent = hasNumber(rpm) ? Number(rpm).toFixed(1) : "--";

  if (!snapshot.connected) {
    setHero("unavailable", "CONEXIÓN", "Monitor sin conexión", "Revisa que las dos ESP32 y el servidor estén encendidos y conectados a la misma red.", null);
    els.metricDetail.textContent = "No hay señal reciente para estimar la respiración.";
    setAction("warning", "!", "Revisar conexión", "Cuando el monitor se conecte, esta pantalla se actualizará sola.");
    return;
  }

  if (state === "apnea") {
    const seconds = Math.max(0, Math.round(Number(inference.apnea_duration_s) || 0));
    const duration = seconds > 0 ? ` durante ${seconds} s` : "";
    setHero("apnea", "ATENCIÓN", "Posible pausa detectada", `El monitor no identifica el patrón de respiración esperado${duration}. Verifica el muñeco y la ubicación de las ESP32.`, timestamp);
    els.metricDetail.textContent = "La estimación usa la ventana más reciente de señal.";
    setAction("alert", "!", "Verifica ahora", "Confirma que el mecanismo de respiración esté activo y que nada haya movido las ESP32.");
    return;
  }

  if (state === "breathing") {
    setHero("breathing", "MONITOR ACTIVO", "Respiración detectada", "El patrón de movimiento respiratorio está siendo detectado.", timestamp);
    els.metricDetail.textContent = "Estimación basada en la señal de las ESP32.";
    setAction("normal", "✓", "Todo en calma", "El monitor detecta el patrón esperado. Mantén la posición de las ESP32 sin moverla.");
    return;
  }

  if (state === "warming") {
    const progress = Math.round((Number(inference.progress) || 0) * 100);
    setHero("warming", "PREPARANDO", "Analizando la señal", `El monitor necesita completar su ventana de lectura${progress ? ` (${progress}%)` : ""}.`, null);
    els.metricDetail.textContent = "La respiración se mostrará después de reunir suficiente señal.";
    setAction("warning", "…", "Espera un momento", "No muevas las ESP32 mientras se completa la primera lectura.");
    return;
  }

  setHero("unavailable", "SEÑAL", "Señal por revisar", inference.detail || "La lectura actual no es suficiente para mostrar un estado confiable.", timestamp);
  els.metricDetail.textContent = "Acomoda las ESP32 y espera la siguiente ventana de lectura.";
  setAction("warning", "!", "Revisar instalación", "Verifica que las ESP32 estén encendidas, fijas y orientadas como durante la calibración.");
}

function updateSensors(sensor) {
  if (!sensor) {
    els.sensorBadge.className = "sensor-badge";
    els.sensorBadge.textContent = "Esperando";
    els.temperatureValue.textContent = "--";
    els.soundValue.textContent = "--";
    els.bpmValue.textContent = "--";
    els.sensorNote.textContent = "Aún no llegan lecturas desde los sensores del emisor.";
    return;
  }

  const alerts = sensor.alerts || {};
  const labels = [];
  if (alerts.sound) labels.push("sonido");
  if (alerts.bpm_high) labels.push("ritmo alto");
  if (alerts.temp_high) labels.push("temperatura alta");
  if (alerts.temp_low) labels.push("temperatura baja");

  els.sensorBadge.className = `sensor-badge ${sensor.has_alert ? "alert" : "ok"}`;
  els.sensorBadge.textContent = sensor.has_alert ? "Atención" : "Normal";
  els.temperatureValue.textContent = hasNumber(sensor.temperature_c)
    ? `${Number(sensor.temperature_c).toFixed(1)} °C`
    : "--";
  els.soundValue.textContent = sensor.sound_detected ? "Detectado" : "Normal";
  els.bpmValue.textContent = hasNumber(sensor.bpm) ? `${Math.round(Number(sensor.bpm))} BPM` : "--";
  els.sensorNote.textContent = labels.length
    ? `Sensor con alerta: ${labels.join(", ")}.`
    : "Lecturas de temperatura, sonido y ritmo disponibles.";
}

function render(snapshot) {
  updateConnection(snapshot.connected);
  updateInference(snapshot);
  updateSensors(snapshot.latest_sensor);
  els.updatedAt.textContent = snapshot.connected ? "Actualizado automáticamente" : "Esperando conexión";
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (_error) {
    updateConnection(false);
    setHero("unavailable", "CONEXIÓN", "No se puede abrir el monitor", "Comprueba que el servidor del proyecto esté ejecutándose.", null);
    els.metricDetail.textContent = "No hay datos disponibles.";
    setAction("warning", "!", "Servidor no disponible", "Vuelve a intentarlo cuando el servidor esté encendido.");
    els.updatedAt.textContent = "Sin comunicación con el servidor";
  }
}

refresh();
window.setInterval(refresh, POLL_INTERVAL_MS);

const state = {
  paused: false,
  latest: null,
  history: [],
  rssiHistory: [],
  baseline: null,
  frameLimit: 360,
  activity: 0,
  inference: null,
  sensor: null,
  connected: false,
  lastRender: 0,
  presets: {},
  collection: null,
  durationTouched: false,
  room: {
    width: 5,
    height: 4,
    tx: { x: 0.65, y: 2.05 },
    rx: { x: 4.35, y: 2.2 },
    objects: [
      { name: "Escritorio", x: 2.95, y: 0.75, w: 1.45, h: 0.75 },
      { name: "Cama", x: 0.45, y: 2.75, w: 1.7, h: 0.85 },
      { name: "A/C", x: 0.08, y: 0.25, w: 0.42, h: 0.35 },
      { name: "Laptop", x: 3.35, y: 1.0, w: 0.35, h: 0.25 }
    ]
  }
};

const els = {
  status: document.getElementById("status"),
  statusText: document.getElementById("statusText"),
  subtitle: document.getElementById("subtitle"),
  packetCount: document.getElementById("packetCount"),
  rssi: document.getElementById("rssi"),
  snr: document.getElementById("snr"),
  channel: document.getElementById("channel"),
  subcarriers: document.getElementById("subcarriers"),
  activityValue: document.getElementById("activityValue"),
  ampRange: document.getElementById("ampRange"),
  heatmapRange: document.getElementById("heatmapRange"),
  serialInfo: document.getElementById("serialInfo"),
  calibrateBtn: document.getElementById("calibrateBtn"),
  pauseBtn: document.getElementById("pauseBtn"),
  collectionStatus: document.getElementById("collectionStatus"),
  durationInput: document.getElementById("durationInput"),
  personPositionInput: document.getElementById("personPositionInput"),
  environmentInput: document.getElementById("environmentInput"),
  notesInput: document.getElementById("notesInput"),
  presetButtons: document.getElementById("presetButtons"),
  collectionProgress: document.getElementById("collectionProgress"),
  stopCollectionBtn: document.getElementById("stopCollectionBtn"),
  collectionDetail: document.getElementById("collectionDetail"),
  inferencePanel: document.getElementById("inferencePanel"),
  inferenceStatus: document.getElementById("inferenceStatus"),
  inferenceLabel: document.getElementById("inferenceLabel"),
  inferenceDetail: document.getElementById("inferenceDetail"),
  inferenceWindow: document.getElementById("inferenceWindow"),
  inferenceRpm: document.getElementById("inferenceRpm"),
  inferenceApneaProbability: document.getElementById("inferenceApneaProbability"),
  inferenceConfidence: document.getElementById("inferenceConfidence"),
  inferenceApneaTimer: document.getElementById("inferenceApneaTimer"),
  inferencePc1: document.getElementById("inferencePc1"),
  inferenceSubcarriers: document.getElementById("inferenceSubcarriers"),
  inferenceLatency: document.getElementById("inferenceLatency"),
  inferenceLastRun: document.getElementById("inferenceLastRun"),
  notificationStatus: document.getElementById("notificationStatus"),
  notificationSent: document.getElementById("notificationSent"),
  sensorPanel: document.getElementById("sensorPanel"),
  sensorStatus: document.getElementById("sensorStatus"),
  sensorLabel: document.getElementById("sensorLabel"),
  sensorDetail: document.getElementById("sensorDetail"),
  sensorLastUpdate: document.getElementById("sensorLastUpdate"),
  sensorTemp: document.getElementById("sensorTemp"),
  sensorBpm: document.getElementById("sensorBpm"),
  sensorSound: document.getElementById("sensorSound"),
  sensorBuzzer: document.getElementById("sensorBuzzer"),
  sensorPacketCount: document.getElementById("sensorPacketCount"),
  sensorUptime: document.getElementById("sensorUptime"),
  roomCanvas: document.getElementById("roomCanvas"),
  amplitudeCanvas: document.getElementById("amplitudeCanvas"),
  heatmapCanvas: document.getElementById("heatmapCanvas"),
  rssiCanvas: document.getElementById("rssiCanvas")
};

const presetTitles = {
  empty_off: "Cuarto vacío",
  breathing_off: "Respiración",
  hold_breath_off: "Apnea",
  walking_between_off: "Caminando",
  ambient_on_people: "Ambiente activo"
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function colorFor(value) {
  const t = clamp(value, 0, 1);
  const stops = [
    [0.0, [24, 24, 24]],
    [0.18, [42, 84, 110]],
    [0.42, [83, 190, 178]],
    [0.68, [230, 190, 84]],
    [1.0, [255, 94, 88]]
  ];

  for (let i = 0; i < stops.length - 1; i += 1) {
    const [aT, aC] = stops[i];
    const [bT, bC] = stops[i + 1];
    if (t >= aT && t <= bT) {
      const p = (t - aT) / (bT - aT);
      return [
        Math.round(lerp(aC[0], bC[0], p)),
        Math.round(lerp(aC[1], bC[1], p)),
        Math.round(lerp(aC[2], bC[2], p))
      ];
    }
  }
  return stops[stops.length - 1][1];
}

function activityColor(activity) {
  if (activity < 4) return "#72d66b";
  if (activity < 12) return "#f2bc57";
  return "#ff6b5f";
}

function mean(values) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function displayLabel(label) {
  return presetTitles[label] || String(label || "").replaceAll("_", " ");
}

function formatSeconds(value) {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (!minutes) return `${rest}s`;
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Math.round(Number(value) * 100)}%`;
}

function formatClock(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function inferenceClass(stateName) {
  if (stateName === "breathing") return "breathing";
  if (stateName === "apnea") return "apnea";
  if (stateName === "signal_bad" || stateName === "error") return "warning";
  return "warming";
}

function setCollectionError(message) {
  els.collectionStatus.textContent = "Error";
  els.collectionDetail.textContent = message;
}

function renderPresetButtons(presets) {
  state.presets = presets || {};
  els.presetButtons.innerHTML = "";

  Object.entries(state.presets).forEach(([label, preset]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.label = label;

    const title = document.createElement("strong");
    title.textContent = displayLabel(label);

    const detail = document.createElement("span");
    detail.textContent = `${formatSeconds(preset.duration_s)} | ${preset.description || ""}`;

    button.append(title, detail);
    button.addEventListener("click", () => startCollection(label));
    els.presetButtons.append(button);
  });
}

function applyPresetToForm(label) {
  const preset = state.presets[label] || {};
  if (!state.durationTouched && preset.duration_s) {
    els.durationInput.value = Math.round(preset.duration_s);
  }
  if (preset.person_position) {
    els.personPositionInput.value = preset.person_position;
  }
  if (preset.environment) {
    els.environmentInput.value = preset.environment;
  }
}

function buildCollectionPayload(label) {
  const preset = state.presets[label] || {};
  const duration = Number.parseFloat(els.durationInput.value);
  return {
    label,
    duration_s: Number.isFinite(duration) && duration > 0 ? duration : preset.duration_s || 180,
    person_position: els.personPositionInput.value.trim() || preset.person_position || "",
    environment: els.environmentInput.value.trim() || preset.environment || "",
    notes: els.notesInput.value.trim(),
    description: preset.description || ""
  };
}

async function postJson(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function startCollection(label) {
  try {
    applyPresetToForm(label);
    els.collectionStatus.textContent = `Iniciando ${displayLabel(label)}`;
    els.collectionDetail.textContent = "Preparando archivo CSV y metadatos...";
    const result = await postJson("/api/collection/start", buildCollectionPayload(label));
    updateCollection(result.collection);
  } catch (error) {
    setCollectionError(error.message);
  }
}

async function stopCollection() {
  try {
    els.collectionStatus.textContent = "Deteniendo";
    const result = await postJson("/api/collection/stop");
    updateCollection({ active: false, last_completed: result.completed, presets: state.presets });
  } catch (error) {
    setCollectionError(error.message);
  }
}

function updateCollection(collection) {
  if (!collection) return;
  state.collection = collection;

  if (collection.presets && !Object.keys(state.presets).length) {
    renderPresetButtons(collection.presets);
  }

  const active = Boolean(collection.active);
  const buttons = Array.from(els.presetButtons.querySelectorAll("button"));
  buttons.forEach((button) => {
    button.disabled = active;
    button.classList.toggle("recording", active && button.dataset.label === collection.label);
  });
  els.stopCollectionBtn.disabled = !active;

  if (active) {
    const progress = clamp(collection.progress || 0, 0, 1);
    els.collectionStatus.textContent = `Grabando ${displayLabel(collection.label)}`;
    els.collectionProgress.style.width = `${(progress * 100).toFixed(1)}%`;
    els.collectionDetail.textContent =
      `Evento: ${collection.event_label} | ` +
      `${formatSeconds(collection.elapsed_s)} / ${formatSeconds(collection.duration_s)} | ` +
      `${collection.samples_collected} muestras | ${collection.raw_file}`;
    return;
  }

  els.collectionStatus.textContent = "Sin sesión activa";
  els.collectionProgress.style.width = "0%";

  if (collection.last_completed) {
    const completed = collection.last_completed;
    els.collectionDetail.textContent =
      `Última sesión: ${displayLabel(completed.label)} | ` +
      `${completed.samples_collected} muestras | ` +
      `${completed.sample_rate_estimated_hz} Hz | ` +
      `${completed.raw_file}`;
  } else {
    els.collectionDetail.textContent =
      "Primero verifica que la web reciba CSI; luego presiona un estado para guardar una sesión cronometrada.";
  }
}

function computeBaseline() {
  const rows = state.history.slice(-60);
  if (!rows.length) return null;
  const size = rows[0].length;
  const baseline = new Array(size).fill(0);

  for (const row of rows) {
    for (let i = 0; i < size; i += 1) {
      baseline[i] += row[i] || 0;
    }
  }

  return baseline.map((value) => value / rows.length);
}

function computeActivity(amplitude) {
  if (!state.baseline || !amplitude.length) return 0;
  const len = Math.min(state.baseline.length, amplitude.length);
  let sum = 0;

  for (let i = 0; i < len; i += 1) {
    sum += Math.abs(amplitude[i] - state.baseline[i]);
  }

  return sum / len;
}

function updateInference(inference) {
  if (!inference) return;
  state.inference = inference;

  const className = inferenceClass(inference.state);
  els.inferencePanel.className = `panel wide inference-panel ${className}`;
  els.inferenceStatus.className = `inference-status ${className}`;

  els.inferenceLabel.textContent = inference.label || "Modelo";
  els.inferenceDetail.textContent = inference.detail || "";
  els.inferenceWindow.textContent =
    `Ventana ${Math.round(inference.window_s || 0)}s | ` +
    `refresco ${Math.round(inference.step_s || 0)}s | ` +
    `${Math.round((inference.progress || 0) * 100)}% llena`;

  els.inferenceRpm.textContent =
    inference.rpm_estimate === null || inference.rpm_estimate === undefined
      ? "-- RPM"
      : `${Number(inference.rpm_estimate).toFixed(1)} RPM`;
  els.inferenceApneaProbability.textContent = formatPercent(inference.apnea_probability);
  els.inferenceConfidence.textContent = formatPercent(inference.confidence);
  els.inferenceApneaTimer.textContent = formatSeconds(inference.apnea_duration_s || 0);
  els.inferencePc1.textContent =
    inference.pc1_var_explained === null || inference.pc1_var_explained === undefined
      ? "--"
      : Number(inference.pc1_var_explained).toFixed(2);
  els.inferenceSubcarriers.textContent = inference.active_subcarriers || "--";
  els.inferenceLatency.textContent =
    inference.latency_min_s && inference.latency_max_s
      ? `${Number(inference.latency_min_s).toFixed(0)}-${Number(inference.latency_max_s).toFixed(0)}s`
      : "--";
  els.inferenceLastRun.textContent = formatClock(inference.last_inference_at);
}

function updateNotifications(notifications) {
  if (!notifications) return;
  if (!notifications.enabled) {
    els.notificationStatus.textContent = "Inactivo";
    els.notificationSent.textContent = "0";
    return;
  }

  els.notificationStatus.textContent = notifications.last_error ? "Error" : "Activo";
  els.notificationSent.textContent = String(notifications.sent_count || 0);
}

function updateSensors(sensor, packetCount = 0) {
  els.sensorPacketCount.textContent = String(packetCount || 0);

  if (!sensor) {
    els.sensorPanel.className = "panel wide sensor-panel waiting";
    els.sensorStatus.className = "sensor-status waiting";
    els.sensorLabel.textContent = "Sin datos";
    els.sensorDetail.textContent = "La ESP emisora aun no envia SENSOR_DATA al receptor.";
    els.sensorLastUpdate.textContent = "Esperando SENSOR_DATA";
    els.sensorTemp.textContent = "-- C";
    els.sensorBpm.textContent = "--";
    els.sensorSound.textContent = "--";
    els.sensorBuzzer.textContent = "--";
    els.sensorUptime.textContent = "--";
    return;
  }

  state.sensor = sensor;
  const alerts = sensor.alerts || {};
  const activeAlerts = [];
  if (alerts.sound) activeAlerts.push("sonido");
  if (alerts.bpm_high) activeAlerts.push("BPM alto");
  if (alerts.temp_high) activeAlerts.push("temperatura alta");
  if (alerts.temp_low) activeAlerts.push("temperatura baja");

  const className = sensor.has_alert ? "alert" : "ok";
  els.sensorPanel.className = `panel wide sensor-panel ${className}`;
  els.sensorStatus.className = `sensor-status ${className}`;
  els.sensorLabel.textContent = sensor.has_alert ? "Alerta activa" : "Sensores normales";
  els.sensorDetail.textContent = activeAlerts.length
    ? `Detectado: ${activeAlerts.join(", ")}.`
    : "Temperatura, BPM y sonido dentro del estado esperado.";
  els.sensorLastUpdate.textContent = `Ultima lectura ${formatClock(sensor.time ? sensor.time * 1000 : null)}`;
  els.sensorTemp.textContent =
    sensor.temperature_c === null || sensor.temperature_c === undefined
      ? "-- C"
      : `${Number(sensor.temperature_c).toFixed(1)} C`;
  els.sensorBpm.textContent = String(sensor.bpm ?? "--");
  els.sensorSound.textContent = sensor.sound_detected ? "Detectado" : "Normal";
  els.sensorBuzzer.textContent =
    sensor.buzzer_interval_ms > 0
      ? `${sensor.buzzer_on ? "ON" : "Pulso"} / ${sensor.buzzer_interval_ms} ms`
      : "Silencio";
  els.sensorUptime.textContent = formatSeconds((sensor.uptime_ms || 0) / 1000);
}

function updateData(snapshot) {
  state.connected = snapshot.connected;
  els.status.classList.toggle("connected", snapshot.connected);
  els.statusText.textContent = snapshot.connected ? "Conectado" : "Desconectado";
  els.packetCount.textContent = snapshot.packet_count ?? 0;
  els.serialInfo.textContent = `${snapshot.port || "--"} @ ${snapshot.baud || "--"}`;
  updateCollection(snapshot.collection);
  updateInference(snapshot.inference);
  updateNotifications(snapshot.notifications);
  updateSensors(snapshot.latest_sensor, snapshot.sensor_packet_count);

  if (snapshot.last_error && !snapshot.connected) {
    els.subtitle.textContent = snapshot.last_error;
  }

  if (!snapshot.latest || state.paused) return;

  const sample = snapshot.latest;
  state.latest = sample;
  state.history.push(sample.amplitude);
  state.rssiHistory.push({ rssi: sample.rssi, snr: sample.snr });

  if (state.history.length > state.frameLimit) state.history.shift();
  if (state.rssiHistory.length > state.frameLimit) state.rssiHistory.shift();

  if (!state.baseline && state.history.length >= 30) {
    state.baseline = computeBaseline();
  }

  state.activity = computeActivity(sample.amplitude);

  els.subtitle.textContent = `${sample.mac} | formato ${sample.format}`;
  els.rssi.textContent = `${sample.rssi} dBm`;
  els.snr.textContent = sample.snr === null ? "-- dB" : `${sample.snr} dB`;
  els.channel.textContent = sample.channel;
  els.subcarriers.textContent = sample.subcarriers;
  els.activityValue.textContent = state.activity.toFixed(2);
  els.activityValue.style.color = activityColor(state.activity);
}

function canvasSetup(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round((canvas.height / canvas.width) * rect.width * dpr));

  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: (canvas.height / dpr) };
}

function drawAxes(ctx, width, height, labelY = "") {
  ctx.strokeStyle = "#333333";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(42, 12);
  ctx.lineTo(42, height - 30);
  ctx.lineTo(width - 12, height - 30);
  ctx.stroke();

  ctx.fillStyle = "#8f938d";
  ctx.font = "12px ui-sans-serif, system-ui";
  if (labelY) ctx.fillText(labelY, 10, 24);
}

function drawAmplitude() {
  const { ctx, width, height } = canvasSetup(els.amplitudeCanvas);
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, "amp");

  const data = state.latest?.amplitude || [];
  if (!data.length) return;

  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = Math.max(1, max - min);
  els.ampRange.textContent = `${min.toFixed(1)} - ${max.toFixed(1)}`;

  const left = 42;
  const top = 12;
  const plotWidth = width - 54;
  const plotHeight = height - 42;

  ctx.strokeStyle = "#65d4d7";
  ctx.lineWidth = 2;
  ctx.beginPath();

  data.forEach((value, index) => {
    const x = left + (index / Math.max(1, data.length - 1)) * plotWidth;
    const y = top + (1 - (value - min) / range) * plotHeight;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawHeatmap() {
  const { ctx, width, height } = canvasSetup(els.heatmapCanvas);
  ctx.clearRect(0, 0, width, height);

  const rows = state.history;
  if (!rows.length) return;

  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    for (const value of row) {
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }

  const subcarriers = rows[rows.length - 1].length;
  const range = Math.max(1, max - min);
  els.heatmapRange.textContent = `${min.toFixed(1)} - ${max.toFixed(1)}`;

  const image = ctx.createImageData(rows.length, subcarriers);
  for (let x = 0; x < rows.length; x += 1) {
    const row = rows[x];
    for (let y = 0; y < subcarriers; y += 1) {
      const value = (row[y] - min) / range;
      const [r, g, b] = colorFor(value);
      const targetY = subcarriers - 1 - y;
      const index = (targetY * rows.length + x) * 4;
      image.data[index] = r;
      image.data[index + 1] = g;
      image.data[index + 2] = b;
      image.data[index + 3] = 255;
    }
  }

  const offscreen = document.createElement("canvas");
  offscreen.width = rows.length;
  offscreen.height = subcarriers;
  offscreen.getContext("2d").putImageData(image, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(offscreen, 42, 12, width - 54, height - 42);

  drawAxes(ctx, width, height, "sub");
}

function drawRssi() {
  const { ctx, width, height } = canvasSetup(els.rssiCanvas);
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, "dB");

  const rows = state.rssiHistory;
  if (!rows.length) return;

  const values = rows.flatMap((row) => [row.rssi, row.snr].filter((value) => value !== null));
  const min = Math.min(...values, -95);
  const max = Math.max(...values, 40);
  const range = Math.max(1, max - min);

  function plot(key, color) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();

    rows.forEach((row, index) => {
      const value = row[key];
      if (value === null) return;
      const x = 42 + (index / Math.max(1, rows.length - 1)) * (width - 54);
      const y = 12 + (1 - (value - min) / range) * (height - 42);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });

    ctx.stroke();
  }

  plot("rssi", "#f2bc57");
  plot("snr", "#a88cf0");
}

function drawRoom() {
  const { ctx, width, height } = canvasSetup(els.roomCanvas);
  ctx.clearRect(0, 0, width, height);

  const pad = 36;
  const room = state.room;
  const scale = Math.min((width - pad * 2) / room.width, (height - pad * 2) / room.height);
  const ox = (width - room.width * scale) / 2;
  const oy = (height - room.height * scale) / 2;

  const toPx = (point) => ({
    x: ox + point.x * scale,
    y: oy + point.y * scale
  });

  ctx.fillStyle = "#1b1b1b";
  ctx.strokeStyle = "#4a4a4a";
  ctx.lineWidth = 2;
  ctx.fillRect(ox, oy, room.width * scale, room.height * scale);
  ctx.strokeRect(ox, oy, room.width * scale, room.height * scale);

  for (const item of room.objects) {
    const pos = toPx(item);
    ctx.fillStyle = item.name === "A/C" ? "#2f3b3c" : "#2a2520";
    ctx.strokeStyle = "#5c544a";
    ctx.lineWidth = 1;
    ctx.fillRect(pos.x, pos.y, item.w * scale, item.h * scale);
    ctx.strokeRect(pos.x, pos.y, item.w * scale, item.h * scale);
    ctx.fillStyle = "#c9cbc5";
    ctx.font = "12px ui-sans-serif, system-ui";
    ctx.fillText(item.name, pos.x + 8, pos.y + 18);
  }

  const tx = toPx(room.tx);
  const rx = toPx(room.rx);
  const dx = rx.x - tx.x;
  const dy = rx.y - tx.y;
  const angle = Math.atan2(dy, dx);
  const distance = Math.hypot(dx, dy);
  const activity = clamp(state.activity / 18, 0, 1);

  ctx.save();
  ctx.translate(tx.x, tx.y);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.ellipse(distance / 2, 0, distance / 2, 54 + activity * 44, 0, 0, Math.PI * 2);
  ctx.fillStyle = `rgba(${activity < 0.35 ? "114,214,107" : activity < 0.72 ? "242,188,87" : "255,107,95"}, ${0.12 + activity * 0.18})`;
  ctx.fill();
  ctx.restore();

  ctx.strokeStyle = activityColor(state.activity);
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(tx.x, tx.y);
  ctx.lineTo(rx.x, rx.y);
  ctx.stroke();

  function node(point, label, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(point.x, point.y, 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#f2f2ef";
    ctx.font = "13px ui-sans-serif, system-ui";
    ctx.fillText(label, point.x + 14, point.y + 5);
  }

  node(tx, "TX", "#65d4d7");
  node(rx, "RX", "#72d66b");

  ctx.fillStyle = "#a7aaa5";
  ctx.font = "12px ui-sans-serif, system-ui";
  ctx.fillText(`Actividad media: ${state.activity.toFixed(2)}`, ox, oy + room.height * scale + 24);
}

function render(now = 0) {
  if (now - state.lastRender > 80) {
    drawRoom();
    drawAmplitude();
    drawHeatmap();
    drawRssi();
    state.lastRender = now;
  }
  requestAnimationFrame(render);
}

function connectEvents() {
  const source = new EventSource("/events");

  source.onmessage = (event) => {
    try {
      updateData(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
    }
  };

  source.onerror = () => {
    els.status.classList.remove("connected");
    els.statusText.textContent = "Reconectando";
  };
}

els.calibrateBtn.addEventListener("click", () => {
  state.baseline = computeBaseline();
});

els.pauseBtn.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pauseBtn.textContent = state.paused ? "Reanudar" : "Pausar";
});

els.durationInput.addEventListener("input", () => {
  state.durationTouched = true;
});

els.stopCollectionBtn.addEventListener("click", stopCollection);

window.addEventListener("resize", () => {
  drawRoom();
  drawAmplitude();
  drawHeatmap();
  drawRssi();
});

connectEvents();
requestAnimationFrame(render);

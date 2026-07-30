#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import math
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from collections import deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from logging.handlers import RotatingFileHandler
from pathlib import Path

CLASSIC_COLUMNS = [
    "type",
    "id",
    "mac",
    "rssi",
    "rate",
    "sig_mode",
    "mcs",
    "bandwidth",
    "smoothing",
    "not_sounding",
    "aggregation",
    "stbc",
    "fec_coding",
    "sgi",
    "noise_floor",
    "ampdu_cnt",
    "channel",
    "secondary_channel",
    "local_timestamp",
    "ant",
    "sig_len",
    "rx_state",
    "len",
    "first_word",
    "data",
]

C5C6_COLUMNS = [
    "type",
    "id",
    "mac",
    "rssi",
    "rate",
    "noise_floor",
    "fft_gain",
    "agc_gain",
    "channel",
    "local_timestamp",
    "sig_len",
    "rx_state",
    "len",
    "first_word",
    "data",
]

SENSOR_COLUMNS = [
    "type",
    "seq",
    "temp_c_x10",
    "bpm",
    "sound_detected",
    "alert_sound",
    "alert_bpm_high",
    "alert_temp_high",
    "alert_temp_low",
    "buzzer_interval_ms",
    "buzzer_on",
    "uptime_ms",
]

WIRE_BATCH_HEADER = struct.Struct("<4sBBH")
WIRE_FRAME_HEADER = struct.Struct("<BBHI")
WIRE_CSI_BODY = struct.Struct("<IbbBBHHBB6s")
WIRE_SENSOR_BODY = struct.Struct("<IHHIhHBBHBBI")
WIRE_BATCH_MAGIC = b"CSIH"
WIRE_BATCH_VERSION = 1
WIRE_FRAME_CSI = 1
WIRE_FRAME_SENSOR = 2
WIRE_MAX_BODY = 64 * 1024

COLLECTION_PRESETS = {
    "empty_off": {
        "description": "Todo apagado, sin nadie entre ESP32.",
        "duration_s": 180,
        "person_position": "none",
        "environment": "off",
    },
    "breathing_off": {
        "description": "Persona quieta respirando normal en medio de las ESP32.",
        "duration_s": 180,
        "person_position": "between_tx_rx",
        "environment": "off",
    },
    "hold_breath_off": {
        "description": "Muñeco respirando y detenido por ciclos balanceados de 60 s.",
        "duration_s": 360,
        "person_position": "between_tx_rx",
        "environment": "off",
        "events": [
            {"start_s": 0, "end_s": 60, "label": "breathing"},
            {"start_s": 60, "end_s": 120, "label": "hold_breath"},
            {"start_s": 120, "end_s": 180, "label": "breathing"},
            {"start_s": 180, "end_s": 240, "label": "hold_breath"},
            {"start_s": 240, "end_s": 300, "label": "breathing"},
            {"start_s": 300, "end_s": 360, "label": "hold_breath"},
        ],
    },
    "walking_between_off": {
        "description": "Persona caminando entre las ESP32.",
        "duration_s": 120,
        "person_position": "walking_between_tx_rx",
        "environment": "off",
    },
    "ambient_on_people": {
        "description": "Aire acondicionado/equipos encendidos y gente caminando.",
        "duration_s": 180,
        "person_position": "people_moving",
        "environment": "ambient_on",
    },
}

RAW_OUTPUT_COLUMNS = [
    "session_id",
    "sample_index",
    "received_at",
    "elapsed_s",
    "label",
    "event_label",
    "person_position",
    "environment",
    "notes",
    "raw_line",
    "type",
    "id",
    "mac",
    "rssi",
    "rate",
    "noise_floor",
    "fft_gain",
    "agc_gain",
    "channel",
    "timestamp",
    "sig_len",
    "rx_state",
    "len",
    "first_word",
    "data",
    "sig_mode",
    "mcs",
    "bandwidth",
    "smoothing",
    "not_sounding",
    "aggregation",
    "stbc",
    "fec_coding",
    "sgi",
    "ampdu_cnt",
    "secondary_channel",
    "ant",
    "parse_error",
]

MODEL_FEATURES = [
    "psd_resp_band",
    "psd_total",
    "snr_resp",
    "variance_filtered",
    "zero_crossings",
    "spectral_entropy",
    "peak_freq_hz",
    "peak_power",
]

DEFAULT_MODEL_PATH = str(Path(__file__).resolve().parents[1] / "models" / "apnea_model_july_2026.joblib")
DEFAULT_TELEGRAM_ALERT_MESSAGE = "\U0001f6a8 \u26a0\ufe0f ALERTA DE AHOGO: posible apnea detectada."
LOCAL_ENV_FILENAME = ".env.local"
DEFAULT_LOG_DIRNAME = "logs"
LOG_CATEGORIES = {
    "startup": "Arranque y configuracion del servidor.",
    "network": "Recepcion HTTP de datos CSI y sensores.",
    "serial": "Conexion, desconexion o lectura del puerto serial.",
    "parse": "Lineas CSI recibidas con formato invalido.",
    "collection": "Inicio, escritura o cierre de sesiones CSV.",
    "ml": "Carga del modelo, preprocesamiento e inferencia.",
    "telegram": "Envio de alertas por Telegram.",
    "http": "Errores de endpoints de la web.",
}


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_name(value):
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value).strip())
    return cleaned.strip("_") or "unknown"


def ensure_data_dirs(base_dir):
    paths = {
        "raw": base_dir / "data" / "raw",
        "metadata": base_dir / "data" / "metadata",
        "processed": base_dir / "data" / "processed",
        "reports": base_dir / "data" / "reports",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def truncate_text(value, max_len=500):
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class LogService:
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir).expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.app_log_path = self.log_dir / "app.log"
        self.error_log_path = self.log_dir / "errors.jsonl"
        self.lock = threading.Lock()
        self.counts = {category: 0 for category in LOG_CATEGORIES}
        self.recent = deque(maxlen=30)
        self.logger = logging.getLogger(f"csi_web.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        handler = RotatingFileHandler(
            self.app_log_path,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        self.logger.handlers.clear()
        self.logger.addHandler(handler)

    def info(self, category, message, context=None):
        self.event(category, message, level="INFO", context=context)

    def warning(self, category, message, context=None):
        self.event(category, message, level="WARNING", context=context)

    def error(self, category, message, context=None, exc=None):
        self.event(category, message, level="ERROR", context=context, exc=exc)

    def event(self, category, message, level="ERROR", context=None, exc=None):
        category = category if category in LOG_CATEGORIES else "startup"
        record = {
            "time": iso_now(),
            "level": level,
            "category": category,
            "message": truncate_text(message, 1000),
            "context": context or {},
        }
        if exc is not None:
            record["exception_type"] = exc.__class__.__name__
            record["exception"] = truncate_text(str(exc), 1000)

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with open(self.error_log_path, "a", encoding="utf-8") as error_fd:
                error_fd.write(line + "\n")
            self.counts[category] = self.counts.get(category, 0) + 1
            self.recent.append(record)

        log_level = getattr(logging, level.upper(), logging.ERROR)
        self.logger.log(log_level, "%s | %s | %s", category, record["message"], record["context"])

    def snapshot(self):
        with self.lock:
            return {
                "log_dir": str(self.log_dir),
                "app_log": str(self.app_log_path),
                "error_log": str(self.error_log_path),
                "categories": LOG_CATEGORIES,
                "counts": dict(self.counts),
                "recent": list(self.recent),
            }


class NullLogService:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def snapshot(self):
        return {
            "log_dir": "",
            "app_log": "",
            "error_log": "",
            "categories": LOG_CATEGORIES,
            "counts": {},
            "recent": [],
        }


def load_env_file(path):
    path = Path(path)
    if not path.exists():
        return False

    with open(path, "r", encoding="utf-8") as env_fd:
        for line in env_fd:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ[key] = value

    return True


def event_label_at(elapsed_s, label, events):
    for event in events:
        if event["start_s"] <= elapsed_s < event["end_s"]:
            return event["label"]
    return label


def schedule_state_at(elapsed_s, label, events):
    """Return the active protocol phase and the next scheduled transition."""
    elapsed_s = max(0.0, float(elapsed_s))
    if not events:
        return {
            "phase_index": 0,
            "phase_count": 1,
            "phase_label": label,
            "phase_start_s": 0.0,
            "phase_end_s": None,
            "phase_elapsed_s": elapsed_s,
            "phase_remaining_s": None,
            "next_phase_label": None,
            "next_phase_start_s": None,
        }

    for index, event in enumerate(events):
        start_s = float(event["start_s"])
        end_s = float(event["end_s"])
        if start_s <= elapsed_s < end_s:
            next_event = events[index + 1] if index + 1 < len(events) else None
            return {
                "phase_index": index,
                "phase_count": len(events),
                "phase_label": event["label"],
                "phase_start_s": start_s,
                "phase_end_s": end_s,
                "phase_elapsed_s": max(0.0, elapsed_s - start_s),
                "phase_remaining_s": max(0.0, end_s - elapsed_s),
                "next_phase_label": next_event["label"] if next_event else None,
                "next_phase_start_s": float(next_event["start_s"]) if next_event else None,
            }

    if elapsed_s < float(events[0]["start_s"]):
        event = events[0]
        return {
            "phase_index": -1,
            "phase_count": len(events),
            "phase_label": "waiting",
            "phase_start_s": 0.0,
            "phase_end_s": float(event["start_s"]),
            "phase_elapsed_s": elapsed_s,
            "phase_remaining_s": max(0.0, float(event["start_s"]) - elapsed_s),
            "next_phase_label": event["label"],
            "next_phase_start_s": float(event["start_s"]),
        }

    last = events[-1]
    return {
        "phase_index": len(events) - 1,
        "phase_count": len(events),
        "phase_label": last["label"],
        "phase_start_s": float(last["start_s"]),
        "phase_end_s": float(last["end_s"]),
        "phase_elapsed_s": max(0.0, float(last["end_s"]) - float(last["start_s"])),
        "phase_remaining_s": 0.0,
        "next_phase_label": None,
        "next_phase_start_s": None,
    }


class RealtimeApneaInference:
    def __init__(
        self,
        model_path,
        window_s=20.0,
        step_s=5.0,
        target_hz=50.0,
        respiratory_low_hz=0.1,
        respiratory_high_hz=0.5,
        min_pc1_var=0.30,
        log_service=None,
    ):
        self.logs = log_service or NullLogService()
        self.model_path = str(model_path) if model_path else ""
        self.window_s = float(window_s)
        self.step_s = float(step_s)
        self.target_hz = float(target_hz)
        self.respiratory_low_hz = float(respiratory_low_hz)
        self.respiratory_high_hz = float(respiratory_high_hz)
        self.min_pc1_var = float(min_pc1_var)
        self.buffer = deque()
        self.lock = threading.Lock()
        self.last_eval_monotonic = 0.0
        self.apnea_started_monotonic = None
        self.np = None
        self.butter = None
        self.filtfilt = None
        self.welch = None
        self.model = None
        self.scaler = None
        self.features = list(MODEL_FEATURES)
        self.status = {
            "enabled": False,
            "ready": False,
            "state": "model_unavailable",
            "label": "Modelo no disponible",
            "detail": "El modelo ML no se ha cargado.",
            "model_path": self.model_path,
            "mode": "sliding_window_features",
            "window_s": self.window_s,
            "step_s": self.step_s,
            "latency_min_s": round(self.window_s / 2.0, 1),
            "latency_max_s": round(self.window_s + self.step_s, 1),
            "last_inference_at": None,
            "progress": 0,
            "samples_in_window": 0,
            "active_subcarriers": 0,
            "rpm_estimate": None,
            "apnea_probability": None,
            "confidence": None,
            "model_score": None,
            "apnea_duration_s": 0,
            "pc1_var_explained": None,
            "features": {},
            "feature_zscores": {},
            "resp_preview": [],
            "last_error": "",
        }
        self._load_model()

    def _load_model(self):
        if not self.model_path:
            self.status["detail"] = "Ejecuta server.py con --model-path para habilitar inferencia."
            self.logs.warning("ml", self.status["detail"])
            return

        if not os.path.exists(self.model_path):
            self.status["detail"] = f"No existe el archivo del modelo: {self.model_path}"
            self.logs.error("ml", self.status["detail"], context={"model_path": self.model_path})
            return

        try:
            import joblib
            import numpy as np
            from scipy.signal import butter, filtfilt, welch
        except Exception as exc:
            self.status["detail"] = (
                "Faltan dependencias ML. Instala numpy scipy scikit-learn joblib "
                f"en el Python que corre esta web. Detalle: {exc}"
            )
            self.status["last_error"] = str(exc)
            self.logs.error("ml", self.status["detail"], exc=exc)
            return

        try:
            loaded = joblib.load(self.model_path)
        except Exception as exc:
            self.status["detail"] = f"No se pudo cargar el modelo: {exc}"
            self.status["last_error"] = str(exc)
            self.logs.error("ml", self.status["detail"], context={"model_path": self.model_path}, exc=exc)
            return

        if isinstance(loaded, dict):
            self.model = loaded.get("model") or loaded.get("clf") or loaded.get("classifier")
            self.scaler = loaded.get("scaler")
            self.features = list(loaded.get("features") or loaded.get("feature_names") or MODEL_FEATURES)
        else:
            self.model = loaded

        if self.model is None:
            self.status["detail"] = "El joblib no contiene una clave de modelo reconocible."
            self.logs.error("ml", self.status["detail"], context={"model_path": self.model_path})
            return

        self.np = np
        self.butter = butter
        self.filtfilt = filtfilt
        self.welch = welch
        self.status.update(
            {
                "enabled": True,
                "ready": False,
                "state": "warming",
                "label": "Calentando ventana",
                "detail": f"Modelo cargado. Esperando {int(self.window_s)} s de CSI.",
                "last_error": "",
            }
        )
        self.logs.info(
            "ml",
            "Modelo cargado correctamente.",
            context={"model_path": self.model_path, "features": self.features},
        )

    def snapshot(self):
        with self.lock:
            status = dict(self.status)
            status["features"] = dict(self.status.get("features") or {})
            return status

    def add_sample(self, payload):
        now = time.monotonic()
        with self.lock:
            if not self.status.get("enabled"):
                return

            amplitude = payload.get("amplitude") or []
            if not amplitude:
                return

            try:
                amp = self.np.asarray(amplitude, dtype=self.np.float32)
            except Exception as exc:
                self._set_error_locked(f"Amplitud invalida: {exc}")
                return

            self.buffer.append((now, amp))
            cutoff = now - max(self.window_s * 1.5, self.window_s + self.step_s)
            while self.buffer and self.buffer[0][0] < cutoff:
                self.buffer.popleft()

            duration = self.buffer[-1][0] - self.buffer[0][0] if len(self.buffer) > 1 else 0
            progress = min(1.0, duration / self.window_s) if self.window_s else 0
            self.status["progress"] = round(progress, 3)
            self.status["samples_in_window"] = sum(1 for ts, _ in self.buffer if ts >= now - self.window_s)

            if duration < self.window_s:
                self.status.update(
                    {
                        "ready": False,
                        "state": "warming",
                        "label": "Calentando ventana",
                        "detail": f"Recolectando ventana ML: {duration:.1f}/{self.window_s:.0f} s.",
                    }
                )
                return

            if now - self.last_eval_monotonic < self.step_s:
                return

            self.last_eval_monotonic = now
            try:
                result = self._evaluate_locked(now)
                self.status.update(result)
            except Exception as exc:
                self._set_error_locked(str(exc))

    def _set_error_locked(self, message):
        self.logs.error("ml", message)
        self.status.update(
            {
                "ready": False,
                "state": "error",
                "label": "Error ML",
                "detail": message,
                "last_error": message,
            }
        )

    def _evaluate_locked(self, now):
        np = self.np
        samples = [(ts, amp) for ts, amp in self.buffer if ts >= now - self.window_s]
        if len(samples) < max(10, int(self.window_s * 8)):
            return {
                "ready": False,
                "state": "signal_bad",
                "label": "Señal insuficiente",
                "detail": "No hay suficientes paquetes CSI en la ventana.",
            }

        min_len = min(len(amp) for _, amp in samples)
        if min_len < 16:
            return {
                "ready": False,
                "state": "signal_bad",
                "label": "CSI insuficiente",
                "detail": "La trama CSI tiene muy pocas subportadoras.",
            }

        times = np.asarray([ts for ts, _ in samples], dtype=np.float64)
        matrix = np.stack([amp[:min_len] for _, amp in samples], axis=0)
        start_time = now - self.window_s
        rel_times = times - start_time

        target_count = int(round(self.window_s * self.target_hz))
        target_times = np.linspace(0.0, self.window_s, target_count, endpoint=False)

        active_mask = matrix.mean(axis=0) > 0.5
        active_count = int(active_mask.sum())
        if active_count < 8:
            return {
                "ready": False,
                "state": "signal_bad",
                "label": "Sin señal útil",
                "detail": "No hay suficientes subportadoras activas.",
                "active_subcarriers": active_count,
            }

        matrix = matrix[:, active_mask]
        uniform = np.empty((target_count, matrix.shape[1]), dtype=np.float32)
        for carrier in range(matrix.shape[1]):
            uniform[:, carrier] = np.interp(target_times, rel_times, matrix[:, carrier])

        detrended = np.empty_like(uniform)
        rolling_n = int(round(3.0 * self.target_hz))
        if rolling_n % 2 == 0:
            rolling_n += 1
        kernel = np.ones(rolling_n, dtype=np.float32) / rolling_n
        for carrier in range(uniform.shape[1]):
            padded = np.pad(uniform[:, carrier], rolling_n // 2, mode="edge")
            trend = np.convolve(padded, kernel, mode="valid")
            detrended[:, carrier] = uniform[:, carrier] - trend

        centered = detrended - detrended.mean(axis=0)
        u, s, _ = np.linalg.svd(centered, full_matrices=False)
        if not len(s) or float((s * s).sum()) <= 1e-12:
            return {
                "ready": False,
                "state": "signal_bad",
                "label": "Sin variación",
                "detail": "La ventana CSI no tiene variación útil.",
                "active_subcarriers": active_count,
            }

        pc1 = u[:, 0] * s[0]
        pc1_var = float(s[0] ** 2 / (s * s).sum())
        if pc1_var < self.min_pc1_var:
            return {
                "ready": False,
                "state": "signal_bad",
                "label": "Señal débil",
                "detail": f"PC1 explica solo {pc1_var:.2f} de la variación.",
                "active_subcarriers": active_count,
                "pc1_var_explained": round(pc1_var, 4),
            }

        nyq = self.target_hz / 2.0
        b, a = self.butter(
            4,
            [self.respiratory_low_hz / nyq, self.respiratory_high_hz / nyq],
            btype="band",
        )
        resp = self.filtfilt(b, a, pc1)
        features = self._extract_features(resp)
        raw_row = [[float(features[name]) for name in self.features]]
        feature_zscores = {}
        row = raw_row
        if self.scaler is not None:
            row = self.scaler.transform(row)
            feature_zscores = {
                name: float(value)
                for name, value in zip(self.features, row[0])
            }

        prediction = int(self.model.predict(row)[0])
        apnea_probability = None
        confidence = None
        model_score = None
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(row)[0]
            classes = list(getattr(self.model, "classes_", [0, 1]))
            if 1 in classes:
                apnea_probability = float(proba[classes.index(1)])
            else:
                apnea_probability = float(proba[-1])
            confidence = float(max(proba))
        elif hasattr(self.model, "decision_function"):
            score = float(self.model.decision_function(row)[0])
            model_score = score
            apnea_probability = float(1.0 / (1.0 + math.exp(-score)))
            confidence = max(apnea_probability, 1.0 - apnea_probability)

        if hasattr(self.model, "decision_function") and model_score is None:
            model_score = float(self.model.decision_function(row)[0])

        preview_step = max(1, len(resp) // 160)
        resp_center = float(resp.mean())
        resp_scale = max(float(resp.std()), 1e-9)
        resp_preview = [
            round(float((value - resp_center) / resp_scale), 4)
            for value in resp[::preview_step]
        ]

        state = "apnea" if prediction == 1 else "breathing"
        if state == "apnea":
            if self.apnea_started_monotonic is None:
                self.apnea_started_monotonic = now
            apnea_duration = now - self.apnea_started_monotonic
        else:
            self.apnea_started_monotonic = None
            apnea_duration = 0.0

        label = "Posible apnea" if state == "apnea" else "Respirando"
        detail = (
            f"Ventana {self.window_s:.0f}s | "
            f"RPM {features['rpm_estimate']:.1f} | "
            f"PC1 {pc1_var:.2f}"
        )

        return {
            "ready": True,
            "state": state,
            "label": label,
            "detail": detail,
            "last_inference_at": iso_now(),
            "progress": 1,
            "samples_in_window": len(samples),
            "active_subcarriers": active_count,
            "rpm_estimate": round(float(features["rpm_estimate"]), 2),
            "apnea_probability": round(apnea_probability, 4) if apnea_probability is not None else None,
            "confidence": round(confidence, 4) if confidence is not None else None,
            "model_score": round(model_score, 4) if model_score is not None else None,
            "apnea_duration_s": round(apnea_duration, 1),
            "pc1_var_explained": round(pc1_var, 4),
            "features": {key: round(float(value), 6) for key, value in features.items()},
            "feature_zscores": {key: round(float(value), 4) for key, value in feature_zscores.items()},
            "resp_preview": resp_preview,
            "last_error": "",
        }

    def _extract_features(self, resp):
        np = self.np
        f, pxx = self.welch(resp, fs=self.target_hz, nperseg=min(len(resp), int(round(self.window_s * self.target_hz))))
        band = (f >= self.respiratory_low_hz) & (f <= self.respiratory_high_hz)
        psd_resp = float(pxx[band].mean()) if bool(band.any()) else 0.0
        psd_total = float(pxx.mean())
        snr_resp = psd_resp / (psd_total + 1e-12)
        variance_filtered = float(resp.var())
        zero_crossings = int((np.diff(np.sign(resp)) != 0).sum())
        normalized = pxx / (pxx.sum() + 1e-12)
        spectral_entropy = float(-np.sum(normalized * np.log(normalized + 1e-12)))

        if bool(band.any()) and float(pxx[band].max()) > 0:
            band_freqs = f[band]
            band_power = pxx[band]
            peak_index = int(np.argmax(band_power))
            peak_freq = float(band_freqs[peak_index])
            peak_power = float(band_power[peak_index])
        else:
            peak_freq = 0.0
            peak_power = 0.0

        return {
            "psd_resp_band": psd_resp,
            "psd_total": psd_total,
            "snr_resp": snr_resp,
            "variance_filtered": variance_filtered,
            "zero_crossings": zero_crossings,
            "spectral_entropy": spectral_entropy,
            "peak_freq_hz": peak_freq,
            "peak_power": peak_power,
            "rpm_estimate": peak_freq * 60.0,
        }


class TelegramNotifier:
    def __init__(
        self,
        bot_token="",
        chat_id="",
        interval_s=1.0,
        message=DEFAULT_TELEGRAM_ALERT_MESSAGE,
        log_service=None,
    ):
        self.logs = log_service or NullLogService()
        self.bot_token = str(bot_token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.interval_s = max(0.5, float(interval_s or 1.0))
        self.message = str(message or DEFAULT_TELEGRAM_ALERT_MESSAGE)
        self.enabled = bool(self.bot_token and self.chat_id)
        self.lock = threading.Lock()
        self.in_flight = False
        self.last_sent_monotonic = 0.0
        self.last_sent_at = None
        self.sent_count = 0
        self.last_error = ""
        self.last_state = "unknown"

    def update(self, inference):
        if not self.enabled:
            return

        state = str((inference or {}).get("state") or "unknown")
        now = time.monotonic()
        with self.lock:
            self.last_state = state
            if state != "apnea":
                self.last_sent_monotonic = 0.0
                return

            if self.in_flight or now - self.last_sent_monotonic < self.interval_s:
                return

            self.in_flight = True
            self.last_sent_monotonic = now

        thread = threading.Thread(target=self._send_message, daemon=True)
        thread.start()

    def _send_message(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": self.message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Telegram HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            try:
                response_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                response_body = ""
            with self.lock:
                self.last_error = str(exc)
                self.in_flight = False
            self.logs.error(
                "telegram",
                "No se pudo enviar alerta por Telegram.",
                context={
                    "status": exc.code,
                    "response": truncate_text(response_body, 1000),
                    "chat_id": self.chat_id,
                },
                exc=exc,
            )
            return
        except (urllib.error.URLError, RuntimeError) as exc:
            with self.lock:
                self.last_error = str(exc)
                self.in_flight = False
            self.logs.error("telegram", "No se pudo enviar alerta por Telegram.", exc=exc)
            return

        with self.lock:
            self.sent_count += 1
            self.last_sent_at = iso_now()
            self.last_error = ""
            self.in_flight = False
        self.logs.info("telegram", "Alerta enviada por Telegram.", context={"chat_configured": bool(self.chat_id)})

    def snapshot(self):
        with self.lock:
            return {
                "enabled": self.enabled,
                "chat_configured": bool(self.chat_id),
                "interval_s": self.interval_s,
                "sent_count": self.sent_count,
                "last_sent_at": self.last_sent_at,
                "last_error": self.last_error,
                "last_state": self.last_state,
            }


class CsiRecorder:
    def __init__(self, base_dir, log_service=None):
        self.base_dir = Path(base_dir)
        self.logs = log_service or NullLogService()
        self.dirs = ensure_data_dirs(self.base_dir)
        self.lock = threading.Lock()
        self.active = False
        self.session = None
        self.raw_fd = None
        self.writer = None
        self.sample_index = 0
        self.corrupt_lines = 0
        self.started_monotonic = None
        self.last_completed = None

    def start(self, config):
        with self.lock:
            if self.active:
                raise RuntimeError("Ya hay una sesion de recoleccion activa")

            label = safe_name(config.get("label", "unknown"))
            preset = COLLECTION_PRESETS.get(label, {})
            duration_s = float(config.get("duration_s") or preset.get("duration_s") or 180)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = safe_name(config.get("session_id") or f"session_{timestamp}_{label}")
            events = config.get("events") or preset.get("events") or []
            events = [
                {
                    "start_s": float(event["start_s"]),
                    "end_s": float(event["end_s"]),
                    "label": str(event["label"]),
                }
                for event in events
            ]

            raw_file = self.dirs["raw"] / f"{session_id}_{label}.csv"
            metadata_file = self.dirs["metadata"] / f"{session_id}_{label}.json"

            self.raw_fd = open(raw_file, "w", newline="", encoding="utf-8")
            self.writer = csv.DictWriter(self.raw_fd, fieldnames=RAW_OUTPUT_COLUMNS)
            self.writer.writeheader()
            self.raw_fd.flush()
            self.sample_index = 0
            self.corrupt_lines = 0
            self.started_monotonic = time.monotonic()
            self.active = True
            self.session = {
                "session_id": session_id,
                "label": label,
                "duration_s": duration_s,
                "requested_duration_s": duration_s,
                "description": config.get("description") or preset.get("description", ""),
                "tx_position": config.get("tx_position", ""),
                "rx_position": config.get("rx_position", ""),
                "distance_cm": config.get("distance_cm", ""),
                "height_cm": config.get("height_cm", ""),
                "environment": config.get("environment") or preset.get("environment", ""),
                "person_position": config.get("person_position") or preset.get("person_position", ""),
                "notes": config.get("notes", ""),
                "events": events,
                "started_at": iso_now(),
                "ended_at": None,
                "raw_file": str(raw_file.relative_to(self.base_dir)),
                "metadata_file": str(metadata_file.relative_to(self.base_dir)),
                "samples_collected": 0,
                "corrupt_lines": 0,
                "sample_rate_estimated_hz": 0,
            }
            self.logs.info(
                "collection",
                "Sesion de recoleccion iniciada.",
                context={
                    "session_id": session_id,
                    "label": label,
                    "duration_s": duration_s,
                    "raw_file": str(raw_file),
                    "metadata_file": str(metadata_file),
                },
            )
            return self.snapshot_locked()

    def stop(self, reason="manual"):
        with self.lock:
            return self._stop_locked(reason)

    def _stop_locked(self, reason):
        if not self.active:
            return self.last_completed

        elapsed_s = max(time.monotonic() - self.started_monotonic, 1e-6)
        self.session["ended_at"] = iso_now()
        self.session["duration_s"] = round(elapsed_s, 3)
        self.session["samples_collected"] = self.sample_index
        self.session["corrupt_lines"] = self.corrupt_lines
        self.session["sample_rate_estimated_hz"] = round(self.sample_index / elapsed_s, 3)
        self.session["stop_reason"] = reason

        metadata_file = self.base_dir / self.session["metadata_file"]
        with open(metadata_file, "w", encoding="utf-8") as metadata_fd:
            json.dump(self.session, metadata_fd, indent=2, ensure_ascii=False)

        if self.raw_fd:
            self.raw_fd.flush()
            self.raw_fd.close()

        completed = dict(self.session)
        self.active = False
        self.session = None
        self.raw_fd = None
        self.writer = None
        self.started_monotonic = None
        self.last_completed = completed
        self.logs.info(
            "collection",
            "Sesion de recoleccion cerrada.",
            context={
                "session_id": completed.get("session_id"),
                "label": completed.get("label"),
                "reason": reason,
                "samples_collected": completed.get("samples_collected"),
                "raw_file": completed.get("raw_file"),
            },
        )
        return completed

    def record_sample(self, raw_line, record):
        with self.lock:
            if not self.active:
                return

            elapsed_s = time.monotonic() - self.started_monotonic
            if elapsed_s >= float(self.session["requested_duration_s"]):
                self._stop_locked("duration_complete")
                return

            row = {column: "" for column in RAW_OUTPUT_COLUMNS}
            row.update(
                {
                    "session_id": self.session["session_id"],
                    "sample_index": self.sample_index,
                    "received_at": iso_now(),
                    "elapsed_s": f"{elapsed_s:.6f}",
                    "label": self.session["label"],
                    "event_label": event_label_at(elapsed_s, self.session["label"], self.session["events"]),
                    "person_position": self.session["person_position"],
                    "environment": self.session["environment"],
                    "notes": self.session["notes"],
                    "raw_line": raw_line,
                }
            )
            for key, value in record.items():
                if key == "local_timestamp":
                    row["timestamp"] = value
                elif key in row:
                    row[key] = value
            self.writer.writerow(row)
            self.sample_index += 1
            self.session["samples_collected"] = self.sample_index
            if self.sample_index % 250 == 0:
                self.raw_fd.flush()

    def record_corrupt(self, raw_line, error):
        with self.lock:
            if not self.active:
                return

            elapsed_s = time.monotonic() - self.started_monotonic
            if elapsed_s >= float(self.session["requested_duration_s"]):
                self._stop_locked("duration_complete")
                return

            row = {column: "" for column in RAW_OUTPUT_COLUMNS}
            row.update(
                {
                    "session_id": self.session["session_id"],
                    "sample_index": self.sample_index,
                    "received_at": iso_now(),
                    "elapsed_s": f"{elapsed_s:.6f}",
                    "label": self.session["label"],
                    "event_label": event_label_at(elapsed_s, self.session["label"], self.session["events"]),
                    "person_position": self.session["person_position"],
                    "environment": self.session["environment"],
                    "notes": self.session["notes"],
                    "raw_line": raw_line,
                    "parse_error": error,
                }
            )
            self.writer.writerow(row)
            self.sample_index += 1
            self.corrupt_lines += 1
            self.session["samples_collected"] = self.sample_index
            self.session["corrupt_lines"] = self.corrupt_lines
            if self.sample_index % 250 == 0:
                self.raw_fd.flush()

    def snapshot(self):
        with self.lock:
            return self.snapshot_locked()

    def snapshot_locked(self):
        if not self.active:
            return {
                "active": False,
                "last_completed": self.last_completed,
                "presets": COLLECTION_PRESETS,
            }

        elapsed_s = time.monotonic() - self.started_monotonic
        duration_s = float(self.session["requested_duration_s"])
        remaining_s = max(0.0, duration_s - elapsed_s)
        return {
            "active": True,
            "session_id": self.session["session_id"],
            "label": self.session["label"],
            "started_at": self.session["started_at"],
            "event_label": event_label_at(elapsed_s, self.session["label"], self.session["events"]),
            "elapsed_s": round(elapsed_s, 3),
            "duration_s": duration_s,
            "remaining_s": round(remaining_s, 3),
            "progress": min(1.0, elapsed_s / duration_s) if duration_s else 0.0,
            "events": self.session["events"],
            "schedule": schedule_state_at(elapsed_s, self.session["label"], self.session["events"]),
            "samples_collected": self.sample_index,
            "corrupt_lines": self.corrupt_lines,
            "raw_file": self.session["raw_file"],
            "metadata_file": self.session["metadata_file"],
            "last_completed": self.last_completed,
            "presets": COLLECTION_PRESETS,
        }


class CsiState:
    def __init__(self, base_dir, inference, notifier, log_service=None, udp_port=5000):
        self.condition = threading.Condition()
        self.logs = log_service or NullLogService()
        self.recorder = CsiRecorder(base_dir, self.logs)
        self.inference = inference
        self.notifier = notifier
        self.latest = None
        self.latest_sensor = None
        self.seq = 0
        self.connected = False
        self.transport = "udp"
        self.network_port = int(udp_port)
        self.udp_port = int(udp_port)
        self.last_packet_monotonic = 0.0
        self.port = ""
        self.baud = 0
        self.packet_count = 0
        self.sensor_packet_count = 0
        self.parse_errors = 0
        self.serial_errors = []
        self.last_error = ""
        self.last_raw = ""
        self.last_logged_serial_error = ""
        self.last_serial_log_monotonic = 0.0

    def set_serial_config(self, port, baud):
        with self.condition:
            self.port = port
            self.baud = baud
            self.condition.notify_all()

    def set_connected(self, connected, error=""):
        log_event = None
        with self.condition:
            was_connected = self.connected
            self.connected = connected
            self.last_error = error
            if error:
                self.serial_errors.append({"time": time.time(), "error": error})
                self.serial_errors = self.serial_errors[-20:]
                now = time.monotonic()
                if error != self.last_logged_serial_error or now - self.last_serial_log_monotonic >= 10:
                    self.last_logged_serial_error = error
                    self.last_serial_log_monotonic = now
                    log_event = ("error", "serial", "Error de conexion serial.", {"port": self.port, "baud": self.baud}, error)
            elif connected and not was_connected:
                log_event = ("info", "serial", "Puerto serial conectado.", {"port": self.port, "baud": self.baud}, None)
            self.condition.notify_all()
        if log_event:
            level, category, message, context, detail = log_event
            if level == "error":
                self.logs.error(category, message, context={**context, "error": detail})
            else:
                self.logs.info(category, message, context=context)

    def publish(self, payload, raw):
        self.inference.add_sample(payload)
        self.notifier.update(self.inference.snapshot())
        with self.condition:
            self.connected = True
            self.last_packet_monotonic = time.monotonic()
            self.last_error = ""
            self.latest = payload
            self.last_raw = raw
            self.seq += 1
            self.packet_count += 1
            self.condition.notify_all()

    def publish_sensor(self, payload, raw):
        with self.condition:
            self.connected = True
            self.last_packet_monotonic = time.monotonic()
            self.last_error = ""
            self.latest_sensor = payload
            self.last_raw = raw
            self.seq += 1
            self.sensor_packet_count += 1
            self.condition.notify_all()

    def add_parse_error(self, raw, error):
        with self.condition:
            self.parse_errors += 1
            self.last_raw = raw
            self.last_error = error
            self.condition.notify_all()
        self.logs.warning(
            "parse",
            "Trama CSI no pudo ser interpretada.",
            context={"error": error, "raw": truncate_text(raw, 300)},
        )

    def snapshot(self):
        with self.condition:
            connected = self.connected and (
                time.monotonic() - self.last_packet_monotonic <= 3.0
            )
            snapshot = {
                "connected": connected,
                "transport": self.transport,
                "network_port": self.network_port,
                "port": self.port,
                "baud": self.baud,
                "packet_count": self.packet_count,
                "sensor_packet_count": self.sensor_packet_count,
                "parse_errors": self.parse_errors,
                "last_error": self.last_error,
                "last_raw": self.last_raw[-500:],
                "latest": self.latest,
                "latest_sensor": self.latest_sensor,
            }
        snapshot["collection"] = self.recorder.snapshot()
        snapshot["inference"] = self.inference.snapshot()
        snapshot["notifications"] = self.notifier.snapshot()
        snapshot["logs"] = self.logs.snapshot()
        return snapshot


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_csi_record(line):
    start = line.find("CSI_DATA")
    if start < 0:
        return None, None, None

    row = next(csv.reader(StringIO(line[start:])))
    if len(row) == len(CLASSIC_COLUMNS):
        record = dict(zip(CLASSIC_COLUMNS, row))
        format_name = "esp32"
    elif len(row) == len(C5C6_COLUMNS):
        record = dict(zip(C5C6_COLUMNS, row))
        format_name = "esp32_c5_c6"
    else:
        raise ValueError(f"unexpected column count: {len(row)}")

    raw = json.loads(record["data"])
    expected_len = to_int(record["len"], len(raw))
    if expected_len != len(raw):
        raise ValueError(f"CSI len mismatch: header={expected_len}, data={len(raw)}")

    if len(raw) < 2:
        raise ValueError("CSI vector is empty")
    if len(raw) % 2:
        raw = raw[:-1]

    return record, format_name, raw


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def build_payload(record, format_name, raw):
    expected_len = to_int(record["len"], len(raw))

    imag = raw[0::2]
    real = raw[1::2]
    amplitude = [math.hypot(r, i) for r, i in zip(real, imag)]
    phase = [math.atan2(i, r) for r, i in zip(real, imag)]

    rssi = to_int(record.get("rssi"))
    noise_floor = to_int(record.get("noise_floor"))
    snr = rssi - noise_floor if noise_floor else None

    return {
        "format": format_name,
        "id": to_int(record.get("id")),
        "mac": record.get("mac", ""),
        "rssi": rssi,
        "noise_floor": noise_floor,
        "snr": snr,
        "rate": to_int(record.get("rate")),
        "channel": to_int(record.get("channel")),
        "local_timestamp": to_int(record.get("local_timestamp")),
        "sig_len": to_int(record.get("sig_len")),
        "rx_state": to_int(record.get("rx_state")),
        "len": expected_len,
        "subcarriers": len(amplitude),
        "first_word": to_int(record.get("first_word")),
        "fft_gain": to_int(record.get("fft_gain")) if "fft_gain" in record else None,
        "agc_gain": to_int(record.get("agc_gain")) if "agc_gain" in record else None,
        "amplitude": amplitude,
        "phase": phase,
        "time": time.time(),
    }


def parse_csi_line(line):
    record, format_name, raw = parse_csi_record(line)
    if record is None:
        return None
    return build_payload(record, format_name, raw)


def parse_bool_int(record, key):
    return bool(to_int(record.get(key), 0))


def parse_sensor_line(line):
    start = line.find("SENSOR_DATA")
    if start < 0:
        return None

    row = next(csv.reader(StringIO(line[start:])))
    if len(row) != len(SENSOR_COLUMNS):
        raise ValueError(f"unexpected SENSOR_DATA column count: {len(row)}")

    record = dict(zip(SENSOR_COLUMNS, row))
    temp_c_x10 = to_int(record.get("temp_c_x10"))
    alerts = {
        "sound": parse_bool_int(record, "alert_sound"),
        "bpm_high": parse_bool_int(record, "alert_bpm_high"),
        "temp_high": parse_bool_int(record, "alert_temp_high"),
        "temp_low": parse_bool_int(record, "alert_temp_low"),
    }

    return {
        "seq": to_int(record.get("seq")),
        "temperature_c": round(temp_c_x10 / 10.0, 1),
        "temperature_c_x10": temp_c_x10,
        "bpm": to_int(record.get("bpm")),
        "sound_detected": parse_bool_int(record, "sound_detected"),
        "alerts": alerts,
        "has_alert": any(alerts.values()),
        "buzzer_interval_ms": to_int(record.get("buzzer_interval_ms")),
        "buzzer_on": parse_bool_int(record, "buzzer_on"),
        "uptime_ms": to_int(record.get("uptime_ms")),
        "time": time.time(),
    }


def _canonical_csi_line(record, format_name, raw):
    data_text = json.dumps(raw, separators=(",", ":"))
    if format_name == "esp32_c5_c6":
        values = [
            "CSI_DATA",
            record["id"],
            record["mac"],
            record["rssi"],
            record["rate"],
            record["noise_floor"],
            record.get("fft_gain", 0),
            record.get("agc_gain", 0),
            record["channel"],
            record["local_timestamp"],
            record["sig_len"],
            record.get("rx_state", 0),
            record["len"],
            record["first_word"],
        ]
    else:
        values = [
            "CSI_DATA",
            record["id"],
            record["mac"],
            record["rssi"],
            record["rate"],
            record.get("sig_mode", 1),
            record.get("mcs", 0),
            record.get("bandwidth", 1),
            record.get("smoothing", 0),
            record.get("not_sounding", 0),
            record.get("aggregation", 0),
            record.get("stbc", 0),
            record.get("fec_coding", 0),
            record.get("sgi", 0),
            record["noise_floor"],
            record.get("ampdu_cnt", 0),
            record["channel"],
            record.get("secondary_channel", 0),
            record["local_timestamp"],
            record.get("ant", 0),
            record["sig_len"],
            record.get("rx_state", 0),
            record["len"],
            record["first_word"],
        ]
    return ",".join(str(value) for value in values) + ',"' + data_text + '"'


def decode_udp_batch(body):
    if len(body) < WIRE_BATCH_HEADER.size:
        raise ValueError("UDP body demasiado corto")
    if len(body) > WIRE_MAX_BODY:
        raise ValueError("UDP body demasiado grande")

    magic, version, frame_count, _reserved = WIRE_BATCH_HEADER.unpack_from(body)
    if magic != WIRE_BATCH_MAGIC:
        raise ValueError("magic UDP CSI invalido")
    if version != WIRE_BATCH_VERSION:
        raise ValueError(f"version UDP CSI no soportada: {version}")
    if frame_count < 1 or frame_count > 8:
        raise ValueError(f"cantidad de frames invalida: {frame_count}")

    offset = WIRE_BATCH_HEADER.size
    decoded = []
    for _index in range(frame_count):
        if offset + WIRE_FRAME_HEADER.size > len(body):
            raise ValueError("frame UDP incompleto")
        frame_type, _flags, frame_len, seq = WIRE_FRAME_HEADER.unpack_from(body, offset)
        if frame_len < WIRE_FRAME_HEADER.size or offset + frame_len > len(body):
            raise ValueError("longitud de frame UDP invalida")
        frame_start = offset + WIRE_FRAME_HEADER.size
        frame_end = offset + frame_len

        if frame_type == WIRE_FRAME_CSI:
            if frame_start + WIRE_CSI_BODY.size > frame_end:
                raise ValueError("CSI UDP incompleto")
            (
                timestamp,
                rssi,
                noise_floor,
                rate,
                channel,
                sig_len,
                csi_len,
                first_word,
                format_id,
                mac_bytes,
            ) = WIRE_CSI_BODY.unpack_from(body, frame_start)
            data_start = frame_start + WIRE_CSI_BODY.size
            data_size = csi_len * 2
            if data_start + data_size > frame_end:
                raise ValueError("vector CSI UDP incompleto")
            raw = list(struct.unpack_from(f"<{csi_len}h", body, data_start))
            mac = ":".join(f"{value:02x}" for value in mac_bytes)
            format_name = "esp32_c5_c6" if format_id else "esp32"
            record = {
                "type": "CSI_DATA",
                "id": seq,
                "mac": mac,
                "rssi": rssi,
                "rate": rate,
                "noise_floor": noise_floor,
                "channel": channel,
                "local_timestamp": timestamp,
                "sig_len": sig_len,
                "rx_state": 0,
                "len": csi_len,
                "first_word": first_word,
                "data": json.dumps(raw, separators=(",", ":")),
            }
            if format_name == "esp32_c5_c6":
                record.update({"fft_gain": 0, "agc_gain": 0})
            else:
                record.update(
                    {
                        "sig_mode": 1,
                        "mcs": 0,
                        "bandwidth": 1,
                        "smoothing": 0,
                        "not_sounding": 0,
                        "aggregation": 0,
                        "stbc": 0,
                        "fec_coding": 0,
                        "sgi": 0,
                        "ampdu_cnt": 0,
                        "secondary_channel": 0,
                        "ant": 0,
                    }
                )
            raw_line = _canonical_csi_line(record, format_name, raw)
            payload = build_payload(record, format_name, raw)
            decoded.append(("csi", payload, raw_line, record))
        elif frame_type == WIRE_FRAME_SENSOR:
            if frame_start + WIRE_SENSOR_BODY.size > frame_end:
                raise ValueError("SENSOR_DATA UDP incompleto")
            sensor_values = WIRE_SENSOR_BODY.unpack_from(body, frame_start)
            (
                magic_sensor,
                sensor_version,
                sensor_size,
                sensor_seq,
                temp_c_x10,
                bpm,
                sound_detected,
                alert_flags,
                buzzer_interval_ms,
                buzzer_on,
                _reserved,
                uptime_ms,
            ) = sensor_values
            if magic_sensor != 0x534E4553 or sensor_version != 1 or sensor_size != WIRE_SENSOR_BODY.size:
                raise ValueError("SENSOR_DATA UDP invalido")
            sensor_line = (
                "SENSOR_DATA,{},{},{},{},{},{},{},{},{},{},{}".format(
                    sensor_seq,
                    temp_c_x10,
                    bpm,
                    sound_detected,
                    1 if alert_flags & 1 else 0,
                    1 if alert_flags & 2 else 0,
                    1 if alert_flags & 4 else 0,
                    1 if alert_flags & 8 else 0,
                    buzzer_interval_ms,
                    buzzer_on,
                    uptime_ms,
                )
            )
            decoded.append(("sensor", parse_sensor_line(sensor_line), sensor_line, None))
        else:
            raise ValueError(f"tipo de frame UDP desconocido: {frame_type}")
        offset = frame_end

    if offset != len(body):
        raise ValueError("bytes extra al final del body UDP")
    return decoded


def publish_decoded_batch(state, decoded):
    csi_count = 0
    sensor_count = 0
    for kind, payload, raw_line, record in decoded:
        if kind == "csi":
            state.recorder.record_sample(raw_line, record)
            state.publish(payload, raw_line)
            csi_count += 1
        else:
            state.publish_sensor(payload, raw_line)
            sensor_count += 1
    return csi_count, sensor_count


class DashboardHandler(SimpleHTTPRequestHandler):
    state = None

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == "/events":
            self.handle_events()
            return

        if self.path == "/api/status":
            self.send_json(self.state.snapshot())
            return

        if self.path == "/api/logs/status":
            self.send_json({"logs": self.state.logs.snapshot()})
            return

        if self.path == "/api/collection/presets":
            self.send_json({"presets": COLLECTION_PRESETS})
            return

        if self.path in {"/usuario", "/familia"}:
            self.path = "/user.html"

        if self.path == "/":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/ingest":
            try:
                body = self.read_binary_body()
                decoded = decode_udp_batch(body)
                csi_count, sensor_count = publish_decoded_batch(self.state, decoded)
                self.send_json(
                    {
                        "ok": True,
                        "frames": len(decoded),
                        "csi": csi_count,
                        "sensors": sensor_count,
                    }
                )
            except Exception as exc:
                self.state.add_parse_error("HTTP /api/ingest", str(exc))
                self.state.logs.error("network", "No se pudo decodificar ingesta HTTP.", exc=exc)
                self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/reset":
            with self.state.condition:
                self.state.packet_count = 0
                self.state.sensor_packet_count = 0
                self.state.parse_errors = 0
                self.state.latest = None
                self.state.latest_sensor = None
                self.state.seq += 1
                self.state.condition.notify_all()
            self.send_json({"ok": True})
            return

        if self.path == "/api/collection/start":
            try:
                payload = self.read_json_body()
                collection = self.state.recorder.start(payload)
                with self.state.condition:
                    self.state.seq += 1
                    self.state.condition.notify_all()
                self.send_json({"ok": True, "collection": collection})
            except Exception as exc:
                self.state.logs.error("http", "No se pudo iniciar la recoleccion.", exc=exc)
                self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/collection/stop":
            completed = self.state.recorder.stop("manual")
            with self.state.condition:
                self.state.seq += 1
                self.state.condition.notify_all()
            self.send_json({"ok": True, "completed": completed})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def read_binary_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("HTTP body vacio")
        if content_length > WIRE_MAX_BODY:
            raise ValueError("HTTP body supera el limite permitido")
        return self.rfile.read(content_length)

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_events(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_seq = -1
        while True:
            with self.state.condition:
                self.state.condition.wait_for(
                    lambda: self.state.seq != last_seq,
                    timeout=10,
                )
                if self.state.seq == last_seq:
                    event = b": keepalive\n\n"
                else:
                    last_seq = self.state.seq
                    event = (
                        "data: "
                        + json.dumps(self.state.snapshot(), separators=(",", ":"))
                        + "\n\n"
                    ).encode("utf-8")

            try:
                self.wfile.write(event)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return


class DashboardHTTPServer(ThreadingHTTPServer):
    """HTTP server that treats a browser disconnect as an expected event."""

    def handle_error(self, request, client_address):
        exc_type, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class UdpIngestServer:
    """Receives CSI wire datagrams while the HTTP server serves the dashboard."""

    def __init__(self, host, port, state):
        self.host = host
        self.port = int(port)
        self.state = state
        self.sock = None
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(1.0)
        self.thread = threading.Thread(target=self.run, name="csi-udp", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                body, address = self.sock.recvfrom(WIRE_MAX_BODY)
            except socket.timeout:
                continue
            except OSError:
                return

            try:
                decoded = decode_udp_batch(body)
                publish_decoded_batch(self.state, decoded)
            except Exception as exc:
                source = f"UDP {address[0]}:{address[1]}"
                self.state.add_parse_error(source, str(exc))
                self.state.logs.error(
                    "network",
                    "No se pudo decodificar datagrama UDP.",
                    context={"source": source, "error": str(exc)},
                )

    def stop(self):
        self.stop_event.set()
        if self.sock is not None:
            self.sock.close()
        if self.thread is not None:
            self.thread.join(timeout=2)


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    loaded_env = load_env_file(Path(root) / LOCAL_ENV_FILENAME)

    parser = argparse.ArgumentParser(description="Local ESP CSI web dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--udp-host", default="0.0.0.0")
    parser.add_argument("--udp-port", type=int, default=5000)
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--log-dir", default=os.environ.get("CSI_WEB_LOG_DIR", str(Path(root) / DEFAULT_LOG_DIRNAME)))
    parser.add_argument("--inference-window-s", type=float, default=20.0)
    parser.add_argument("--inference-step-s", type=float, default=5.0)
    parser.add_argument("--inference-target-hz", type=float, default=50.0)
    parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--telegram-alert-interval-s", type=float, default=env_float("TELEGRAM_ALERT_INTERVAL_S", 1.0))
    parser.add_argument(
        "--telegram-alert-message",
        default=os.environ.get("TELEGRAM_ALERT_MESSAGE", DEFAULT_TELEGRAM_ALERT_MESSAGE),
    )
    args = parser.parse_args()

    base_dir = Path(root).parent
    static_dir = os.path.join(root, "static")
    logs = LogService(args.log_dir)
    logs.info(
        "startup",
        "Servidor CSI inicializando.",
        context={
            "host": args.host,
            "http_port": args.http_port,
            "udp_host": args.udp_host,
            "udp_port": args.udp_port,
            "model_path": args.model_path,
            "telegram_configured": bool(args.telegram_bot_token and args.telegram_chat_id),
        },
    )
    inference = RealtimeApneaInference(
        model_path=args.model_path,
        window_s=args.inference_window_s,
        step_s=args.inference_step_s,
        target_hz=args.inference_target_hz,
        log_service=logs,
    )
    notifier = TelegramNotifier(
        bot_token=args.telegram_bot_token,
        chat_id=args.telegram_chat_id,
        interval_s=args.telegram_alert_interval_s,
        message=args.telegram_alert_message,
        log_service=logs,
    )
    state = CsiState(base_dir, inference, notifier, logs, udp_port=args.udp_port)

    DashboardHandler.state = state

    def handler(*handler_args, **handler_kwargs):
        return DashboardHandler(
            *handler_args,
            directory=static_dir,
            **handler_kwargs,
        )

    httpd = DashboardHTTPServer((args.host, args.http_port), handler)
    udp_server = UdpIngestServer(args.udp_host, args.udp_port, state)
    udp_server.start()
    print(f"CSI dashboard: http://{args.host}:{args.http_port}")
    print(f"CSI ingest: UDP {args.udp_host}:{args.udp_port}")
    print(f"Model: {args.model_path}")
    print(f"Local env: {'loaded' if loaded_env else 'not found'} ({LOCAL_ENV_FILENAME})")
    print(f"Telegram alerts: {'enabled' if notifier.enabled else 'disabled'}")
    print(f"Logs: {logs.log_dir}")
    logs.info("startup", "Servidor HTTP iniciado.", context={"url": f"http://{args.host}:{args.http_port}"})
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CSI dashboard")
        logs.info("startup", "Servidor detenido por teclado.")
    finally:
        state.recorder.stop("server_shutdown")
        udp_server.stop()
        httpd.server_close()
        logs.info("startup", "Servidor cerrado.")


if __name__ == "__main__":
    main()

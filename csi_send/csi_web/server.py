#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "Missing pyserial. Run with the ESP-IDF Python, for example:\n"
        "/Users/jandonyggarofalo/.espressif/tools/python/v5.5.4/venv/bin/python3 "
        "csi_web/server.py --serial-port /dev/cu.usbserial-0001"
    ) from exc


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
        "description": "Respiracion normal y apnea simulada por ciclos.",
        "duration_s": 240,
        "person_position": "between_tx_rx",
        "environment": "off",
        "events": [
            {"start_s": 0, "end_s": 30, "label": "breathing"},
            {"start_s": 30, "end_s": 50, "label": "hold_breath"},
            {"start_s": 50, "end_s": 80, "label": "breathing"},
            {"start_s": 80, "end_s": 100, "label": "hold_breath"},
            {"start_s": 100, "end_s": 130, "label": "breathing"},
            {"start_s": 130, "end_s": 150, "label": "hold_breath"},
            {"start_s": 150, "end_s": 180, "label": "breathing"},
            {"start_s": 180, "end_s": 200, "label": "hold_breath"},
            {"start_s": 200, "end_s": 240, "label": "breathing"},
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


def event_label_at(elapsed_s, label, events):
    for event in events:
        if event["start_s"] <= elapsed_s < event["end_s"]:
            return event["label"]
    return label


class CsiRecorder:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
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
            "event_label": event_label_at(elapsed_s, self.session["label"], self.session["events"]),
            "elapsed_s": round(elapsed_s, 3),
            "duration_s": duration_s,
            "remaining_s": round(remaining_s, 3),
            "progress": min(1.0, elapsed_s / duration_s) if duration_s else 0.0,
            "samples_collected": self.sample_index,
            "corrupt_lines": self.corrupt_lines,
            "raw_file": self.session["raw_file"],
            "metadata_file": self.session["metadata_file"],
            "last_completed": self.last_completed,
            "presets": COLLECTION_PRESETS,
        }


class CsiState:
    def __init__(self, base_dir):
        self.condition = threading.Condition()
        self.recorder = CsiRecorder(base_dir)
        self.latest = None
        self.seq = 0
        self.connected = False
        self.port = ""
        self.baud = 0
        self.packet_count = 0
        self.parse_errors = 0
        self.serial_errors = []
        self.last_error = ""
        self.last_raw = ""

    def set_serial_config(self, port, baud):
        with self.condition:
            self.port = port
            self.baud = baud
            self.condition.notify_all()

    def set_connected(self, connected, error=""):
        with self.condition:
            self.connected = connected
            self.last_error = error
            if error:
                self.serial_errors.append({"time": time.time(), "error": error})
                self.serial_errors = self.serial_errors[-20:]
            self.condition.notify_all()

    def publish(self, payload, raw):
        with self.condition:
            self.latest = payload
            self.last_raw = raw
            self.seq += 1
            self.packet_count += 1
            self.condition.notify_all()

    def add_parse_error(self, raw, error):
        with self.condition:
            self.parse_errors += 1
            self.last_raw = raw
            self.last_error = error
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            snapshot = {
                "connected": self.connected,
                "port": self.port,
                "baud": self.baud,
                "packet_count": self.packet_count,
                "parse_errors": self.parse_errors,
                "last_error": self.last_error,
                "last_raw": self.last_raw[-500:],
                "latest": self.latest,
            }
        snapshot["collection"] = self.recorder.snapshot()
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


def serial_reader(state, serial_port, baud):
    state.set_serial_config(serial_port, baud)

    while True:
        try:
            with serial.Serial(serial_port, baudrate=baud, timeout=1) as dev:
                state.set_connected(True, "")
                while True:
                    raw_bytes = dev.readline()
                    if not raw_bytes:
                        continue

                    line = raw_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    try:
                        record, format_name, raw = parse_csi_record(line)
                        if record is None:
                            continue
                        payload = build_payload(record, format_name, raw)
                    except Exception as exc:
                        state.add_parse_error(line, str(exc))
                        state.recorder.record_corrupt(line, str(exc))
                        continue

                    state.recorder.record_sample(line, record)
                    state.publish(payload, line)
        except Exception as exc:
            state.set_connected(False, str(exc))
            time.sleep(1.5)


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

        if self.path == "/api/collection/presets":
            self.send_json({"presets": COLLECTION_PRESETS})
            return

        if self.path == "/":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/reset":
            with self.state.condition:
                self.state.packet_count = 0
                self.state.parse_errors = 0
                self.state.latest = None
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


def main():
    parser = argparse.ArgumentParser(description="Local ESP CSI web dashboard")
    parser.add_argument("--serial-port", default="/dev/cu.usbserial-0001")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8080)
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    base_dir = Path(root).parent
    static_dir = os.path.join(root, "static")
    state = CsiState(base_dir)

    reader = threading.Thread(
        target=serial_reader,
        args=(state, args.serial_port, args.baud),
        daemon=True,
    )
    reader.start()

    DashboardHandler.state = state

    def handler(*handler_args, **handler_kwargs):
        return DashboardHandler(
            *handler_args,
            directory=static_dir,
            **handler_kwargs,
        )

    httpd = ThreadingHTTPServer((args.host, args.http_port), handler)
    print(f"CSI dashboard: http://{args.host}:{args.http_port}")
    print(f"Serial: {args.serial_port} @ {args.baud}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CSI dashboard")
    finally:
        state.recorder.stop("server_shutdown")
        httpd.server_close()


if __name__ == "__main__":
    main()

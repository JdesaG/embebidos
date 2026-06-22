#!/usr/bin/env python3
"""
Capture one ESP32 CSI session from serial and store traceable raw data.

Example:
  python csi_ml/collect_csi_session.py \
    --port /dev/cu.usbserial-4 \
    --baudrate 921600 \
    --label breathing_off \
    --duration 180 \
    --session-id session_001 \
    --tx-position "desk_left" \
    --rx-position "desk_right" \
    --distance-cm 180 \
    --height-cm 90 \
    --person-position "between_tx_rx" \
    --environment "ac_off" \
    --notes "quiet room, normal breathing"

Hold-breath events:
  python csi_ml/collect_csi_session.py ... \
    --label hold_breath_off \
    --events "0:30:breathing,30:50:hold_breath,50:80:breathing"
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from io import StringIO
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
    "timestamp",
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
    "timestamp",
    "sig_len",
    "rx_state",
    "len",
    "first_word",
    "data",
]

OUTPUT_COLUMNS = [
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


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ensure_data_dirs(base_dir: Path) -> dict[str, Path]:
    paths = {
        "raw": base_dir / "data" / "raw",
        "metadata": base_dir / "data" / "metadata",
        "processed": base_dir / "data" / "processed",
        "reports": base_dir / "data" / "reports",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned.strip("_") or "unknown"


def prompt_if_missing(value, prompt: str, default: str | None = None):
    if value not in (None, ""):
        return value
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def parse_events(events_text: str | None, events_file: str | None) -> list[dict]:
    if events_file:
        with open(events_file, "r", encoding="utf-8") as fd:
            data = json.load(fd)
        events = data.get("events", data) if isinstance(data, dict) else data
    elif events_text:
        events = []
        for chunk in events_text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) != 3:
                raise ValueError(f"Invalid event '{chunk}'. Use start:end:label")
            start_s, end_s, label = parts
            events.append({"start_s": float(start_s), "end_s": float(end_s), "label": label})
    else:
        events = []

    normalized = []
    for event in events:
        start_s = float(event["start_s"])
        end_s = float(event["end_s"])
        if end_s <= start_s:
            raise ValueError(f"Invalid event range: {event}")
        normalized.append({"start_s": start_s, "end_s": end_s, "label": str(event["label"])})
    return sorted(normalized, key=lambda item: item["start_s"])


def event_label_at(elapsed_s: float, label: str, events: list[dict]) -> str:
    for event in events:
        if event["start_s"] <= elapsed_s < event["end_s"]:
            return event["label"]
    return label


def parse_csi_line(line: str) -> tuple[dict, str]:
    start = line.find("CSI_DATA")
    if start < 0:
        raise ValueError("line does not contain CSI_DATA")
    row = next(csv.reader(StringIO(line[start:])))
    if len(row) == len(CLASSIC_COLUMNS):
        return dict(zip(CLASSIC_COLUMNS, row)), "esp32"
    if len(row) == len(C5C6_COLUMNS):
        return dict(zip(C5C6_COLUMNS, row)), "esp32_c5_c6"
    raise ValueError(f"unexpected column count: {len(row)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect one labeled ESP32 CSI session.")
    parser.add_argument("--port", help="Receiver serial port, e.g. /dev/cu.usbserial-4")
    parser.add_argument("--baudrate", type=int, default=921600)
    parser.add_argument("--label", help="General session label, e.g. breathing_off")
    parser.add_argument("--duration", type=float, help="Capture duration in seconds")
    parser.add_argument("--session-id", help="Stable session id, e.g. session_001")
    parser.add_argument("--tx-position", default="")
    parser.add_argument("--rx-position", default="")
    parser.add_argument("--distance-cm", type=float)
    parser.add_argument("--height-cm", type=float)
    parser.add_argument("--person-position", default="")
    parser.add_argument("--environment", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--events", help="Comma list start:end:label, e.g. 0:30:breathing,30:50:hold_breath")
    parser.add_argument("--events-file", help="JSON file with events.")
    parser.add_argument("--base-dir", default=".", help="Project base directory. Defaults to current directory.")
    parser.add_argument("--interactive", action="store_true", help="Ask for missing values.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.interactive:
        args.port = prompt_if_missing(args.port, "Serial port", "/dev/cu.usbserial-4")
        args.label = prompt_if_missing(args.label, "Label", "empty_off")
        args.duration = float(prompt_if_missing(args.duration, "Duration seconds", "180"))
        args.session_id = prompt_if_missing(args.session_id, "Session ID", "session_001")
        args.tx_position = prompt_if_missing(args.tx_position, "TX position", "")
        args.rx_position = prompt_if_missing(args.rx_position, "RX position", "")
        args.person_position = prompt_if_missing(args.person_position, "Person position", "")
        args.environment = prompt_if_missing(args.environment, "Environment", "")
        args.notes = prompt_if_missing(args.notes, "Notes", "")

    missing = []
    for key in ("port", "label", "duration", "session_id"):
        if getattr(args, key.replace("-", "_"), None) in (None, ""):
            missing.append("--" + key.replace("_", "-"))
    if missing:
        print(f"Missing required values: {', '.join(missing)}. Use --interactive if helpful.", file=sys.stderr)
        return 2

    try:
        import serial
    except ImportError:
        print("pyserial is missing. Try the ESP-IDF Python or install pyserial.", file=sys.stderr)
        return 2

    base_dir = Path(args.base_dir).resolve()
    dirs = ensure_data_dirs(base_dir)
    events = parse_events(args.events, args.events_file)

    session_label = safe_name(args.label)
    session_id = safe_name(args.session_id)
    file_stem = f"{session_id}_{session_label}"
    raw_file = dirs["raw"] / f"{file_stem}.csv"
    metadata_file = dirs["metadata"] / f"{file_stem}.json"

    started_at = iso_now()
    start_monotonic = time.monotonic()
    sample_index = 0
    corrupt_lines = 0
    first_packet_id = None
    last_packet_id = None

    print(f"Writing raw session to {raw_file}")
    print(f"Duration: {args.duration:.1f}s | Port: {args.port} @ {args.baudrate}")

    try:
        serial_dev = serial.Serial(args.port, baudrate=args.baudrate, timeout=1)
    except Exception as exc:
        print(f"Could not open serial port {args.port}: {exc}", file=sys.stderr)
        return 1

    with serial_dev, open(raw_file, "w", newline="", encoding="utf-8") as raw_fd:
        writer = csv.DictWriter(raw_fd, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()

        while True:
            elapsed_s = time.monotonic() - start_monotonic
            if elapsed_s >= args.duration:
                break

            try:
                raw_bytes = serial_dev.readline()
            except Exception as exc:
                print(f"Serial read error: {exc}", file=sys.stderr)
                break
            if not raw_bytes:
                continue

            raw_line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if "CSI_DATA" not in raw_line:
                continue

            row = {column: "" for column in OUTPUT_COLUMNS}
            row.update(
                {
                    "session_id": session_id,
                    "sample_index": sample_index,
                    "received_at": iso_now(),
                    "elapsed_s": f"{elapsed_s:.6f}",
                    "label": args.label,
                    "event_label": event_label_at(elapsed_s, args.label, events),
                    "person_position": args.person_position,
                    "environment": args.environment,
                    "notes": args.notes,
                    "raw_line": raw_line,
                }
            )

            try:
                parsed, _format_name = parse_csi_line(raw_line)
                row.update({key: parsed.get(key, "") for key in parsed})
                packet_id = int(parsed.get("id", ""))
                first_packet_id = packet_id if first_packet_id is None else first_packet_id
                last_packet_id = packet_id
            except Exception as exc:
                corrupt_lines += 1
                row["parse_error"] = str(exc)

            writer.writerow(row)
            sample_index += 1

            if sample_index % 500 == 0:
                rate = sample_index / max(elapsed_s, 1e-6)
                print(f"{sample_index} samples | {elapsed_s:.1f}s | {rate:.1f} Hz", flush=True)

    ended_at = iso_now()
    actual_duration_s = max(time.monotonic() - start_monotonic, 1e-6)
    sample_rate = sample_index / actual_duration_s

    metadata = {
        "session_id": session_id,
        "label": args.label,
        "duration_s": round(actual_duration_s, 3),
        "requested_duration_s": args.duration,
        "sample_rate_estimated_hz": round(sample_rate, 3),
        "samples_collected": sample_index,
        "corrupt_lines": corrupt_lines,
        "first_packet_id": first_packet_id,
        "last_packet_id": last_packet_id,
        "tx_position": args.tx_position,
        "rx_position": args.rx_position,
        "distance_cm": args.distance_cm,
        "height_cm": args.height_cm,
        "environment": args.environment,
        "person_position": args.person_position,
        "notes": args.notes,
        "events": events,
        "started_at": started_at,
        "ended_at": ended_at,
        "raw_file": str(raw_file.relative_to(base_dir)),
        "baudrate": args.baudrate,
        "port": args.port,
    }

    with open(metadata_file, "w", encoding="utf-8") as metadata_fd:
        json.dump(metadata, metadata_fd, indent=2, ensure_ascii=False)

    print(f"Done. Samples: {sample_index}, estimated rate: {sample_rate:.2f} Hz")
    print(f"Metadata: {metadata_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

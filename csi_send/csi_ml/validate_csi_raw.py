#!/usr/bin/env python3
"""
Validate one or more raw CSI session CSV files.

Examples:
  python csi_ml/validate_csi_raw.py
  python csi_ml/validate_csi_raw.py data/raw/session_001_empty_off.csv
  python csi_ml/validate_csi_raw.py --normal-rx-states 0,1 --max-loss-pct 10
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
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


def parse_csi_line(line: str) -> dict:
    start = line.find("CSI_DATA")
    if start < 0:
        raise ValueError("line does not contain CSI_DATA")
    row = next(csv.reader(StringIO(line[start:])))
    if len(row) == len(CLASSIC_COLUMNS):
        return dict(zip(CLASSIC_COLUMNS, row))
    if len(row) == len(C5C6_COLUMNS):
        return dict(zip(C5C6_COLUMNS, row))
    raise ValueError(f"unexpected column count: {len(row)}")


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    values_sorted = sorted(values)
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 3),
        "stdev": round(statistics.pstdev(values), 3),
        "p10": round(values_sorted[int(0.10 * (len(values_sorted) - 1))], 3),
        "p50": round(statistics.median(values), 3),
        "p90": round(values_sorted[int(0.90 * (len(values_sorted) - 1))], 3),
    }


def iter_session_rows(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fd:
        sample = fd.read(4096)
        fd.seek(0)
        if sample.startswith("session_id,") or "raw_line" in sample.splitlines()[0]:
            for row in csv.DictReader(fd):
                yield row, row.get("raw_line", "")
        else:
            for index, line in enumerate(fd):
                yield {"sample_index": index, "raw_line": line.rstrip("\r\n")}, line.rstrip("\r\n")


def estimate_losses(ids: list[int]) -> tuple[int, int, float]:
    if len(ids) < 2:
        return 0, len(ids), 0.0
    lost = 0
    expected = 1
    prev = ids[0]
    for current in ids[1:]:
        if current > prev:
            gap = current - prev
            expected += gap
            if gap > 1:
                lost += gap - 1
        else:
            expected += 1
        prev = current
    loss_pct = 100.0 * lost / max(expected, 1)
    return lost, expected, loss_pct


def validate_file(path: Path, reports_dir: Path, normal_rx_states: set[int], max_loss_pct: float) -> dict:
    total_rows = 0
    valid_rows = 0
    corrupt_lines = 0
    len_mismatch = 0
    rx_state_abnormal = 0
    ids = []
    rssi_values = []
    channels = set()
    elapsed_values = []

    for row, raw_line in iter_session_rows(path):
        total_rows += 1
        parsed = None
        parse_error = row.get("parse_error") or ""

        if raw_line:
            try:
                parsed = parse_csi_line(raw_line)
            except Exception:
                parse_error = parse_error or "could not parse raw_line"
        elif row.get("type") == "CSI_DATA":
            parsed = row
        else:
            parse_error = parse_error or "missing raw_line"

        if parse_error and not parsed:
            corrupt_lines += 1
            continue

        valid_rows += 1
        packet_id = to_int(parsed.get("id"))
        if packet_id is not None:
            ids.append(packet_id)
        rssi = to_int(parsed.get("rssi"))
        if rssi is not None:
            rssi_values.append(rssi)
        channel = to_int(parsed.get("channel"))
        if channel is not None:
            channels.add(channel)
        csi_len = to_int(parsed.get("len"))
        if csi_len != 384:
            len_mismatch += 1
        rx_state = to_int(parsed.get("rx_state"))
        if rx_state is not None and rx_state not in normal_rx_states:
            rx_state_abnormal += 1
        try:
            if row.get("elapsed_s") not in (None, ""):
                elapsed_values.append(float(row["elapsed_s"]))
        except ValueError:
            pass

    lost_packets, expected_packets, packet_loss_pct = estimate_losses(ids)
    if len(elapsed_values) >= 2:
        duration_estimated_s = max(elapsed_values) - min(elapsed_values)
        sample_rate_hz = valid_rows / duration_estimated_s if duration_estimated_s > 0 else None
    elif len(ids) >= 2:
        duration_estimated_s = None
        sample_rate_hz = None
    else:
        duration_estimated_s = None
        sample_rate_hz = None

    quality = "bad_for_fine_training" if packet_loss_pct > max_loss_pct else "ok"
    report = {
        "source_file": str(path),
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_lines": total_rows,
        "total_samples": valid_rows,
        "duration_estimated_s": round(duration_estimated_s, 3) if duration_estimated_s is not None else None,
        "sample_rate_avg_hz": round(sample_rate_hz, 3) if sample_rate_hz is not None else None,
        "lost_packets_estimated": lost_packets,
        "expected_packets_estimated": expected_packets,
        "packet_loss_pct": round(packet_loss_pct, 3),
        "corrupt_lines": corrupt_lines,
        "len_not_384": len_mismatch,
        "rx_state_abnormal": rx_state_abnormal,
        "rssi_distribution": stats(rssi_values),
        "channels_detected": sorted(channels),
        "quality": quality,
        "bad_for_fine_training": quality != "ok",
        "rules": {
            "normal_rx_states": sorted(normal_rx_states),
            "max_loss_pct": max_loss_pct,
            "expected_len": 384,
        },
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / f"{path.stem}_validation.json"
    with open(report_file, "w", encoding="utf-8") as fd:
        json.dump(report, fd, indent=2, ensure_ascii=False)
    report["report_file"] = str(report_file)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate raw ESP32 CSI session files.")
    parser.add_argument("files", nargs="*", help="Raw CSV files. Defaults to data/raw/*.csv")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--normal-rx-states", default="0,1")
    parser.add_argument("--max-loss-pct", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    raw_dir = base_dir / "data" / "raw"
    reports_dir = base_dir / "data" / "reports"
    files = [Path(item) for item in args.files] if args.files else sorted(raw_dir.glob("*.csv"))
    if not files:
        print(f"No raw CSV files found in {raw_dir}")
        return 1

    normal_rx_states = {int(item.strip()) for item in args.normal_rx_states.split(",") if item.strip()}
    all_reports = []
    for path in files:
        report = validate_file(path, reports_dir, normal_rx_states, args.max_loss_pct)
        all_reports.append(report)
        print("\n" + path.name)
        print(f"  samples: {report['total_samples']} | corrupt: {report['corrupt_lines']}")
        print(f"  loss: {report['packet_loss_pct']}% | len!=384: {report['len_not_384']} | rx abnormal: {report['rx_state_abnormal']}")
        print(f"  sample_rate: {report['sample_rate_avg_hz']} Hz | channels: {report['channels_detected']}")
        print(f"  RSSI: {report['rssi_distribution']}")
        print(f"  quality: {report['quality']}")
        print(f"  report: {report['report_file']}")

    bad_count = sum(1 for report in all_reports if report["bad_for_fine_training"])
    print(f"\nValidated {len(all_reports)} session(s). Bad for fine training: {bad_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

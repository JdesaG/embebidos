#!/usr/bin/env python3
"""
Convert raw ESP32 CSI CSV files into normalized ML windows.

Examples:
  python csi_ml/preprocess_csi_windows.py --window-s 20 --stride-s 5
  python csi_ml/preprocess_csi_windows.py data/raw/session_003_hold_breath_off.csv \
    --window-s 10 --stride-s 2 --target-hz 70
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from io import StringIO
from pathlib import Path

np = None


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


def raw_to_amplitude(data_text: str, expected_len: int = 384) -> np.ndarray:
    raw = json.loads(data_text)
    if len(raw) != expected_len:
        raise ValueError(f"CSI len mismatch: expected {expected_len}, got {len(raw)}")
    imag = np.asarray(raw[0::2], dtype=np.float32)
    real = np.asarray(raw[1::2], dtype=np.float32)
    return np.sqrt(real * real + imag * imag)


def iter_session_rows(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fd:
        first_line = fd.readline()
        fd.seek(0)
        if first_line.startswith("session_id,") or "raw_line" in first_line:
            for row in csv.DictReader(fd):
                yield row, row.get("raw_line", "")
        else:
            for index, line in enumerate(fd):
                yield {"sample_index": index, "raw_line": line.rstrip("\r\n")}, line.rstrip("\r\n")


def load_metadata_for(raw_file: Path, base_dir: Path) -> dict:
    metadata_dir = base_dir / "data" / "metadata"
    candidates = list(metadata_dir.glob(raw_file.stem + ".json"))
    if candidates:
        with open(candidates[0], "r", encoding="utf-8") as fd:
            return json.load(fd)
    return {}


def estimate_packet_loss(ids: list[int]) -> float:
    if len(ids) < 2:
        return 0.0
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
    return 100.0 * lost / max(expected, 1)


def load_session(raw_file: Path, base_dir: Path, expected_len: int, stabilization_s: float) -> list[dict]:
    metadata = load_metadata_for(raw_file, base_dir)
    fallback_rate = float(metadata.get("sample_rate_estimated_hz") or 70.0)
    session = []
    clean_index = 0

    for row, raw_line in iter_session_rows(raw_file):
        try:
            parsed = parse_csi_line(raw_line) if raw_line else row
            if to_int(parsed.get("len")) != expected_len:
                continue
            amplitude = raw_to_amplitude(parsed["data"], expected_len)
            if amplitude.shape[0] != expected_len // 2:
                continue

            elapsed_s_text = row.get("elapsed_s")
            if elapsed_s_text not in (None, ""):
                elapsed_s = float(elapsed_s_text)
            else:
                elapsed_s = clean_index / fallback_rate
            clean_index += 1

            if elapsed_s < stabilization_s:
                continue

            session.append(
                {
                    "elapsed_s": elapsed_s,
                    "id": to_int(parsed.get("id")),
                    "rssi": to_int(parsed.get("rssi")),
                    "label": row.get("label") or metadata.get("label") or "unknown",
                    "event_label": row.get("event_label") or row.get("label") or metadata.get("label") or "unknown",
                    "session_id": row.get("session_id") or metadata.get("session_id") or raw_file.stem,
                    "amplitude": amplitude,
                }
            )
        except Exception:
            continue
    return session


def majority_label(labels: list[str], min_fraction: float) -> str | None:
    if not labels:
        return None
    counts = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    label, count = max(counts.items(), key=lambda item: item[1])
    return label if count / len(labels) >= min_fraction else None


def resample_window(samples: list[dict], start_s: float, end_s: float, target_count: int) -> np.ndarray:
    times = np.asarray([sample["elapsed_s"] for sample in samples], dtype=np.float32)
    matrix = np.stack([sample["amplitude"] for sample in samples], axis=0)
    target_times = np.linspace(start_s, end_s, target_count, endpoint=False, dtype=np.float32)
    output = np.empty((target_count, matrix.shape[1]), dtype=np.float32)
    for carrier in range(matrix.shape[1]):
        output[:, carrier] = np.interp(target_times, times, matrix[:, carrier])
    mean = float(output.mean())
    std = float(output.std())
    if std < 1e-6:
        std = 1.0
    return (output - mean) / std


def preprocess_file(raw_file: Path, args: argparse.Namespace, base_dir: Path):
    session = load_session(raw_file, base_dir, args.expected_len, args.stabilization_s)
    if not session:
        return [], []

    windows = []
    metadata_rows = []
    start_min = min(item["elapsed_s"] for item in session)
    end_max = max(item["elapsed_s"] for item in session)
    start_s = start_min
    target_count = int(round(args.window_s * args.target_hz))
    window_id_base = raw_file.stem

    while start_s + args.window_s <= end_max + 1e-6:
        end_s = start_s + args.window_s
        samples = [item for item in session if start_s <= item["elapsed_s"] < end_s]
        start_s += args.stride_s
        if len(samples) < max(4, int(target_count * args.min_coverage)):
            continue

        general_label = majority_label([item["label"] for item in samples], args.majority_threshold)
        event_label = majority_label([item["event_label"] for item in samples], args.majority_threshold)
        if general_label is None or event_label is None:
            continue

        ids = [item["id"] for item in samples if item["id"] is not None]
        packet_loss_pct = estimate_packet_loss(ids)
        if packet_loss_pct > args.max_window_loss_pct:
            continue

        window = resample_window(samples, samples[0]["elapsed_s"], samples[-1]["elapsed_s"], target_count)
        rssi_values = [item["rssi"] for item in samples if item["rssi"] is not None]
        label_for_y = event_label if args.label_source == "event" else general_label
        window_id = f"{window_id_base}_w{len(windows):05d}"
        windows.append((window, label_for_y))
        metadata_rows.append(
            {
                "window_id": window_id,
                "session_id": samples[0]["session_id"],
                "source_file": str(raw_file),
                "label": general_label,
                "event_label": event_label,
                "y_label": label_for_y,
                "start_s": round(samples[0]["elapsed_s"], 6),
                "end_s": round(samples[-1]["elapsed_s"], 6),
                "window_s": args.window_s,
                "stride_s": args.stride_s,
                "target_hz": args.target_hz,
                "sample_count": len(samples),
                "resampled_count": target_count,
                "packet_loss_pct": round(packet_loss_pct, 3),
                "rssi_mean": round(statistics.mean(rssi_values), 3) if rssi_values else "",
                "rssi_std": round(statistics.pstdev(rssi_values), 3) if len(rssi_values) > 1 else 0,
            }
        )

    return windows, metadata_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preprocess ESP32 CSI raw CSV into ML windows.")
    parser.add_argument("files", nargs="*", help="Raw files. Defaults to data/raw/*.csv")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--window-s", type=float, default=20.0)
    parser.add_argument("--stride-s", type=float, default=5.0)
    parser.add_argument("--target-hz", type=float, default=70.0)
    parser.add_argument("--stabilization-s", type=float, default=10.0)
    parser.add_argument("--expected-len", type=int, default=384)
    parser.add_argument("--majority-threshold", type=float, default=0.80)
    parser.add_argument("--min-coverage", type=float, default=0.50)
    parser.add_argument("--max-window-loss-pct", type=float, default=10.0)
    parser.add_argument("--label-source", choices=["event", "session"], default="event")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global np
    try:
        import numpy as numpy_module
    except ImportError:
        print("Missing dependency: numpy. Install it with `python3 -m pip install numpy`.")
        return 2
    np = numpy_module

    base_dir = Path(args.base_dir).resolve()
    raw_dir = base_dir / "data" / "raw"
    processed_dir = base_dir / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(item) for item in args.files] if args.files else sorted(raw_dir.glob("*.csv"))
    if not files:
        print(f"No raw files found in {raw_dir}")
        return 1

    all_windows = []
    all_labels = []
    all_metadata = []
    for raw_file in files:
        windows, metadata_rows = preprocess_file(raw_file, args, base_dir)
        for window, label in windows:
            all_windows.append(window)
            all_labels.append(label)
        all_metadata.extend(metadata_rows)
        print(f"{raw_file.name}: {len(windows)} windows")

    if not all_windows:
        print("No valid windows generated.")
        return 1

    X = np.stack(all_windows, axis=0).astype(np.float32)
    y = np.asarray(all_labels, dtype=object)
    np.save(processed_dir / "X_windows.npy", X)
    np.save(processed_dir / "y_labels.npy", y)

    metadata_file = processed_dir / "window_metadata.csv"
    fieldnames = [
        "window_id",
        "session_id",
        "source_file",
        "label",
        "event_label",
        "y_label",
        "start_s",
        "end_s",
        "window_s",
        "stride_s",
        "target_hz",
        "sample_count",
        "resampled_count",
        "packet_loss_pct",
        "rssi_mean",
        "rssi_std",
    ]
    with open(metadata_file, "w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_metadata)

    print(f"Saved X: {processed_dir / 'X_windows.npy'} shape={X.shape}")
    print(f"Saved y: {processed_dir / 'y_labels.npy'} count={len(y)}")
    print(f"Saved metadata: {metadata_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

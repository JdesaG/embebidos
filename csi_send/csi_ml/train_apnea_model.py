#!/usr/bin/env python3
"""Train the dashboard-compatible apnea model from labeled CSI sessions.

The extracted feature pipeline deliberately matches ``RealtimeApneaInference``
in csi_web/server.py.  It accepts only clean 20-second windows whose label is
entirely known; this keeps the transitions into and out of a breath hold out of
the binary training target.

Example:
  /Users/jandonyggarofalo/.espressif/tools/python/v5.5.4/venv/bin/python3 \
    csi_ml/train_apnea_model.py --month 202607
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import joblib
import numpy as np
from scipy.signal import butter, filtfilt, welch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


C5C6_COLUMNS = [
    "type", "id", "mac", "rssi", "rate", "noise_floor", "fft_gain", "agc_gain",
    "channel", "timestamp", "sig_len", "rx_state", "len", "first_word", "data",
]
CLASSIC_COLUMNS = [
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth",
    "smoothing", "not_sounding", "aggregation", "stbc", "fec_coding", "sgi",
    "noise_floor", "ampdu_cnt", "channel", "secondary_channel", "timestamp", "ant",
    "sig_len", "rx_state", "len", "first_word", "data",
]
FEATURES = [
    "psd_resp_band", "psd_total", "snr_resp", "variance_filtered",
    "zero_crossings", "spectral_entropy", "peak_freq_hz", "peak_power",
]
APNEA_EVENT_LABEL = "hold_breath"
NORMAL_EVENT_LABELS = {"breathing", "breathing_off"}


def parse_csi_line(line: str) -> dict:
    start = line.find("CSI_DATA")
    if start < 0:
        raise ValueError("CSI_DATA missing")
    values = next(csv.reader(StringIO(line[start:])))
    if len(values) == len(C5C6_COLUMNS):
        return dict(zip(C5C6_COLUMNS, values))
    if len(values) == len(CLASSIC_COLUMNS):
        return dict(zip(CLASSIC_COLUMNS, values))
    raise ValueError("unexpected CSI column count")


def amplitude_from_data(text: str) -> np.ndarray:
    raw = json.loads(text)
    if len(raw) != 384:
        raise ValueError("unexpected CSI payload length")
    imag = np.asarray(raw[0::2], dtype=np.float32)
    real = np.asarray(raw[1::2], dtype=np.float32)
    return np.sqrt(real * real + imag * imag)


def event_label_at(elapsed_s: float, session_label: str, events: list[dict]) -> str:
    for event in events:
        if float(event["start_s"]) <= elapsed_s < float(event["end_s"]):
            return str(event["label"])
    return session_label


def load_session(raw_path: Path, metadata_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return time, amplitude and binary/event labels for one raw session."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    events = metadata.get("events") or []
    times, amplitudes, labels = [], [], []
    with raw_path.open("r", encoding="utf-8", errors="replace", newline="") as fd:
        for row in csv.DictReader(fd):
            try:
                elapsed_s = float(row["elapsed_s"])
                parsed = parse_csi_line(row.get("raw_line", ""))
                if int(parsed["len"]) != 384:
                    continue
                amplitude = amplitude_from_data(parsed["data"])
                times.append(elapsed_s)
                amplitudes.append(amplitude)
                labels.append(row.get("event_label") or event_label_at(elapsed_s, metadata["label"], events))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    if not times:
        raise ValueError("no valid CSI samples")
    return (
        np.asarray(times, dtype=np.float64),
        np.stack(amplitudes).astype(np.float32),
        np.asarray(labels, dtype=object),
    )


def extract_features(matrix: np.ndarray, target_hz: float, respiratory_low_hz: float, respiratory_high_hz: float) -> dict:
    """Mirror the dashboard's PCA, filtering and eight-feature calculation."""
    active = matrix.mean(axis=0) > 0.5
    if int(active.sum()) < 8:
        raise ValueError("not enough active subcarriers")
    matrix = matrix[:, active]
    rolling_n = int(round(3.0 * target_hz))
    if rolling_n % 2 == 0:
        rolling_n += 1
    kernel = np.ones(rolling_n, dtype=np.float32) / rolling_n
    detrended = np.empty_like(matrix)
    for carrier in range(matrix.shape[1]):
        padded = np.pad(matrix[:, carrier], rolling_n // 2, mode="edge")
        detrended[:, carrier] = matrix[:, carrier] - np.convolve(padded, kernel, mode="valid")

    centered = detrended - detrended.mean(axis=0)
    u, singular, _ = np.linalg.svd(centered, full_matrices=False)
    if not len(singular) or float((singular * singular).sum()) <= 1e-12:
        raise ValueError("no useful CSI variation")
    pc1 = u[:, 0] * singular[0]
    pc1_var = float(singular[0] ** 2 / (singular * singular).sum())

    nyquist = target_hz / 2.0
    b, a = butter(4, [respiratory_low_hz / nyquist, respiratory_high_hz / nyquist], btype="band")
    resp = filtfilt(b, a, pc1)
    freq, psd = welch(resp, fs=target_hz, nperseg=len(resp))
    band = (freq >= respiratory_low_hz) & (freq <= respiratory_high_hz)
    psd_resp = float(psd[band].mean()) if bool(band.any()) else 0.0
    psd_total = float(psd.mean())
    normalized = psd / (psd.sum() + 1e-12)
    if bool(band.any()) and float(psd[band].max()) > 0:
        band_freq = freq[band]
        band_psd = psd[band]
        peak_index = int(np.argmax(band_psd))
        peak_freq = float(band_freq[peak_index])
        peak_power = float(band_psd[peak_index])
    else:
        peak_freq = peak_power = 0.0
    return {
        "psd_resp_band": psd_resp,
        "psd_total": psd_total,
        "snr_resp": psd_resp / (psd_total + 1e-12),
        "variance_filtered": float(resp.var()),
        "zero_crossings": int((np.diff(np.sign(resp)) != 0).sum()),
        "spectral_entropy": float(-np.sum(normalized * np.log(normalized + 1e-12))),
        "peak_freq_hz": peak_freq,
        "peak_power": peak_power,
        "pc1_var_explained": pc1_var,
    }


def windows_from_session(raw_path: Path, metadata_path: Path, args: argparse.Namespace) -> tuple[list[list[float]], list[int], list[str]]:
    times, amplitudes, labels = load_session(raw_path, metadata_path)
    features, targets, groups = [], [], []
    start = max(float(times.min()), args.stabilization_s)
    end = float(times.max())
    target_count = int(round(args.window_s * args.target_hz))
    while start + args.window_s <= end:
        stop = start + args.window_s
        included = (times >= start) & (times < stop)
        window_labels = labels[included]
        if len(window_labels) < 4:
            start += args.stride_s
            continue
        counts = Counter(window_labels.tolist())
        label, count = counts.most_common(1)[0]
        if count / len(window_labels) < args.label_purity or label not in NORMAL_EVENT_LABELS | {APNEA_EVENT_LABEL}:
            start += args.stride_s
            continue
        window_times = times[included]
        window_matrix = amplitudes[included]
        uniform_times = np.linspace(start, stop, target_count, endpoint=False)
        uniform = np.empty((target_count, window_matrix.shape[1]), dtype=np.float32)
        for carrier in range(window_matrix.shape[1]):
            uniform[:, carrier] = np.interp(uniform_times, window_times, window_matrix[:, carrier])
        try:
            result = extract_features(uniform, args.target_hz, args.respiratory_low_hz, args.respiratory_high_hz)
        except ValueError:
            start += args.stride_s
            continue
        features.append([float(result[name]) for name in FEATURES])
        targets.append(1 if label == APNEA_EVENT_LABEL else 0)
        groups.append(raw_path.stem)
        start += args.stride_s
    return features, targets, groups


def split_groups(groups: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Reserve one whole apnea and one whole normal session for an honest test."""
    group_target = {}
    for group in sorted(set(groups)):
        values = targets[groups == group]
        group_target[group] = int(bool((values == 1).any()))
    test_groups = []
    for target in (0, 1):
        candidates = [group for group, value in group_target.items() if value == target]
        if not candidates:
            raise ValueError(f"missing class {target} for test split")
        test_groups.append(candidates[-1])
    test_mask = np.isin(groups, test_groups)
    return ~test_mask, test_mask, {"train_groups": sorted(set(groups[~test_mask])), "test_groups": test_groups}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a dashboard-compatible CSI apnea model.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--month", default="202607", help="YYYYMM prefix to select raw sessions.")
    parser.add_argument("--output", default="models/apnea_model_july_2026.joblib")
    parser.add_argument("--report", default="data/reports/apnea_model_july_2026.json")
    parser.add_argument("--window-s", type=float, default=20.0)
    parser.add_argument("--stride-s", type=float, default=5.0)
    parser.add_argument("--target-hz", type=float, default=50.0)
    parser.add_argument("--stabilization-s", type=float, default=10.0)
    parser.add_argument("--label-purity", type=float, default=1.0)
    parser.add_argument("--respiratory-low-hz", type=float, default=0.1)
    parser.add_argument("--respiratory-high-hz", type=float, default=0.5)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    raw_files = sorted((base_dir / "data" / "raw").glob(f"session_{args.month}*.csv"))
    if not raw_files:
        raise SystemExit(f"No raw sessions found for month prefix {args.month}.")

    all_x, all_y, all_groups, skipped = [], [], [], []
    for raw_path in raw_files:
        metadata_path = base_dir / "data" / "metadata" / f"{raw_path.stem}.json"
        if not metadata_path.exists():
            skipped.append({"file": raw_path.name, "reason": "metadata missing"})
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("label") not in {"breathing_off", "hold_breath_off"}:
            skipped.append({"file": raw_path.name, "reason": f"not a binary respiratory session ({metadata.get('label')})"})
            continue
        x, y, groups = windows_from_session(raw_path, metadata_path, args)
        all_x.extend(x)
        all_y.extend(y)
        all_groups.extend(groups)
        print(f"{raw_path.name}: {len(x)} clean windows")
    if not all_x or len(set(all_y)) != 2:
        raise SystemExit("Insufficient clean windows for both classes.")

    X = np.asarray(all_x, dtype=np.float64)
    y = np.asarray(all_y, dtype=np.int8)
    groups = np.asarray(all_groups, dtype=object)
    train_mask, test_mask, split = split_groups(groups, y)
    candidate = Pipeline([
        ("scaler", StandardScaler()),
        ("model", LogisticRegression(C=1.0, class_weight={0: 1, 1: 2}, max_iter=2000, random_state=args.random_state)),
    ])
    candidate.fit(X[train_mask], y[train_mask])
    predicted = candidate.predict(X[test_mask])
    probabilities = candidate.predict_proba(X[test_mask])[:, 1]
    test_report = classification_report(y[test_mask], predicted, target_names=["breathing", "apnea"], output_dict=True, zero_division=0)

    # The saved model is refit on every eligible July window after the held-out
    # measurement; the report makes that distinction explicit.
    final_pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("model", LogisticRegression(C=1.0, class_weight={0: 1, 1: 2}, max_iter=2000, random_state=args.random_state)),
    ])
    final_pipeline.fit(X, y)
    output_path = (base_dir / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": final_pipeline.named_steps["model"],
        "scaler": final_pipeline.named_steps["scaler"],
        "features": FEATURES,
        "classes": {"0": "breathing", "1": "apnea"},
        "training": {
            "source_month": args.month,
            "window_s": args.window_s,
            "stride_s": args.stride_s,
            "target_hz": args.target_hz,
            "respiratory_band_hz": [args.respiratory_low_hz, args.respiratory_high_hz],
            "label_purity": args.label_purity,
            "class_weight": {"breathing": 1, "apnea": 2},
            "feature_pipeline": "Matches csi_web.server.RealtimeApneaInference",
        },
    }
    joblib.dump(payload, output_path)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_file": str(output_path.relative_to(base_dir)),
        "source_month": args.month,
        "eligible_sessions": sorted(set(groups)),
        "skipped_sessions": skipped,
        "features": FEATURES,
        "windows": {"total": int(len(y)), "breathing": int((y == 0).sum()), "apnea": int((y == 1).sum())},
        "held_out_session_test": {
            **split,
            "windows": int(test_mask.sum()),
            "accuracy": round(float(accuracy_score(y[test_mask], predicted)), 4),
            "confusion_matrix_rows_actual_columns_predicted": confusion_matrix(y[test_mask], predicted, labels=[0, 1]).tolist(),
            "classification_report": test_report,
            "apnea_probability_mean": round(float(probabilities.mean()), 4),
        },
        "saved_model_training": "Refit on all eligible July windows after held-out evaluation.",
        "limitations": [
            "This is a simulated breath-hold detector, not a medical device or diagnostic tool.",
            "Windows from the active-environment session were excluded because its label is not normal breathing.",
        ],
    }
    report_path = (base_dir / args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved model: {output_path}")
    print(f"Saved report: {report_path}")
    print(f"Held-out accuracy: {report['held_out_session_test']['accuracy']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

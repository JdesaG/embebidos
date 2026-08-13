#!/usr/bin/env python3
"""Build a conservative, gap-aware respiratory CSI window dataset.

The source CSV files are never modified.  Every candidate window is recorded in
the manifest together with its acceptance status and rejection reason.  A
second, conservative label filter marks scheduled breath-hold windows as
ambiguous when their respiratory activity resembles the trusted breathing
baseline from the same session.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt, welch


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
NORMAL_LABELS = {"breathing", "breathing_off"}
HOLD_LABEL = "hold_breath"


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


def load_metadata(base_dir: Path, raw_path: Path) -> dict:
    path = base_dir / "data" / "metadata" / f"{raw_path.stem}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_session(base_dir: Path, raw_path: Path) -> tuple[dict, list[dict], Counter]:
    metadata = load_metadata(base_dir, raw_path)
    rows: list[dict] = []
    payload_lengths: Counter = Counter()
    with raw_path.open("r", encoding="utf-8", errors="replace", newline="") as fd:
        for source in csv.DictReader(fd):
            try:
                parsed = parse_csi_line(source.get("raw_line", ""))
                raw = json.loads(parsed["data"])
                declared_len = int(parsed["len"])
                if declared_len != len(raw) or len(raw) < 16 or len(raw) % 2:
                    continue
                imag = np.asarray(raw[0::2], dtype=np.float32)
                real = np.asarray(raw[1::2], dtype=np.float32)
                amplitude = np.hypot(real, imag).astype(np.float32)
                payload_lengths[declared_len] += 1
                rows.append(
                    {
                        "time": float(source["elapsed_s"]),
                        "label": source.get("event_label") or source.get("label") or "unknown",
                        "amplitude": amplitude,
                        "rssi": float(parsed.get("rssi") or 0),
                        "packet_id": int(parsed.get("id") or 0),
                    }
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    if not rows:
        raise ValueError("no valid CSI rows")
    modal_len = payload_lengths.most_common(1)[0][0]
    rows = [row for row in rows if row["amplitude"].size * 2 == modal_len]
    return metadata, rows, payload_lengths


def robust_clip(matrix: np.ndarray, z_limit: float) -> tuple[np.ndarray, float]:
    """Winsorize isolated per-subcarrier amplitude spikes with median/MAD limits."""
    median = np.median(matrix, axis=0)
    mad = np.median(np.abs(matrix - median), axis=0)
    scale = 1.4826 * mad
    fallback = np.std(matrix, axis=0)
    scale = np.where(scale > 1e-6, scale, np.where(fallback > 1e-6, fallback, 1.0))
    low = median - z_limit * scale
    high = median + z_limit * scale
    mask = (matrix < low) | (matrix > high)
    return np.clip(matrix, low, high).astype(np.float32), float(mask.mean())


def extract_features(
    matrix: np.ndarray,
    target_hz: float,
    respiratory_low_hz: float,
    respiratory_high_hz: float,
) -> dict:
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
    total_variance = float((singular * singular).sum())
    if not len(singular) or total_variance <= 1e-12:
        raise ValueError("no useful CSI variation")
    pc1 = u[:, 0] * singular[0]

    nyquist = target_hz / 2.0
    b, a = butter(4, [respiratory_low_hz / nyquist, respiratory_high_hz / nyquist], btype="band")
    resp = filtfilt(b, a, pc1)
    freq, psd = welch(resp, fs=target_hz, nperseg=len(resp))
    band = (freq >= respiratory_low_hz) & (freq <= respiratory_high_hz)
    if not bool(band.any()):
        raise ValueError("respiratory band is empty")
    psd_resp = float(psd[band].mean())
    psd_total = float(psd.mean())
    normalized = psd / (psd.sum() + 1e-12)
    band_freq = freq[band]
    band_psd = psd[band]
    peak_index = int(np.argmax(band_psd))
    return {
        "psd_resp_band": psd_resp,
        "psd_total": psd_total,
        "snr_resp": psd_resp / (psd_total + 1e-12),
        "variance_filtered": float(resp.var()),
        "zero_crossings": int((np.diff(np.sign(resp)) != 0).sum()),
        "spectral_entropy": float(-np.sum(normalized * np.log(normalized + 1e-12))),
        "peak_freq_hz": float(band_freq[peak_index]),
        "peak_power": float(band_psd[peak_index]),
        "pc1_var_explained": float(singular[0] ** 2 / total_variance),
    }


def overlaps_transition(start_s: float, end_s: float, transitions: list[float], guard_s: float) -> bool:
    return any(start_s < transition + guard_s and end_s > transition - guard_s for transition in transitions)


def activity_log(features: dict) -> float:
    """Scalar periodic respiratory-energy score used only for conservative filtering."""
    eps = 1e-12
    return float(
        np.median(
            [
                math.log10(features["psd_resp_band"] + eps),
                math.log10(features["variance_filtered"] + eps),
                math.log10(features["peak_power"] + eps),
            ]
        )
    )


def candidate_windows(base_dir: Path, raw_path: Path, args: argparse.Namespace) -> tuple[list[dict], dict]:
    metadata, rows, payload_lengths = load_session(base_dir, raw_path)
    times = np.asarray([row["time"] for row in rows], dtype=np.float64)
    labels = np.asarray([row["label"] for row in rows], dtype=object)
    amplitudes = np.stack([row["amplitude"] for row in rows]).astype(np.float32)
    transitions = sorted(
        {
            float(event[edge])
            for event in metadata.get("events", [])
            for edge in ("start_s", "end_s")
            if float(event[edge]) > 0
        }
    )
    start_s = max(float(times.min()), args.stabilization_s)
    end_s = float(times.max())
    target_count = int(round(args.window_s * args.target_hz))
    output: list[dict] = []
    index = 0

    while start_s + args.window_s <= end_s + 1e-9:
        stop_s = start_s + args.window_s
        included = (times >= start_s) & (times < stop_s)
        window_times = times[included]
        window_labels = labels[included]
        record = {
            "window_id": f"{raw_path.stem}_w{index:05d}",
            "session_id": raw_path.stem,
            "source_file": str(raw_path),
            "start_s": round(start_s, 6),
            "end_s": round(stop_s, 6),
            "scheduled_label": "unknown",
            "quality_status": "rejected",
            "quality_reason": "",
            "confidence_status": "not_evaluated",
            "final_label": "",
            "sample_count": int(included.sum()),
            "max_gap_ms": "",
            "clip_fraction": "",
        }
        index += 1

        if not len(window_times):
            record["quality_reason"] = "no_samples"
            output.append(record)
            start_s += args.stride_s
            continue
        counts = Counter(window_labels.tolist())
        label, count = counts.most_common(1)[0]
        record["scheduled_label"] = label
        if label not in NORMAL_LABELS | {HOLD_LABEL} or count != len(window_labels):
            record["quality_reason"] = "mixed_or_unknown_label"
            output.append(record)
            start_s += args.stride_s
            continue
        if overlaps_transition(start_s, stop_s, transitions, args.transition_guard_s):
            record["quality_reason"] = "transition_guard"
            output.append(record)
            start_s += args.stride_s
            continue
        if len(window_times) < int(math.ceil(target_count * args.min_sample_ratio)):
            record["quality_reason"] = "insufficient_samples"
            output.append(record)
            start_s += args.stride_s
            continue
        gaps = np.diff(window_times)
        max_gap_s = float(gaps.max()) if len(gaps) else args.window_s
        record["max_gap_ms"] = round(max_gap_s * 1000.0, 3)
        if max_gap_s > args.max_gap_s:
            record["quality_reason"] = "time_gap"
            output.append(record)
            start_s += args.stride_s
            continue

        matrix = amplitudes[included]
        matrix, clip_fraction = robust_clip(matrix, args.outlier_mad_z)
        record["clip_fraction"] = round(clip_fraction, 8)
        if clip_fraction > args.max_clip_fraction:
            record["quality_reason"] = "too_many_amplitude_outliers"
            output.append(record)
            start_s += args.stride_s
            continue
        uniform_times = np.linspace(start_s, stop_s, target_count, endpoint=False)
        uniform = np.empty((target_count, matrix.shape[1]), dtype=np.float32)
        for carrier in range(matrix.shape[1]):
            uniform[:, carrier] = np.interp(uniform_times, window_times, matrix[:, carrier])
        try:
            features = extract_features(
                uniform,
                args.target_hz,
                args.respiratory_low_hz,
                args.respiratory_high_hz,
            )
        except ValueError as exc:
            record["quality_reason"] = f"feature_error:{exc}"
            output.append(record)
            start_s += args.stride_s
            continue

        record.update({name: features[name] for name in FEATURES})
        record["pc1_var_explained"] = features["pc1_var_explained"]
        record["activity_log"] = activity_log(features)
        record["quality_status"] = "accepted"
        record["quality_reason"] = ""
        output.append(record)
        start_s += args.stride_s

    summary = {
        "source_file": str(raw_path),
        "metadata": metadata,
        "valid_rows": len(rows),
        "payload_lengths": {str(key): value for key, value in sorted(payload_lengths.items())},
        "data_start_s": round(float(times.min()), 6),
        "data_end_s": round(float(times.max()), 6),
    }
    return output, summary


def apply_confidence_filter(records: list[dict], breathing_quantile: float) -> dict:
    sessions = sorted({record["session_id"] for record in records})
    session_thresholds = {}
    for session in sessions:
        trusted = [
            record["activity_log"]
            for record in records
            if record["session_id"] == session
            and record["quality_status"] == "accepted"
            and record["scheduled_label"] in NORMAL_LABELS
        ]
        if not trusted:
            continue
        threshold = float(np.quantile(np.asarray(trusted), breathing_quantile))
        session_thresholds[session] = threshold
        for record in records:
            if record["session_id"] != session or record["quality_status"] != "accepted":
                continue
            record["breathing_activity_threshold"] = threshold
            if record["scheduled_label"] in NORMAL_LABELS:
                record["confidence_status"] = "trusted_breathing"
                record["final_label"] = "breathing"
            elif record["scheduled_label"] == HOLD_LABEL:
                if float(record["activity_log"]) >= threshold:
                    record["confidence_status"] = "ambiguous_breathing_evidence"
                    record["final_label"] = ""
                else:
                    record["confidence_status"] = "high_confidence_hold"
                    record["final_label"] = "hold_breath"
    return session_thresholds


def write_outputs(output_dir: Path, records: list[dict], session_summaries: list[dict], args: argparse.Namespace) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "window_manifest.csv"
    feature_fields = FEATURES + ["pc1_var_explained", "activity_log", "breathing_activity_threshold"]
    fieldnames = [
        "window_id", "session_id", "source_file", "start_s", "end_s", "scheduled_label",
        "quality_status", "quality_reason", "confidence_status", "final_label", "sample_count",
        "max_gap_ms", "clip_fraction",
    ] + feature_fields
    with manifest_path.open("w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    quality_accepted = [
        record for record in records
        if record["quality_status"] == "accepted"
        and record["scheduled_label"] in NORMAL_LABELS | {HOLD_LABEL}
    ]
    X_quality = np.asarray(
        [[float(record[name]) for name in FEATURES] for record in quality_accepted],
        dtype=np.float64,
    )
    y_quality = np.asarray(
        [0 if record["scheduled_label"] in NORMAL_LABELS else 1 for record in quality_accepted],
        dtype=np.int8,
    )
    quality_groups = np.asarray([record["session_id"] for record in quality_accepted], dtype=object)
    np.save(output_dir / "X_quality_features.npy", X_quality)
    np.save(output_dir / "y_quality_labels.npy", y_quality)
    np.save(output_dir / "quality_groups.npy", quality_groups)

    accepted = [record for record in records if record.get("final_label")]
    X = np.asarray([[float(record[name]) for name in FEATURES] for record in accepted], dtype=np.float64)
    y = np.asarray([0 if record["final_label"] == "breathing" else 1 for record in accepted], dtype=np.int8)
    groups = np.asarray([record["session_id"] for record in accepted], dtype=object)
    np.save(output_dir / "X_features.npy", X)
    np.save(output_dir / "y_labels.npy", y)
    np.save(output_dir / "groups.npy", groups)

    reasons = Counter(record["quality_reason"] or "accepted" for record in records)
    confidence = Counter(record["confidence_status"] for record in records if record["quality_status"] == "accepted")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_files": [summary["source_file"] for summary in session_summaries],
        "source_files_unchanged": True,
        "parameters": vars(args),
        "filters": [
            "valid parseable CSI with a consistent even payload length per session",
            "initial stabilization exclusion",
            "100% label purity and transition guard",
            "minimum sample coverage",
            "maximum inter-sample time gap",
            "per-subcarrier median/MAD spike winsorization",
            "maximum clipped-amplitude fraction",
            "valid active-subcarrier/PCA/respiratory feature extraction",
            "conservative exclusion of hold windows whose activity matches session breathing baseline",
        ],
        "candidate_windows": len(records),
        "quality_counts": dict(reasons),
        "confidence_counts": dict(confidence),
        "final_windows": {
            "total": len(accepted),
            "breathing": int((y == 0).sum()) if len(y) else 0,
            "hold_breath": int((y == 1).sum()) if len(y) else 0,
        },
        "session_summaries": session_summaries,
        "artifacts": {
            "manifest": str(manifest_path),
            "quality_features": str(output_dir / "X_quality_features.npy"),
            "quality_labels": str(output_dir / "y_quality_labels.npy"),
            "quality_groups": str(output_dir / "quality_groups.npy"),
            "features": str(output_dir / "X_features.npy"),
            "labels": str(output_dir / "y_labels.npy"),
            "groups": str(output_dir / "groups.npy"),
        },
        "limitations": [
            "Breathing inside a scheduled hold has no external ground-truth timestamp.",
            "Ambiguous hold windows are excluded, never relabeled as breathing.",
            "The confidence filter is suitable for conservative training curation, not an independent performance claim.",
        ],
    }
    (output_dir / "filter_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Raw CSI CSV files to filter")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--output-dir", default="data/filtered/respiratory_20260812")
    parser.add_argument("--window-s", type=float, default=8.0)
    parser.add_argument("--stride-s", type=float, default=2.0)
    parser.add_argument("--target-hz", type=float, default=50.0)
    parser.add_argument("--stabilization-s", type=float, default=10.0)
    parser.add_argument("--transition-guard-s", type=float, default=2.0)
    parser.add_argument("--max-gap-s", type=float, default=0.1)
    parser.add_argument("--min-sample-ratio", type=float, default=0.8)
    parser.add_argument("--outlier-mad-z", type=float, default=6.0)
    parser.add_argument("--max-clip-fraction", type=float, default=0.02)
    parser.add_argument("--breathing-quantile", type=float, default=0.10)
    parser.add_argument("--respiratory-low-hz", type=float, default=0.1)
    parser.add_argument("--respiratory-high-hz", type=float, default=0.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    all_records: list[dict] = []
    summaries: list[dict] = []
    for value in args.files:
        raw_path = Path(value).resolve()
        records, summary = candidate_windows(base_dir, raw_path, args)
        all_records.extend(records)
        summaries.append(summary)
        accepted = sum(record["quality_status"] == "accepted" for record in records)
        print(f"{raw_path.name}: {accepted}/{len(records)} quality windows")
    thresholds = apply_confidence_filter(all_records, args.breathing_quantile)
    for summary in summaries:
        session = Path(summary["source_file"]).stem
        summary["breathing_activity_threshold"] = thresholds.get(session)
    write_outputs(output_dir, all_records, summaries, args)
    final = [record for record in all_records if record.get("final_label")]
    counts = Counter(record["final_label"] for record in final)
    print(f"Saved: {output_dir}")
    print(f"Final windows: {len(final)} {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

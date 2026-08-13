#!/usr/bin/env python3
"""Evaluate filtered CSI windows with whole-session cross-validation."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "psd_resp_band", "psd_total", "snr_resp", "variance_filtered",
    "zero_crossings", "spectral_entropy", "peak_freq_hz", "peak_power",
]
LOG_FEATURES = {"psd_resp_band", "psd_total", "variance_filtered", "peak_power"}
NORMAL_LABELS = {"breathing", "breathing_off"}


def load_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as fd:
        return list(csv.DictReader(fd))


def feature_vector(row: dict) -> np.ndarray:
    values = []
    for name in FEATURES:
        value = float(row[name])
        if name in LOG_FEATURES:
            value = float(np.log10(max(value, 1e-12)))
        values.append(value)
    return np.asarray(values, dtype=np.float64)


def baseline_relative(X: np.ndarray, rows: list[dict], groups: np.ndarray) -> np.ndarray:
    output = X.copy()
    for group in sorted(set(groups)):
        indices = np.flatnonzero(groups == group)
        trusted = [
            index for index in indices
            if rows[index]["scheduled_label"] in NORMAL_LABELS
        ]
        if not trusted:
            continue
        trusted.sort(key=lambda index: float(rows[index]["start_s"]))
        # The first normal windows model an explicit pre-test calibration phase.
        baseline_indices = trusted[: min(15, len(trusted))]
        baseline = np.median(X[baseline_indices], axis=0)
        output[indices] -= baseline
    return output


def evaluate(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict:
    logo = LeaveOneGroupOut()
    predicted = np.zeros_like(y)
    probabilities = np.zeros(len(y), dtype=np.float64)
    per_session = []
    for train_index, test_index in logo.split(X, y, groups):
        if len(set(y[train_index].tolist())) < 2:
            raise ValueError("a training fold contains only one class")
        pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("model", LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000, random_state=42)),
            ]
        )
        pipeline.fit(X[train_index], y[train_index])
        predicted[test_index] = pipeline.predict(X[test_index])
        probabilities[test_index] = pipeline.predict_proba(X[test_index])[:, 1]
        actual = y[test_index]
        per_session.append(
            {
                "session_id": str(groups[test_index][0]),
                "windows": int(len(test_index)),
                "actual_hold": int((actual == 1).sum()),
                "predicted_hold": int((predicted[test_index] == 1).sum()),
                "accuracy": round(float(accuracy_score(actual, predicted[test_index])), 4),
                "balanced_accuracy": (
                    round(float(balanced_accuracy_score(actual, predicted[test_index])), 4)
                    if len(set(actual.tolist())) == 2
                    else None
                ),
            }
        )
    result = {
        "windows": int(len(y)),
        "breathing_windows": int((y == 0).sum()),
        "hold_windows": int((y == 1).sum()),
        "sessions": int(len(set(groups.tolist()))),
        "accuracy": round(float(accuracy_score(y, predicted)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y, predicted)), 4),
        "hold_precision": round(float(precision_score(y, predicted, zero_division=0)), 4),
        "hold_recall": round(float(recall_score(y, predicted, zero_division=0)), 4),
        "confusion_matrix_rows_actual_columns_predicted": confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
        "per_session": per_session,
    }
    if len(set(y.tolist())) == 2:
        result["roc_auc"] = round(float(roc_auc_score(y, probabilities)), 4)
    return result


def dataset(rows: list[dict], mode: str) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray]:
    if mode == "quality_only":
        selected = [
            row for row in rows
            if row["quality_status"] == "accepted"
            and row["scheduled_label"] in NORMAL_LABELS | {"hold_breath"}
        ]
        labels = [0 if row["scheduled_label"] in NORMAL_LABELS else 1 for row in selected]
    else:
        selected = [row for row in rows if row.get("final_label") in {"breathing", "hold_breath"}]
        labels = [0 if row["final_label"] == "breathing" else 1 for row in selected]
    X = np.stack([feature_vector(row) for row in selected])
    y = np.asarray(labels, dtype=np.int8)
    groups = np.asarray([row["session_id"] for row in selected], dtype=object)
    return selected, X, y, groups


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", help="One or more compatible window manifests")
    parser.add_argument("--output", default="evaluation_report.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifests = [Path(value).resolve() for value in args.manifests]
    rows = [row for manifest in manifests for row in load_rows(manifest)]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifests": [str(manifest) for manifest in manifests],
        "validation": "LeaveOneGroupOut with complete source sessions held out",
        "evaluations": {},
        "interpretation_limits": [
            "quality_only uses scheduled labels that may contain unrecorded breathing during a hold",
            "high_confidence excludes hold windows using respiratory activity and is therefore not an independent clinical performance estimate",
            "baseline_relative assumes a short known-normal calibration at the start of each deployment session",
        ],
    }
    for mode in ("quality_only", "high_confidence"):
        selected, X, y, groups = dataset(rows, mode)
        report["evaluations"][mode] = {
            "log_features_absolute": evaluate(X, y, groups),
            "log_features_baseline_relative": evaluate(baseline_relative(X, selected, groups), y, groups),
        }
    output = Path(args.output).resolve()
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved: {output}")
    for mode, variants in report["evaluations"].items():
        for name, metrics in variants.items():
            print(
                mode,
                name,
                f"balanced={metrics['balanced_accuracy']:.4f}",
                f"hold_recall={metrics['hold_recall']:.4f}",
                f"hold_precision={metrics['hold_precision']:.4f}",
                f"windows={metrics['windows']}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

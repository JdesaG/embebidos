#!/usr/bin/env python3
"""Train the specialized manual breath-hold model from filtered features."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneGroupOut


FEATURES = [
    "psd_resp_band", "psd_total", "snr_resp", "variance_filtered",
    "zero_crossings", "spectral_entropy", "peak_freq_hz", "peak_power",
]


def build_model(args: argparse.Namespace) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        min_samples_leaf=args.min_samples_leaf,
        max_features="sqrt",
        class_weight="balanced",
        random_state=args.random_state,
        n_jobs=-1,
    )


def metrics(y: np.ndarray, probability: np.ndarray, threshold: float) -> dict:
    predicted = (probability >= threshold).astype(np.int8)
    has_both_classes = len(set(y.tolist())) == 2
    return {
        "threshold": threshold,
        "accuracy": round(float(accuracy_score(y, predicted)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y, predicted)), 4) if has_both_classes else None,
        "hold_precision": round(float(precision_score(y, predicted, zero_division=0)), 4),
        "hold_recall": round(float(recall_score(y, predicted, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y, probability)), 4) if has_both_classes else None,
        "confusion_matrix_rows_actual_columns_predicted": confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--window-s", type=float, default=20.0)
    parser.add_argument("--stride-s", type=float, default=5.0)
    parser.add_argument("--target-hz", type=float, default=50.0)
    parser.add_argument("--threshold", type=float, default=0.40)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--min-samples-leaf", type=int, default=2)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset_dir = Path(args.dataset_dir).resolve()
    X = np.load(dataset_dir / "X_quality_features.npy")
    y = np.load(dataset_dir / "y_quality_labels.npy")
    groups = np.load(dataset_dir / "quality_groups.npy", allow_pickle=True)
    if X.ndim != 2 or X.shape[1] != len(FEATURES):
        raise SystemExit(f"Unexpected X shape: {X.shape}")
    if len(X) != len(y) or len(y) != len(groups) or set(y.tolist()) != {0, 1}:
        raise SystemExit("Invalid labels or groups")
    if not np.isfinite(X).all():
        raise SystemExit("Dataset contains non-finite values")

    logo = LeaveOneGroupOut()
    probabilities = np.zeros(len(y), dtype=np.float64)
    per_session = []
    for train_index, test_index in logo.split(X, y, groups):
        model = build_model(args)
        model.fit(X[train_index], y[train_index])
        fold_probability = model.predict_proba(X[test_index])[:, list(model.classes_).index(1)]
        probabilities[test_index] = fold_probability
        fold_metrics = metrics(y[test_index], fold_probability, args.threshold)
        per_session.append(
            {
                "session_id": str(groups[test_index][0]),
                "windows": int(len(test_index)),
                "breathing": int((y[test_index] == 0).sum()),
                "hold_breath": int((y[test_index] == 1).sum()),
                **fold_metrics,
            }
        )

    validation = metrics(y, probabilities, args.threshold)
    final_model = build_model(args)
    final_model.fit(X, y)
    feature_importances = {
        name: round(float(value), 6)
        for name, value in sorted(
            zip(FEATURES, final_model.feature_importances_),
            key=lambda item: item[1],
            reverse=True,
        )
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": final_model,
        "scaler": None,
        "features": FEATURES,
        "classes": {"0": "breathing", "1": "apnea"},
        "decision_threshold": args.threshold,
        "feature_importances": feature_importances,
        "training": {
            "purpose": "specialized_manual_hold_test_case",
            "dataset_dir": str(dataset_dir),
            "window_s": args.window_s,
            "stride_s": args.stride_s,
            "target_hz": args.target_hz,
            "respiratory_band_hz": [0.1, 0.5],
            "algorithm": "RandomForestClassifier",
            "hyperparameters": {
                "n_estimators": args.n_estimators,
                "max_depth": args.max_depth,
                "min_samples_leaf": args.min_samples_leaf,
                "max_features": "sqrt",
                "class_weight": "balanced",
                "random_state": args.random_state,
            },
            "feature_pipeline": "Matches csi_web.server.RealtimeApneaInference; no per-session baseline required",
        },
    }
    joblib.dump(payload, output_path)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_file": str(output_path),
        "dataset_dir": str(dataset_dir),
        "purpose": "specialized_manual_hold_test_case",
        "features": FEATURES,
        "feature_importances": feature_importances,
        "windows": {
            "total": int(len(y)),
            "breathing": int((y == 0).sum()),
            "hold_breath": int((y == 1).sum()),
            "sessions": int(len(set(groups.tolist()))),
        },
        "configuration": payload["training"],
        "leave_one_session_out_validation": {
            **validation,
            "per_session": per_session,
        },
        "model_selection_note": (
            "Algorithm, regularization and decision threshold were selected using the same "
            "leave-one-session-out predictions; reported performance is model-selection performance, "
            "not a final untouched external test."
        ),
        "saved_model_training": "Refit on all 97 technically valid windows after validation.",
        "limitations": [
            "Specialized for this person, placement, hardware and acquisition protocol.",
            "Not a medical device or diagnostic model.",
            "Two of thirteen captured sessions contributed no continuous 20-second windows.",
            "An independent new session is still required for an untouched final test.",
        ],
    }
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved model: {output_path}")
    print(f"Saved report: {report_path}")
    print(json.dumps(validation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

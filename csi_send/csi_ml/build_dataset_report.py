#!/usr/bin/env python3
"""
Build a dataset summary from processed CSI windows.

Examples:
  python csi_ml/build_dataset_report.py
  python csi_ml/build_dataset_report.py --seed 7 --min-windows-per-class 20
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run preprocess_csi_windows.py first.")
    with open(path, "r", encoding="utf-8", newline="") as fd:
        return list(csv.DictReader(fd))


def to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_validation_reports(reports_dir: Path) -> dict[str, dict]:
    reports = {}
    for path in reports_dir.glob("*_validation.json"):
        try:
            with open(path, "r", encoding="utf-8") as fd:
                data = json.load(fd)
            source = Path(data.get("source_file", "")).stem
            if source:
                reports[source] = data
        except Exception:
            continue
    return reports


def split_sessions_by_class(rows: list[dict], seed: int) -> dict[str, list[str]]:
    session_to_label = {}
    for row in rows:
        session_id = row["session_id"]
        label = row.get("y_label") or row.get("event_label") or row.get("label") or "unknown"
        session_to_label.setdefault(session_id, label)

    by_class = defaultdict(list)
    for session_id, label in session_to_label.items():
        by_class[label].append(session_id)

    rng = random.Random(seed)
    split = {"train": [], "val": [], "test": []}
    for _label, sessions in by_class.items():
        sessions = sorted(set(sessions))
        rng.shuffle(sessions)
        n = len(sessions)
        if n == 1:
            split["train"].extend(sessions)
            continue
        train_n = max(1, round(n * 0.70))
        val_n = max(1, round(n * 0.15)) if n >= 3 else 0
        if train_n + val_n >= n:
            train_n = max(1, n - 1)
            val_n = 0 if n == 2 else 1
        split["train"].extend(sessions[:train_n])
        split["val"].extend(sessions[train_n : train_n + val_n])
        split["test"].extend(sessions[train_n + val_n :])
    return {key: sorted(value) for key, value in split.items()}


def build_summary(rows: list[dict], validation_reports: dict[str, dict], args: argparse.Namespace) -> tuple[dict, list[dict]]:
    windows_by_class = defaultdict(int)
    sessions_by_class = defaultdict(set)
    duration_by_class = defaultdict(float)
    losses = []
    warnings = []

    for row in rows:
        label = row.get("y_label") or row.get("event_label") or row.get("label") or "unknown"
        windows_by_class[label] += 1
        sessions_by_class[label].add(row["session_id"])
        duration_by_class[label] += to_float(row.get("window_s"))
        losses.append(to_float(row.get("packet_loss_pct")))

    session_to_sources = defaultdict(set)
    for row in rows:
        session_to_sources[row["session_id"]].add(Path(row.get("source_file", "")).stem)

    session_quality = {}
    sample_rates = []
    for session_id, sources in session_to_sources.items():
        related = [validation_reports[source] for source in sources if source in validation_reports]
        if not related:
            session_quality[session_id] = {"validation_found": False}
            continue
        avg_loss = statistics.mean(report.get("packet_loss_pct", 0.0) for report in related)
        rates = [report.get("sample_rate_avg_hz") for report in related if report.get("sample_rate_avg_hz") is not None]
        if rates:
            sample_rates.extend(rates)
        session_quality[session_id] = {
            "validation_found": True,
            "packet_loss_pct": round(avg_loss, 3),
            "bad_for_fine_training": any(report.get("bad_for_fine_training") for report in related),
        }
        if avg_loss > args.max_loss_warning_pct:
            warnings.append(f"Session {session_id} has high packet loss: {avg_loss:.2f}%")

    for label, count in windows_by_class.items():
        if count < args.min_windows_per_class:
            warnings.append(f"Class {label} has few windows: {count}")
        if len(sessions_by_class[label]) < args.min_sessions_per_class:
            warnings.append(f"Class {label} has few sessions: {len(sessions_by_class[label])}")

    split = split_sessions_by_class(rows, args.seed)
    class_rows = []
    for label in sorted(windows_by_class):
        class_rows.append(
            {
                "class": label,
                "windows": windows_by_class[label],
                "sessions": len(sessions_by_class[label]),
                "duration_s_from_windows": round(duration_by_class[label], 3),
                "duration_min_from_windows": round(duration_by_class[label] / 60.0, 3),
            }
        )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_windows": len(rows),
        "total_sessions": len({row["session_id"] for row in rows}),
        "classes": class_rows,
        "packet_loss_pct_avg_from_windows": round(statistics.mean(losses), 3) if losses else None,
        "sample_rate_avg_hz_from_validation": round(statistics.mean(sample_rates), 3) if sample_rates else None,
        "train_val_test_split_by_session": split,
        "split_policy": "70% train, 15% validation, 15% test by complete session, grouped per class",
        "session_quality": session_quality,
        "warnings": warnings,
    }
    return summary, class_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build CSI dataset report.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-windows-per-class", type=int, default=10)
    parser.add_argument("--min-sessions-per-class", type=int, default=2)
    parser.add_argument("--max-loss-warning-pct", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    processed_dir = base_dir / "data" / "processed"
    reports_dir = base_dir / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(processed_dir / "window_metadata.csv")
    validation_reports = load_validation_reports(reports_dir)
    summary, class_rows = build_summary(rows, validation_reports, args)

    json_file = reports_dir / "dataset_summary.json"
    csv_file = reports_dir / "dataset_summary.csv"
    with open(json_file, "w", encoding="utf-8") as fd:
        json.dump(summary, fd, indent=2, ensure_ascii=False)

    with open(csv_file, "w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(
            fd,
            fieldnames=[
                "class",
                "windows",
                "sessions",
                "duration_s_from_windows",
                "duration_min_from_windows",
            ],
        )
        writer.writeheader()
        writer.writerows(class_rows)

    print(f"Total windows: {summary['total_windows']}")
    print(f"Total sessions: {summary['total_sessions']}")
    print("Suggested split by session:")
    for split_name, sessions in summary["train_val_test_split_by_session"].items():
        print(f"  {split_name}: {sessions}")
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"  - {warning}")
    print(f"Saved {json_file}")
    print(f"Saved {csv_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

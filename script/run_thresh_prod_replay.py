#!/usr/bin/env python3
"""Replay SCREENING-28 production THRESH-002 raise on F-Droid v2."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.run_thresh_calibrate_train_test import (
        DEFAULT_METRIC,
        DEFAULT_SEED,
        DEFAULT_SELECTED_LAYERS,
        DEFAULT_TRAIN_RATIO,
        build_ground_truth_pairs,
        discover_apks,
        load_app_records,
        metrics_at_threshold,
        score_pairs,
        split_train_test,
    )
    from script.screening_runner import THRESH_002_BASELINE, THRESH_002_PRODUCTION
    from script.shared_data_store import fdroid_v2_apks_dir
except Exception:
    from run_thresh_calibrate_train_test import (  # type: ignore[no-redef]
        DEFAULT_METRIC,
        DEFAULT_SEED,
        DEFAULT_SELECTED_LAYERS,
        DEFAULT_TRAIN_RATIO,
        build_ground_truth_pairs,
        discover_apks,
        load_app_records,
        metrics_at_threshold,
        score_pairs,
        split_train_test,
    )
    from screening_runner import THRESH_002_BASELINE, THRESH_002_PRODUCTION  # type: ignore[no-redef]
    from shared_data_store import fdroid_v2_apks_dir  # type: ignore[no-redef]


ARTIFACT_ID = "SCREENING-28-THRESH-RAISE"
DEFAULT_OUT = PROJECT_ROOT / "experiments" / "artifacts" / ARTIFACT_ID / "report.json"


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _threshold_replay(scored_pairs: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    metrics = metrics_at_threshold(scored_pairs, threshold)
    shortlist_size = int(metrics["tp"]) + int(metrics["fp"])
    return {
        **metrics,
        "shortlist_size": shortlist_size,
        "preliminary_positive_count": shortlist_size,
        "preliminary_negative_count": int(metrics["fn"]) + int(metrics["tn"]),
    }


def _delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    return {
        "shortlist_size": int(after["shortlist_size"]) - int(before["shortlist_size"]),
        "precision": _round_metric(float(after["precision"]) - float(before["precision"])),
        "recall": _round_metric(float(after["recall"]) - float(before["recall"])),
        "f1": _round_metric(float(after["f1"]) - float(before["f1"])),
        "fpr": _round_metric(float(after["fpr"]) - float(before["fpr"])),
    }


def _segment_report(
    name: str,
    records: Sequence[dict[str, Any]],
    *,
    baseline_threshold: float,
    production_threshold: float,
    selected_layers: Sequence[str],
    metric: str,
) -> dict[str, Any]:
    pairs = build_ground_truth_pairs(records)
    scored = score_pairs(
        records,
        pairs,
        selected_layers=selected_layers,
        metric=metric,
    )
    baseline = _threshold_replay(scored, baseline_threshold)
    production = _threshold_replay(scored, production_threshold)
    clone_count = sum(1 for pair in scored if pair["label"] == "clone")
    return {
        "name": name,
        "documents": len(records),
        "pairs_total": len(scored),
        "pairs_clone": clone_count,
        "pairs_non_clone": len(scored) - clone_count,
        "baseline": baseline,
        "production": production,
        "delta_production_minus_baseline": _delta(production, baseline),
    }


def _limit(paths: Sequence[Path], max_apks: int) -> list[Path]:
    if max_apks <= 0:
        return list(paths)
    return list(paths[:max_apks])


def build_report(
    *,
    corpus_dir: str | Path,
    seed: int = DEFAULT_SEED,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
    selected_layers: Sequence[str] = DEFAULT_SELECTED_LAYERS,
    metric: str = DEFAULT_METRIC,
    baseline_threshold: float = THRESH_002_BASELINE,
    production_threshold: float = THRESH_002_PRODUCTION,
    max_apks_per_split: int = 0,
) -> dict[str, Any]:
    corpus_path = Path(corpus_dir).expanduser().resolve()
    apk_paths = discover_apks(corpus_path)
    train_paths, test_paths = split_train_test(
        apk_paths,
        train_ratio=train_ratio,
        seed=seed,
    )
    replay_train_paths = _limit(train_paths, max_apks_per_split)
    replay_test_paths = _limit(test_paths, max_apks_per_split)

    train_records = load_app_records(replay_train_paths)
    test_records = load_app_records(replay_test_paths)
    segments = [
        _segment_report(
            "train",
            train_records,
            baseline_threshold=baseline_threshold,
            production_threshold=production_threshold,
            selected_layers=selected_layers,
            metric=metric,
        ),
        _segment_report(
            "test",
            test_records,
            baseline_threshold=baseline_threshold,
            production_threshold=production_threshold,
            selected_layers=selected_layers,
            metric=metric,
        ),
    ]

    warnings: list[str] = []
    if len(apk_paths) != 350:
        warnings.append("Expected 350 F-Droid v2 APKs, found {}.".format(len(apk_paths)))
    if max_apks_per_split > 0:
        warnings.append(
            "Replay used a per-split mini-corpus limit of {} APKs.".format(
                max_apks_per_split
            )
        )
    for segment in segments:
        if segment["pairs_clone"] == 0:
            warnings.append(
                "{} segment has no clone pairs under package/signature heuristic.".format(
                    segment["name"]
                )
            )

    return {
        "schema_version": "screening-thresh-production-replay-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_id": ARTIFACT_ID,
        "status": "ok" if not warnings else "ok_with_warnings",
        "calibration_source": {
            "artifact_id": "SCREENING-27-THRESH-CALIBRATE",
            "commit": "b7928f9",
            "optimal_threshold": float(production_threshold),
        },
        "threshold_change": {
            "baseline": float(baseline_threshold),
            "production": float(production_threshold),
        },
        "corpus": {
            "name": "F-Droid v2",
            "path": str(corpus_path),
            "apk_count": len(apk_paths),
            "train_apk_count": len(train_paths),
            "test_apk_count": len(test_paths),
            "replay_train_apk_count": len(replay_train_paths),
            "replay_test_apk_count": len(replay_test_paths),
            "train_ratio": float(train_ratio),
            "seed": int(seed),
        },
        "screening": {
            "selected_layers": list(selected_layers),
            "metric": metric,
            "positive_policy": "score >= threshold => preliminary_positive",
            "replay_mode": "scoring_only_no_candidate_row_materialization",
        },
        "ground_truth_policy": {
            "label_clone_when": "same package_name and same signing key",
            "package_source": "metadata package_name token, fallback to F-Droid stem before final _version",
            "signature_source": "APK signing certificate SHA-256 chain, fallback to metadata signing_prefix token",
        },
        "coordination": {
            "layer_weights": "Not modified here; DEEP-28 owns new LAYER_WEIGHTS.",
        },
        "warnings": warnings,
        "segments": segments,
    }


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _parse_layers(raw_value: str) -> list[str]:
    layers = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not layers:
        raise argparse.ArgumentTypeError("selected layers must not be empty")
    return layers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay production THRESH-002 raise on F-Droid v2 train/test."
    )
    parser.add_argument(
        "--corpus-dir",
        "--corpus_dir",
        default=str(fdroid_v2_apks_dir()),
        help="F-Droid v2 APK corpus directory.",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output report.json path.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--train-ratio", "--train_ratio", type=float, default=DEFAULT_TRAIN_RATIO)
    parser.add_argument(
        "--selected-layers",
        "--selected_layers",
        type=_parse_layers,
        default=list(DEFAULT_SELECTED_LAYERS),
        help="Comma-separated screening layers. Default: code,metadata.",
    )
    parser.add_argument("--metric", default=DEFAULT_METRIC)
    parser.add_argument("--baseline-threshold", type=float, default=THRESH_002_BASELINE)
    parser.add_argument("--production-threshold", type=float, default=THRESH_002_PRODUCTION)
    parser.add_argument(
        "--max-apks-per-split",
        type=int,
        default=0,
        help="Optional deterministic mini-corpus cap per split. 0 means full split.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(
        corpus_dir=args.corpus_dir,
        seed=args.seed,
        train_ratio=args.train_ratio,
        selected_layers=args.selected_layers,
        metric=args.metric,
        baseline_threshold=args.baseline_threshold,
        production_threshold=args.production_threshold,
        max_apks_per_split=args.max_apks_per_split,
    )
    report_path = write_report(args.out, report)
    print(report_path)


if __name__ == "__main__":
    main()

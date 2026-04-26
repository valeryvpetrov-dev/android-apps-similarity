#!/usr/bin/env python3
"""Calibrate MinHash LSH shortlist recall for SCREENING-24."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from minhash_lsh import LSHIndex, MinHashSignature
from screening_runner import (
    aggregate_features,
    calculate_pair_score,
    discover_app_records_from_apk_root,
    validate_app_records,
)


DEFAULT_SELECTED_LAYERS = ["code", "metadata"]
DEFAULT_SEED = 42
DEFAULT_TARGET_RECALL = 0.85


def _parse_int_grid(raw_value: str) -> list[int]:
    values: list[int] = []
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    if not values:
        raise argparse.ArgumentTypeError("grid must contain at least one integer")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LSH num_perm/bands recall calibration over an APK corpus."
    )
    parser.add_argument("--corpus_dir", required=True, help="Directory with APK corpus.")
    parser.add_argument("--out", required=True, help="Output report.json path.")
    parser.add_argument(
        "--num_perm_grid",
        type=_parse_int_grid,
        default=[64, 128, 256],
        help="Comma-separated num_perm values. Default: 64,128,256.",
    )
    parser.add_argument(
        "--bands_grid",
        type=_parse_int_grid,
        default=[16, 32, 64],
        help="Comma-separated bands values. Default: 16,32,64.",
    )
    parser.add_argument("--thresh", type=float, default=0.28)
    return parser.parse_args()


def _pair_key(app_a: dict[str, Any], app_b: dict[str, Any]) -> tuple[str, str]:
    return tuple(sorted((str(app_a["app_id"]), str(app_b["app_id"]))))


def _build_lsh_shortlist_pairs(
    app_records: list[dict[str, Any]],
    *,
    selected_layers: list[str],
    num_perm: int,
    bands: int,
    seed: int,
) -> set[tuple[str, str]]:
    index = LSHIndex(num_perm=num_perm, bands=bands)
    signatures: dict[str, MinHashSignature] = {}
    for record in app_records:
        app_id = str(record["app_id"])
        signature = MinHashSignature.from_features(
            aggregate_features(record, selected_layers),
            num_perm=num_perm,
            seed=seed,
        )
        signatures[app_id] = signature
        index.add(app_id, signature)

    shortlist_pairs: set[tuple[str, str]] = set()
    for record in app_records:
        query_id = str(record["app_id"])
        for candidate_id in index.query(signatures[query_id]):
            if candidate_id == query_id:
                continue
            shortlist_pairs.add(tuple(sorted((query_id, candidate_id))))
    return shortlist_pairs


def _score_pairs(
    app_records: list[dict[str, Any]],
    *,
    selected_layers: list[str],
    thresh: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for app_a, app_b in combinations(app_records, 2):
        full_score = float(
            calculate_pair_score(
                app_a=app_a,
                app_b=app_b,
                metric="jaccard",
                selected_layers=selected_layers,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        )
        rows.append(
            {
                "pair": _pair_key(app_a, app_b),
                "full_score": full_score,
                "passed_thresh": full_score >= thresh,
            }
        )
    return rows


def _select_best_by_recall(per_config: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid_rows = [row for row in per_config if row.get("status") == "ok"]
    if not valid_rows:
        return None
    return dict(
        max(
            valid_rows,
            key=lambda row: (
                float(row["recall_at_shortlist"]),
                -int(row["shortlist_size"]),
                int(row["num_perm"]),
                int(row["bands"]),
            ),
        )
    )


def _select_best_balanced(
    per_config: list[dict[str, Any]],
    *,
    target_recall: float,
) -> dict[str, Any] | None:
    valid_rows = [row for row in per_config if row.get("status") == "ok"]
    if not valid_rows:
        return None
    target_rows = [
        row for row in valid_rows if float(row["recall_at_shortlist"]) >= target_recall
    ]
    if target_rows:
        return dict(
            min(
                target_rows,
                key=lambda row: (
                    int(row["shortlist_size"]),
                    -float(row["recall_at_shortlist"]),
                    int(row["num_perm"]),
                    int(row["bands"]),
                ),
            )
        )
    return _select_best_by_recall(valid_rows)


def run_lsh_recall_grid(
    corpus: list[dict[str, Any]],
    *,
    num_perm_grid: list[int],
    bands_grid: list[int],
    thresh: float = 0.28,
    selected_layers: list[str] | None = None,
    seed: int = DEFAULT_SEED,
    target_recall: float = DEFAULT_TARGET_RECALL,
) -> dict[str, Any]:
    """Measure LSH shortlist recall for each num_perm/bands configuration."""
    selected_layers = list(selected_layers or DEFAULT_SELECTED_LAYERS)
    app_records = sorted(list(corpus), key=lambda item: str(item["app_id"]))
    warnings: list[str] = []

    if len(app_records) < 2:
        return {
            "status": "insufficient_corpus",
            "warnings": ["At least two apps are required to measure pair recall."],
            "config": {
                "selected_layers": selected_layers,
                "metric": "jaccard",
                "threshold": float(thresh),
                "seed": int(seed),
                "target_recall": float(target_recall),
            },
            "per_config": [],
            "best_by_recall": None,
            "best_by_balanced": None,
        }

    validate_app_records(app_records)
    pair_rows = _score_pairs(app_records, selected_layers=selected_layers, thresh=thresh)
    positive_pairs = {row["pair"] for row in pair_rows if row["passed_thresh"]}
    if not positive_pairs:
        return {
            "status": "insufficient_corpus",
            "warnings": ["No pair has full_score >= threshold; recall is not measurable."],
            "config": {
                "selected_layers": selected_layers,
                "metric": "jaccard",
                "threshold": float(thresh),
                "seed": int(seed),
                "target_recall": float(target_recall),
            },
            "per_config": [],
            "best_by_recall": None,
            "best_by_balanced": None,
        }

    per_config: list[dict[str, Any]] = []
    total_pairs = len(pair_rows)
    for num_perm in num_perm_grid:
        for bands in bands_grid:
            row_base = {
                "num_perm": int(num_perm),
                "bands": int(bands),
            }
            try:
                shortlist_pairs = _build_lsh_shortlist_pairs(
                    app_records,
                    selected_layers=selected_layers,
                    num_perm=int(num_perm),
                    bands=int(bands),
                    seed=int(seed),
                )
            except ValueError as exc:
                warning = "Skipping invalid LSH geometry num_perm={}, bands={}: {}".format(
                    num_perm, bands, exc
                )
                warnings.append(warning)
                per_config.append({**row_base, "status": "invalid", "warning": warning})
                continue

            shortlist_positive_pairs = sorted(positive_pairs & shortlist_pairs)
            false_negative_pairs = sorted(positive_pairs - shortlist_pairs)
            true_positive_count = len(shortlist_positive_pairs)
            recall_at_shortlist = true_positive_count / len(positive_pairs)
            per_config.append(
                {
                    **row_base,
                    "status": "ok",
                    "rows_per_band": int(num_perm) // int(bands),
                    "total_pairs": total_pairs,
                    "positive_pairs_above_threshold": len(positive_pairs),
                    "shortlist_size": len(shortlist_pairs),
                    "shortlist_true_positive_count": true_positive_count,
                    "recall_at_shortlist": recall_at_shortlist,
                    "false_negative_rate": 1.0 - recall_at_shortlist,
                    "shortlist_positive_pairs": [
                        [left, right] for left, right in shortlist_positive_pairs
                    ],
                    "false_negative_pairs": [
                        [left, right] for left, right in false_negative_pairs
                    ],
                }
            )

    best_by_recall = _select_best_by_recall(per_config)
    best_by_balanced = _select_best_balanced(
        per_config,
        target_recall=target_recall,
    )
    return {
        "status": "ok" if best_by_recall is not None else "insufficient_corpus",
        "warnings": warnings,
        "config": {
            "selected_layers": selected_layers,
            "metric": "jaccard",
            "threshold": float(thresh),
            "seed": int(seed),
            "target_recall": float(target_recall),
        },
        "per_config": per_config,
        "best_by_recall": best_by_recall,
        "best_by_balanced": best_by_balanced,
    }


def build_cli_report(
    *,
    corpus_dir: str | Path,
    num_perm_grid: list[int],
    bands_grid: list[int],
    thresh: float,
) -> dict[str, Any]:
    corpus_path = Path(corpus_dir).expanduser().resolve()
    records = discover_app_records_from_apk_root(corpus_path)
    grid_report = run_lsh_recall_grid(
        records,
        num_perm_grid=num_perm_grid,
        bands_grid=bands_grid,
        thresh=thresh,
    )
    optimal_config = grid_report["best_by_balanced"]
    return {
        "schema_version": "screening-lsh-recall-calibration-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_dir": str(corpus_path),
        "summary": {
            "total_apps": len(records),
            "total_configs": len(grid_report["per_config"]),
            "target_recall": DEFAULT_TARGET_RECALL,
            "optimal_config": optimal_config,
            "target_recall_met": bool(
                optimal_config
                and float(optimal_config["recall_at_shortlist"]) >= DEFAULT_TARGET_RECALL
            ),
        },
        **grid_report,
    }


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    args = parse_args()
    report = build_cli_report(
        corpus_dir=args.corpus_dir,
        num_perm_grid=args.num_perm_grid,
        bands_grid=args.bands_grid,
        thresh=args.thresh,
    )
    report_path = write_report(args.out, report)
    print(report_path)


if __name__ == "__main__":
    main()

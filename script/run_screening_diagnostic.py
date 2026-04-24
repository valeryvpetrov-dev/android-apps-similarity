#!/usr/bin/env python3
"""Diagnostic run for SCREENING-20-LSH-DIAGNOSTIC."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from m_static_views import compare_m_static_layer
from minhash_lsh import LSHIndex, MinHashSignature
from screening_runner import (
    aggregate_features,
    calculate_pair_score,
    discover_app_records_from_apk_root,
    extract_candidate_index_params,
    extract_screening_stage,
    load_app_records_from_json,
    load_yaml_or_json,
    validate_app_records,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an LSH screening diagnostic over all C(n,2) pairs and write "
            "report.json with shortlist recall / false-negative metrics."
        )
    )
    parser.add_argument(
        "--apk-root",
        default="",
        help="Root directory with APK corpus. Ignored when --apps-features-json is used.",
    )
    parser.add_argument(
        "--apps-features-json",
        default="",
        help="Optional JSON with app_records; useful for synthetic diagnostics/tests.",
    )
    parser.add_argument(
        "--cascade-config",
        required=True,
        help="Path to cascade-config YAML/JSON used for screening.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where report.json will be written.",
    )
    parser.add_argument("--ins-block-sim-threshold", type=float, default=0.80)
    parser.add_argument("--ged-timeout-sec", type=int, default=30)
    parser.add_argument("--processes-count", type=int, default=1)
    parser.add_argument("--threads-count", type=int, default=2)
    return parser.parse_args()


def _load_app_records(
    *,
    app_records: list[dict[str, Any]] | None,
    apk_root: str | Path | None,
    apps_features_json_path: str | Path | None,
) -> list[dict[str, Any]]:
    if app_records is not None:
        records = list(app_records)
    elif apps_features_json_path:
        records = load_app_records_from_json(Path(apps_features_json_path).expanduser().resolve())
    else:
        resolved_apk_root = Path(apk_root or Path(__file__).resolve().parents[1] / "apk")
        records = discover_app_records_from_apk_root(resolved_apk_root.expanduser().resolve())

    validate_app_records(records)
    return sorted(records, key=lambda item: item["app_id"])


def compute_exact_per_view_scores(
    app_a: dict[str, Any],
    app_b: dict[str, Any],
    layers: list[str],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    layers_a = app_a.get("layers", {})
    layers_b = app_b.get("layers", {})
    for layer in layers:
        result = compare_m_static_layer(
            layer,
            layers_a.get(layer, set()),
            layers_b.get(layer, set()),
        )
        scores[layer] = float(result.get("score", 0.0))
    return scores


def build_lsh_shortlist_pairs(
    app_records: list[dict[str, Any]],
    candidate_index_params: dict[str, Any],
) -> set[tuple[str, str]]:
    num_perm = int(candidate_index_params["num_perm"])
    bands = int(candidate_index_params["bands"])
    seed = int(candidate_index_params["seed"])
    index_features = list(candidate_index_params["features"])

    index = LSHIndex(num_perm=num_perm, bands=bands)
    signatures: dict[str, MinHashSignature] = {}
    for record in app_records:
        app_id = str(record["app_id"])
        signature = MinHashSignature.from_features(
            aggregate_features(record, index_features),
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


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def build_diagnostic_report(
    *,
    cascade_config_path: str | Path,
    app_records: list[dict[str, Any]] | None = None,
    apk_root: str | Path | None = None,
    apps_features_json_path: str | Path | None = None,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    processes_count: int = 1,
    threads_count: int = 2,
) -> dict[str, Any]:
    config_path = Path(cascade_config_path).expanduser().resolve()
    config = load_yaml_or_json(config_path)
    selected_layers, metric, threshold = extract_screening_stage(config)
    candidate_index_params = extract_candidate_index_params(
        config,
        default_features=selected_layers,
        metric=metric,
    )
    if candidate_index_params is None:
        raise ValueError("Diagnostic requires stages.screening.candidate_index")

    records = _load_app_records(
        app_records=app_records,
        apk_root=apk_root,
        apps_features_json_path=apps_features_json_path,
    )
    shortlist_pairs = build_lsh_shortlist_pairs(records, candidate_index_params)

    pair_rows: list[dict[str, Any]] = []
    shortlist_true_positive_count = 0
    shortlist_false_positive_count = 0
    positive_pairs_above_threshold = 0
    negative_pairs_below_threshold = 0
    candidate_score_means: list[float] = []

    for app_a, app_b in combinations(records, 2):
        pair_key = tuple(sorted((str(app_a["app_id"]), str(app_b["app_id"]))))
        in_shortlist = pair_key in shortlist_pairs
        full_score = float(
            calculate_pair_score(
                app_a=app_a,
                app_b=app_b,
                metric=metric,
                selected_layers=selected_layers,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
        )
        passed_thresh = full_score >= threshold
        if passed_thresh:
            positive_pairs_above_threshold += 1
            if in_shortlist:
                shortlist_true_positive_count += 1
        else:
            negative_pairs_below_threshold += 1
            if in_shortlist:
                shortlist_false_positive_count += 1

        per_view_scores = compute_exact_per_view_scores(app_a, app_b, selected_layers)
        selected_similarity_score = full_score if in_shortlist else 0.0
        if in_shortlist and passed_thresh:
            candidate_score_means.append(_mean(list(per_view_scores.values())))

        pair_rows.append(
            {
                "query_app_id": app_a["app_id"],
                "candidate_app_id": app_b["app_id"],
                "in_shortlist": in_shortlist,
                "passed_thresh": passed_thresh,
                "full_score": full_score,
                "selected_similarity_score": selected_similarity_score,
                "per_view_scores": per_view_scores,
            }
        )

    total_pairs = len(pair_rows)
    shortlist_size = sum(1 for row in pair_rows if row["in_shortlist"])
    candidate_list_size = sum(
        1 for row in pair_rows if row["in_shortlist"] and row["passed_thresh"]
    )
    recall_at_shortlist = (
        shortlist_true_positive_count / positive_pairs_above_threshold
        if positive_pairs_above_threshold > 0
        else 1.0
    )
    false_negative_rate = 1.0 - recall_at_shortlist
    false_positive_rate = (
        shortlist_false_positive_count / shortlist_size if shortlist_size > 0 else 0.0
    )

    return {
        "schema_version": "screening-diagnostic-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cascade_config_path": str(config_path),
        "apk_root": str(Path(apk_root).expanduser().resolve()) if apk_root else None,
        "screening": {
            "features": list(selected_layers),
            "metric": metric,
            "threshold": float(threshold),
            "candidate_index": {
                "type": candidate_index_params["type"],
                "num_perm": int(candidate_index_params["num_perm"]),
                "bands": int(candidate_index_params["bands"]),
                "seed": int(candidate_index_params["seed"]),
                "features": list(candidate_index_params["features"]),
            },
        },
        "summary": {
            "total_apps": len(records),
            "total_pairs": total_pairs,
            "shortlist_size": shortlist_size,
            "candidate_list_size": candidate_list_size,
            "positive_pairs_above_threshold": positive_pairs_above_threshold,
            "negative_pairs_below_threshold": negative_pairs_below_threshold,
            "shortlist_true_positive_count": shortlist_true_positive_count,
            "shortlist_false_positive_count": shortlist_false_positive_count,
            "recall_at_shortlist": recall_at_shortlist,
            "false_negative_rate": false_negative_rate,
            "false_positive_rate": false_positive_rate,
            "avg_per_view_score_in_candidates": _mean(candidate_score_means),
        },
        "pairs": pair_rows,
    }


def write_report(output_dir: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    report_path = output_path / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


def main() -> None:
    args = parse_args()
    report = build_diagnostic_report(
        cascade_config_path=args.cascade_config,
        apk_root=args.apk_root or None,
        apps_features_json_path=args.apps_features_json or None,
        ins_block_sim_threshold=args.ins_block_sim_threshold,
        ged_timeout_sec=args.ged_timeout_sec,
        processes_count=args.processes_count,
        threads_count=args.threads_count,
    )
    report_path = write_report(args.output_dir, report)
    print(report_path)


if __name__ == "__main__":
    main()

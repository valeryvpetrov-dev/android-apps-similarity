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
DEFAULT_FDROID_V2_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_BASELINE_NUM_PERM = 128
DEFAULT_BASELINE_BANDS = 32
DEFAULT_MAX_SHORTLIST_PAIR_RATIO = 0.30


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
    feature_sets_by_app_id: dict[str, set[str]] | None = None,
) -> set[tuple[str, str]]:
    index = LSHIndex(num_perm=num_perm, bands=bands)
    signatures: dict[str, MinHashSignature] = {}
    for record in app_records:
        app_id = str(record["app_id"])
        feature_set = (
            feature_sets_by_app_id[app_id]
            if feature_sets_by_app_id is not None
            else aggregate_features(record, selected_layers)
        )
        signature = MinHashSignature.from_features(
            feature_set,
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


def _build_feature_sets_by_app_id(
    app_records: list[dict[str, Any]],
    *,
    selected_layers: list[str],
) -> dict[str, set[str]]:
    return {
        str(record["app_id"]): aggregate_features(record, selected_layers)
        for record in app_records
    }


def _score_pairs(
    app_records: list[dict[str, Any]],
    *,
    selected_layers: list[str],
    thresh: float,
    clone_threshold: float | None = None,
    feature_sets_by_app_id: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for app_a, app_b in combinations(app_records, 2):
        if feature_sets_by_app_id is not None:
            features_a = feature_sets_by_app_id[str(app_a["app_id"])]
            features_b = feature_sets_by_app_id[str(app_b["app_id"])]
            union_size = len(features_a | features_b)
            full_score = (
                0.0
                if union_size == 0
                else len(features_a & features_b) / union_size
            )
        else:
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
        is_clone = (
            full_score >= thresh
            if clone_threshold is None
            else full_score > clone_threshold
        )
        rows.append(
            {
                "pair": _pair_key(app_a, app_b),
                "full_score": full_score,
                "passed_thresh": full_score >= thresh,
                "is_clone": is_clone,
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
    corpus: list[dict[str, Any]] | None = None,
    *,
    corpus_dir: str | Path | None = None,
    num_perm_grid: list[int],
    bands_grid: list[int],
    thresh: float = 0.28,
    clone_threshold: float | None = None,
    selected_layers: list[str] | None = None,
    seed: int = DEFAULT_SEED,
    target_recall: float = DEFAULT_TARGET_RECALL,
    corpus_name: str | None = None,
    include_pair_details: bool = True,
) -> dict[str, Any]:
    """Measure LSH shortlist recall for each num_perm/bands configuration."""
    selected_layers = list(selected_layers or DEFAULT_SELECTED_LAYERS)
    if corpus is None and corpus_dir is None:
        raise ValueError("Either corpus or corpus_dir is required")
    if corpus is not None and corpus_dir is not None:
        raise ValueError("Pass either corpus or corpus_dir, not both")

    corpus_path: Path | None = None
    if corpus_dir is not None:
        corpus_path = Path(corpus_dir).expanduser().resolve()
        corpus = discover_app_records_from_apk_root(corpus_path)

    assert corpus is not None
    app_records = sorted(list(corpus), key=lambda item: str(item["app_id"]))
    enriched_report = corpus_path is not None or clone_threshold is not None
    warnings: list[str] = []
    clone_policy = (
        {
            "source": "threshold",
            "threshold": float(thresh),
            "operator": ">=",
        }
        if clone_threshold is None
        else {
            "source": "synthetic_full_score",
            "threshold": float(clone_threshold),
            "operator": ">",
        }
    )

    def _base_report(
        *,
        status: str,
        warnings_value: list[str],
        per_config: list[dict[str, Any]],
        best_by_recall: dict[str, Any] | None,
        best_by_balanced: dict[str, Any] | None,
        pair_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        report = {
            "status": status,
            "warnings": warnings_value,
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
        if not enriched_report:
            return report
        n_documents = len(app_records)
        n_pairs_total = n_documents * (n_documents - 1) // 2
        n_pairs_above_threshold = (
            sum(1 for row in pair_rows if row["passed_thresh"])
            if pair_rows is not None
            else 0
        )
        n_pairs_clone = (
            sum(1 for row in pair_rows if row["is_clone"]) if pair_rows is not None else 0
        )
        report.update(
            {
                "corpus": {
                    "name": corpus_name
                    or ("F-Droid v2" if corpus_path else "in-memory"),
                    "path": str(corpus_path) if corpus_path is not None else None,
                    "n_documents": n_documents,
                },
                "n_pairs_total": n_pairs_total,
                "n_pairs_above_threshold": n_pairs_above_threshold,
                "n_pairs_clone": n_pairs_clone,
                "clone_policy": clone_policy,
                "optimal_config": best_by_balanced,
            }
        )
        return report

    if len(app_records) < 2:
        return _base_report(
            status="insufficient_corpus",
            warnings_value=["At least two apps are required to measure pair recall."],
            per_config=[],
            best_by_recall=None,
            best_by_balanced=None,
        )

    validate_app_records(app_records)
    feature_sets_by_app_id = _build_feature_sets_by_app_id(
        app_records,
        selected_layers=selected_layers,
    )
    pair_rows = _score_pairs(
        app_records,
        selected_layers=selected_layers,
        thresh=thresh,
        clone_threshold=clone_threshold,
        feature_sets_by_app_id=feature_sets_by_app_id,
    )
    threshold_pairs = {row["pair"] for row in pair_rows if row["passed_thresh"]}
    clone_pairs = {row["pair"] for row in pair_rows if row["is_clone"]}
    if not clone_pairs:
        return _base_report(
            status="insufficient_corpus",
            warnings_value=["No clone pair was found; recall is not measurable."],
            per_config=[],
            best_by_recall=None,
            best_by_balanced=None,
            pair_rows=pair_rows,
        )

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
                    feature_sets_by_app_id=feature_sets_by_app_id,
                )
            except ValueError as exc:
                warning = "Skipping invalid LSH geometry num_perm={}, bands={}: {}".format(
                    num_perm, bands, exc
                )
                warnings.append(warning)
                per_config.append({**row_base, "status": "invalid", "warning": warning})
                continue

            shortlist_positive_pairs = sorted(clone_pairs & shortlist_pairs)
            false_negative_pairs = sorted(clone_pairs - shortlist_pairs)
            true_positive_count = len(shortlist_positive_pairs)
            recall_at_shortlist = true_positive_count / len(clone_pairs)
            row = {
                **row_base,
                "status": "ok",
                "rows_per_band": int(num_perm) // int(bands),
                "total_pairs": total_pairs,
                "positive_pairs_above_threshold": len(threshold_pairs),
                "clone_pairs": len(clone_pairs),
                "shortlist_size": len(shortlist_pairs),
                "shortlist_pair_ratio": (
                    len(shortlist_pairs) / total_pairs if total_pairs else 0.0
                ),
                "shortlist_true_positive_count": true_positive_count,
                "recall_at_shortlist": recall_at_shortlist,
                "false_negative_rate": 1.0 - recall_at_shortlist,
            }
            if include_pair_details:
                row["shortlist_positive_pairs"] = [
                    [left, right] for left, right in shortlist_positive_pairs
                ]
                row["false_negative_pairs"] = [
                    [left, right] for left, right in false_negative_pairs
                ]
            per_config.append(row)

    best_by_recall = _select_best_by_recall(per_config)
    best_by_balanced = _select_best_balanced(
        per_config,
        target_recall=target_recall,
    )
    return _base_report(
        status="ok" if best_by_recall is not None else "insufficient_corpus",
        warnings_value=warnings,
        per_config=per_config,
        best_by_recall=best_by_recall,
        best_by_balanced=best_by_balanced,
        pair_rows=pair_rows,
    )


def _find_config_row(
    per_config: list[dict[str, Any]],
    *,
    num_perm: int,
    bands: int,
) -> dict[str, Any] | None:
    for row in per_config:
        if int(row.get("num_perm", -1)) == int(num_perm) and int(
            row.get("bands", -1)
        ) == int(bands):
            return dict(row)
    return None


def _build_decision(
    *,
    optimal_config: dict[str, Any] | None,
    baseline_config: dict[str, Any] | None,
    target_recall: float,
    max_shortlist_pair_ratio: float,
) -> dict[str, Any]:
    if optimal_config is None or baseline_config is None:
        return {
            "production_default_changed": False,
            "reason": "No comparable optimal/baseline configuration was found.",
        }

    baseline_recall = float(baseline_config["recall_at_shortlist"])
    optimal_recall = float(optimal_config["recall_at_shortlist"])
    baseline_shortlist = int(baseline_config["shortlist_size"])
    optimal_shortlist = int(optimal_config["shortlist_size"])
    optimal_ratio = float(optimal_config.get("shortlist_pair_ratio", 1.0))
    same_geometry = (
        int(optimal_config["num_perm"]) == int(baseline_config["num_perm"])
        and int(optimal_config["bands"]) == int(baseline_config["bands"])
    )
    recall_delta = optimal_recall - baseline_recall
    substantially_better = (
        not same_geometry
        and optimal_recall >= target_recall
        and optimal_ratio <= max_shortlist_pair_ratio
        and recall_delta >= 0.05
        and optimal_shortlist <= max(1, int(baseline_shortlist * 1.50))
    )
    reason = (
        "Optimal config improves recall by >=0.05 within shortlist budget."
        if substantially_better
        else (
            "Production default kept: optimal config is baseline or improvement "
            "is not substantial under the recall/shortlist policy."
        )
    )
    return {
        "production_default_changed": substantially_better,
        "reason": reason,
        "target_recall": float(target_recall),
        "max_shortlist_pair_ratio": float(max_shortlist_pair_ratio),
        "recall_delta_vs_baseline": recall_delta,
        "shortlist_delta_vs_baseline": optimal_shortlist - baseline_shortlist,
    }


def build_fdroid_report(
    *,
    corpus_dir: str | Path = DEFAULT_FDROID_V2_CORPUS_DIR,
    num_perm_grid: list[int],
    bands_grid: list[int],
    thresh: float = 0.28,
    clone_threshold: float = 0.50,
    baseline_num_perm: int = DEFAULT_BASELINE_NUM_PERM,
    baseline_bands: int = DEFAULT_BASELINE_BANDS,
    max_shortlist_pair_ratio: float = DEFAULT_MAX_SHORTLIST_PAIR_RATIO,
) -> dict[str, Any]:
    grid_report = run_lsh_recall_grid(
        corpus_dir=corpus_dir,
        num_perm_grid=num_perm_grid,
        bands_grid=bands_grid,
        thresh=thresh,
        clone_threshold=clone_threshold,
        target_recall=DEFAULT_TARGET_RECALL,
        corpus_name="F-Droid v2",
        include_pair_details=False,
    )
    baseline_config = _find_config_row(
        grid_report["per_config"],
        num_perm=baseline_num_perm,
        bands=baseline_bands,
    )
    optimal_config = grid_report.get("optimal_config")
    decision = _build_decision(
        optimal_config=optimal_config,
        baseline_config=baseline_config,
        target_recall=DEFAULT_TARGET_RECALL,
        max_shortlist_pair_ratio=max_shortlist_pair_ratio,
    )
    return {
        "schema_version": "screening-lsh-recall-calibration-fdroid-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_id": "SCREENING-25-LSH-FDROID",
        "baseline_config": baseline_config,
        "decision": decision,
        **grid_report,
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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import combinations
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.code_view_v4 import TLSH_DIFF_MAX
    from script.code_view_v4_shingled import (
        DEFAULT_SHINGLE_SIZE,
        compute_code_v4_shingled,
        extract_code_view_v4_shingled,
    )
except Exception:
    from code_view_v4 import TLSH_DIFF_MAX  # type: ignore[no-redef]
    from code_view_v4_shingled import (  # type: ignore[no-redef]
        DEFAULT_SHINGLE_SIZE,
        compute_code_v4_shingled,
        extract_code_view_v4_shingled,
    )


TLSH_DIFF_MAX_GRID = [100, 150, 200, 250, 300]
SHINGLE_SIZE_GRID = [3, 4, 5, 6]
DEFAULT_OUT = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "REPR-21-TLSH-SHINGLE-ROC"
    / "report.json"
)


def _normalize_apk_key(value: str | Path) -> str:
    return Path(value).name


def _discover_apks(corpus_dir: Path) -> list[Path]:
    return sorted(path for path in corpus_dir.rglob("*.apk") if path.is_file())


def _infer_apk_group(apk_path: Path) -> str:
    stem = apk_path.stem
    parent = apk_path.parent.name
    if parent and stem.startswith(f"{parent}-release"):
        return parent
    return stem


def _canonical_label(raw: str) -> str:
    norm = raw.strip().lower().replace("-", "_")
    if norm in {"clone", "clones"}:
        return "clone"
    if norm in {"non_clone", "nonclone", "negative"}:
        return "non_clone"
    raise ValueError(f"Unsupported pair label: {raw!r}")


def _resolve_csv_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str:
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError(
        "CSV must contain one of columns: {}".format(", ".join(candidates))
    )


def _load_ground_truth_pairs(
    ground_truth_csv: Path,
    apks_by_name: dict[str, Path],
) -> list[dict]:
    with ground_truth_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError("ground_truth_csv has no header")
        apk_a_col = _resolve_csv_column(
            fieldnames,
            ("apk_a", "apk1", "left_apk", "query_apk", "query_apk_path"),
        )
        apk_b_col = _resolve_csv_column(
            fieldnames,
            ("apk_b", "apk2", "right_apk", "candidate_apk", "candidate_apk_path"),
        )
        label_col = _resolve_csv_column(
            fieldnames,
            ("label", "ground_truth", "pair_label", "is_clone"),
        )

        pairs: list[dict] = []
        for row in reader:
            apk_a = _normalize_apk_key(row[apk_a_col])
            apk_b = _normalize_apk_key(row[apk_b_col])
            if apk_a not in apks_by_name or apk_b not in apks_by_name or apk_a == apk_b:
                continue
            label = _canonical_label(str(row[label_col]))
            pairs.append(
                {
                    "apk_a": apk_a,
                    "apk_b": apk_b,
                    "label": label,
                }
            )
    return pairs


def _build_heuristic_pairs(apk_paths: list[Path]) -> tuple[list[dict], dict[str, str]]:
    groups = {
        path.name: _infer_apk_group(path)
        for path in apk_paths
    }
    pairs: list[dict] = []
    for apk_a, apk_b in combinations(apk_paths, 2):
        label = "clone" if groups[apk_a.name] == groups[apk_b.name] else "non_clone"
        pairs.append(
            {
                "apk_a": apk_a.name,
                "apk_b": apk_b.name,
                "label": label,
            }
        )
    return pairs, groups


def _safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return num / den


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _roc_points(scored_pairs: list[dict]) -> list[dict]:
    thresholds = sorted(
        set([0.0, 1.0] + [float(pair["score"]) for pair in scored_pairs]),
        reverse=True,
    )
    points: list[dict] = []
    for threshold in thresholds:
        tp = fp = fn = tn = 0
        for pair in scored_pairs:
            predicted_clone = float(pair["score"]) >= threshold
            is_clone = pair["label"] == "clone"
            if predicted_clone and is_clone:
                tp += 1
            elif predicted_clone and not is_clone:
                fp += 1
            elif is_clone:
                fn += 1
            else:
                tn += 1
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        fpr = _safe_div(fp, fp + tn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        points.append(
            {
                "threshold": _round_metric(threshold),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": _round_metric(precision),
                "recall": _round_metric(recall),
                "tpr": _round_metric(recall),
                "fpr": _round_metric(fpr),
                "f1": _round_metric(f1),
                "youden_j": _round_metric(recall - fpr),
            }
        )
    return points


def _select_operating_point(roc_points: list[dict]) -> dict:
    return max(
        roc_points,
        key=lambda point: (
            point["f1"],
            point["youden_j"],
            -point["fpr"],
            point["tpr"],
            point["threshold"],
        ),
    )


def _select_optimal(metrics: list[dict]) -> dict:
    best = max(
        metrics,
        key=lambda metric: (
            metric["f1"],
            metric["youden_j"],
            -abs(metric["tlsh_diff_max"] - TLSH_DIFF_MAX),
            -abs(metric["shingle_size"] - DEFAULT_SHINGLE_SIZE),
        ),
    )
    return {
        "tlsh_diff_max": best["tlsh_diff_max"],
        "shingle_size": best["shingle_size"],
        "by_f1": best["f1"],
        "by_youden_j": best["youden_j"],
        "decision_threshold": best["decision_threshold"],
    }


def validate_report_structure(report: dict) -> None:
    if report.get("status") == "insufficient_corpus":
        for key in (
            "status",
            "warning",
            "corpus_size",
            "pairs_clone",
            "pairs_non_clone",
            "tlsh_diff_max_grid",
            "shingle_size_grid",
        ):
            if key not in report:
                raise ValueError(f"Missing report key: {key}")
        return

    required_top_level = (
        "tlsh_diff_max_grid",
        "shingle_size_grid",
        "per_param_metrics",
        "optimal",
        "corpus_size",
        "pairs_clone",
        "pairs_non_clone",
    )
    for key in required_top_level:
        if key not in report:
            raise ValueError(f"Missing report key: {key}")
    if report["tlsh_diff_max_grid"] != TLSH_DIFF_MAX_GRID:
        raise ValueError("Unexpected tlsh_diff_max_grid")
    if report["shingle_size_grid"] != SHINGLE_SIZE_GRID:
        raise ValueError("Unexpected shingle_size_grid")
    expected_metrics = len(TLSH_DIFF_MAX_GRID) * len(SHINGLE_SIZE_GRID)
    if len(report["per_param_metrics"]) != expected_metrics:
        raise ValueError("Unexpected per_param_metrics size")
    for metric in report["per_param_metrics"]:
        for key in (
            "tlsh_diff_max",
            "shingle_size",
            "precision",
            "recall",
            "f1",
            "fpr",
            "tpr",
        ):
            if key not in metric:
                raise ValueError(f"Missing metric key: {key}")
    for key in ("tlsh_diff_max", "shingle_size", "by_f1", "by_youden_j"):
        if key not in report["optimal"]:
            raise ValueError(f"Missing optimal key: {key}")


def build_report(
    corpus_dir: Path,
    ground_truth_csv: Path | None = None,
) -> dict:
    apk_paths = _discover_apks(corpus_dir)
    apks_by_name = {path.name: path for path in apk_paths}
    corpus_size = len(apk_paths)

    if corpus_size <= 3:
        return {
            "status": "insufficient_corpus",
            "warning": "Need more than 3 APKs for ROC calibration.",
            "corpus_size": corpus_size,
            "pairs_clone": 0,
            "pairs_non_clone": 0,
            "tlsh_diff_max_grid": TLSH_DIFF_MAX_GRID,
            "shingle_size_grid": SHINGLE_SIZE_GRID,
            "per_param_metrics": [],
            "optimal": None,
        }

    if ground_truth_csv is not None:
        pairs = _load_ground_truth_pairs(ground_truth_csv, apks_by_name)
        apk_groups = {}
        ground_truth_source = str(ground_truth_csv)
    else:
        pairs, apk_groups = _build_heuristic_pairs(apk_paths)
        ground_truth_source = "heuristic_apk_groups"

    pairs_clone = sum(1 for pair in pairs if pair["label"] == "clone")
    pairs_non_clone = sum(1 for pair in pairs if pair["label"] == "non_clone")
    if pairs_clone == 0 or pairs_non_clone == 0:
        return {
            "status": "insufficient_corpus",
            "warning": (
                "Need at least one clone pair and one non-clone pair for ROC "
                "calibration."
            ),
            "corpus_size": corpus_size,
            "pairs_clone": pairs_clone,
            "pairs_non_clone": pairs_non_clone,
            "tlsh_diff_max_grid": TLSH_DIFF_MAX_GRID,
            "shingle_size_grid": SHINGLE_SIZE_GRID,
            "per_param_metrics": [],
            "optimal": None,
        }

    per_param_metrics: list[dict] = []
    feature_cache: dict[int, dict[str, dict | None]] = {}

    for shingle_size in SHINGLE_SIZE_GRID:
        feature_cache[shingle_size] = {
            apk_path.name: extract_code_view_v4_shingled(
                apk_path,
                shingle_size=shingle_size,
            )
            for apk_path in apk_paths
        }
        for tlsh_diff_max in TLSH_DIFF_MAX_GRID:
            scored_pairs = []
            for pair in pairs:
                features_a = feature_cache[shingle_size][pair["apk_a"]]
                features_b = feature_cache[shingle_size][pair["apk_b"]]
                result = compute_code_v4_shingled(
                    pair["apk_a"],
                    pair["apk_b"],
                    shingle_size=shingle_size,
                    tlsh_diff_max=tlsh_diff_max,
                    features_a=features_a,
                    features_b=features_b,
                )
                scored_pairs.append(
                    {
                        "apk_a": pair["apk_a"],
                        "apk_b": pair["apk_b"],
                        "label": pair["label"],
                        "score": result["score"],
                    }
                )
            operating_point = _select_operating_point(_roc_points(scored_pairs))
            per_param_metrics.append(
                {
                    "tlsh_diff_max": tlsh_diff_max,
                    "shingle_size": shingle_size,
                    "precision": operating_point["precision"],
                    "recall": operating_point["recall"],
                    "f1": operating_point["f1"],
                    "fpr": operating_point["fpr"],
                    "tpr": operating_point["tpr"],
                    "youden_j": operating_point["youden_j"],
                    "decision_threshold": operating_point["threshold"],
                }
            )

    report = {
        "status": "ok",
        "ground_truth_source": ground_truth_source,
        "corpus_dir": str(corpus_dir),
        "corpus_apks": [path.name for path in apk_paths],
        "apk_groups": apk_groups,
        "tlsh_diff_max_grid": TLSH_DIFF_MAX_GRID,
        "shingle_size_grid": SHINGLE_SIZE_GRID,
        "per_param_metrics": per_param_metrics,
        "optimal": _select_optimal(per_param_metrics),
        "corpus_size": corpus_size,
        "pairs_clone": pairs_clone,
        "pairs_non_clone": pairs_non_clone,
    }
    validate_report_structure(report)
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate TLSH_DIFF_MAX x shingle_size for code_view_v4_shingled "
            "on a labeled APK corpus."
        )
    )
    parser.add_argument(
        "--corpus_dir",
        type=Path,
        required=True,
        help="Directory with APK files (searched recursively).",
    )
    parser.add_argument(
        "--ground_truth_csv",
        type=Path,
        default=None,
        help=(
            "Optional CSV with pair labels. Without it, heuristic clone/non-clone "
            "labels are inferred from apk_groups."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Path to JSON report (default: {DEFAULT_OUT}).",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    if not args.corpus_dir.exists() or not args.corpus_dir.is_dir():
        print(f"Corpus dir not found: {args.corpus_dir}", file=sys.stderr)
        return 1
    if args.ground_truth_csv is not None and not args.ground_truth_csv.exists():
        print(f"ground_truth_csv not found: {args.ground_truth_csv}", file=sys.stderr)
        return 1

    report = build_report(args.corpus_dir, args.ground_truth_csv)
    validate_report_structure(report)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

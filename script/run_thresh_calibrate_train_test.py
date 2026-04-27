#!/usr/bin/env python3
"""Calibrate THRESH-002 with a fixed train/test protocol on F-Droid v2."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import warnings
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.screening_runner import (
        aggregate_features,
        calculate_pair_score,
        extract_layers_from_apk,
    )
    from script.shared_data_store import fdroid_v2_apks_dir
    from script.signing_view import extract_signing_chain
except Exception:
    from screening_runner import (  # type: ignore[no-redef]
        aggregate_features,
        calculate_pair_score,
        extract_layers_from_apk,
    )
    from shared_data_store import fdroid_v2_apks_dir  # type: ignore[no-redef]
    from signing_view import extract_signing_chain  # type: ignore[no-redef]


ARTIFACT_ID = "SCREENING-27-THRESH-CALIBRATE"
DEFAULT_OUT = PROJECT_ROOT / "experiments" / "artifacts" / ARTIFACT_ID / "report.json"
DEFAULT_SEED = 2
DEFAULT_TRAIN_RATIO = 0.70
DEFAULT_SELECTED_LAYERS = ["code", "metadata"]
DEFAULT_METRIC = "jaccard"
CURRENT_THRESH_002 = 0.28
OVERFIT_MARGIN = 0.02
PACKAGE_VERSION_SUFFIX_RE = re.compile(r"^(.+)_([0-9]+)$")


def threshold_grid(start: float = 0.10, stop: float = 0.90, step: float = 0.05) -> list[float]:
    """Return the fixed threshold sweep used by the calibration report."""
    values: list[float] = []
    current = int(round(start * 100))
    end = int(round(stop * 100))
    delta = int(round(step * 100))
    while current <= end:
        values.append(round(current / 100.0, 10))
        current += delta
    return values


def split_train_test(
    items: Sequence[Any],
    *,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
    seed: int = DEFAULT_SEED,
) -> tuple[list[Any], list[Any]]:
    """Shuffle items with a fixed seed and return train/test partitions."""
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train_ratio must be between 0 and 1")
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    train_size = int(round(len(shuffled) * train_ratio))
    if len(shuffled) > 1:
        train_size = min(max(train_size, 1), len(shuffled) - 1)
    return shuffled[:train_size], shuffled[train_size:]


def _safe_div(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _extract_token_value(tokens: Iterable[str], prefix: str) -> str | None:
    for token in sorted(tokens):
        if token.startswith(prefix):
            value = token[len(prefix):].strip()
            if value:
                return value
    return None


def _metadata_tokens(record: dict[str, Any]) -> set[str]:
    layers = record.get("layers", {})
    metadata = layers.get("metadata", set()) if isinstance(layers, dict) else set()
    if isinstance(metadata, set):
        return {str(token) for token in metadata}
    if isinstance(metadata, (list, tuple)):
        return {str(token) for token in metadata}
    return set()


def infer_package_name(record: dict[str, Any]) -> str | None:
    """Infer package name from metadata, falling back to F-Droid APK stem."""
    package_name = _extract_token_value(_metadata_tokens(record), "package_name:")
    if package_name:
        return package_name

    app_id = str(record.get("app_id") or "").strip()
    match = PACKAGE_VERSION_SUFFIX_RE.match(app_id)
    if match:
        return match.group(1)
    return app_id or None


def infer_signature_key(record: dict[str, Any]) -> str | None:
    """Infer a stable signing key from metadata tokens or explicit fields."""
    raw = record.get("signature") or record.get("signing_hash") or record.get("signing")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("hash", "sha256", "fingerprint", "prefix"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    apk_path = record.get("apk_path")
    if apk_path:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            chain = extract_signing_chain(Path(str(apk_path)))
        cert_hashes = sorted(
            cert.get("sha256", "").strip()
            for cert in chain
            if isinstance(cert, dict) and cert.get("sha256")
        )
        if cert_hashes:
            return "cert_chain:{}".format("|".join(cert_hashes))

    metadata = _metadata_tokens(record)
    signing_prefix = _extract_token_value(metadata, "signing_prefix:")
    if signing_prefix:
        return signing_prefix
    return None


def build_ground_truth_pairs(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build clone/non_clone labels using package_name + signature identity."""
    identities = {
        str(record["app_id"]): {
            "package_name": infer_package_name(record),
            "signature_key": infer_signature_key(record),
        }
        for record in records
    }

    pairs: list[dict[str, Any]] = []
    for app_a, app_b in combinations(records, 2):
        app_a_id = str(app_a["app_id"])
        app_b_id = str(app_b["app_id"])
        left = identities[app_a_id]
        right = identities[app_b_id]
        same_package = (
            left["package_name"] is not None
            and left["package_name"] == right["package_name"]
        )
        same_signature = (
            left["signature_key"] is not None
            and left["signature_key"] == right["signature_key"]
        )
        is_clone = bool(same_package and same_signature)
        pairs.append(
            {
                "apk_a": app_a_id,
                "apk_b": app_b_id,
                "label": "clone" if is_clone else "non_clone",
                "package_a": left["package_name"],
                "package_b": right["package_name"],
                "signature_a": left["signature_key"],
                "signature_b": right["signature_key"],
                "ground_truth_reason": (
                    "same_package_and_signature" if is_clone else "different_package_or_signature"
                ),
            }
        )
    return pairs


def _jaccard_score(
    app_a: dict[str, Any],
    app_b: dict[str, Any],
    selected_layers: Sequence[str],
) -> float:
    features_a = aggregate_features(app_a, list(selected_layers))
    features_b = aggregate_features(app_b, list(selected_layers))
    union_size = len(features_a | features_b)
    if union_size == 0:
        return 0.0
    return len(features_a & features_b) / union_size


def score_pairs(
    records: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    *,
    selected_layers: Sequence[str] = DEFAULT_SELECTED_LAYERS,
    metric: str = DEFAULT_METRIC,
) -> list[dict[str, Any]]:
    records_by_id = {str(record["app_id"]): record for record in records}
    scored: list[dict[str, Any]] = []
    for pair in pairs:
        app_a = records_by_id[str(pair["apk_a"])]
        app_b = records_by_id[str(pair["apk_b"])]
        if metric == "jaccard":
            score = _jaccard_score(app_a, app_b, selected_layers)
        else:
            score = float(
                calculate_pair_score(
                    app_a=app_a,
                    app_b=app_b,
                    metric=metric,
                    selected_layers=list(selected_layers),
                    ins_block_sim_threshold=0.80,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )
            )
        scored.append({**pair, "score": float(score)})
    return scored


def _confusion_at_threshold(scored_pairs: Sequence[dict[str, Any]], threshold: float) -> dict[str, int]:
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
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def metrics_at_threshold(scored_pairs: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    counts = _confusion_at_threshold(scored_pairs, threshold)
    tp = counts["tp"]
    fp = counts["fp"]
    fn = counts["fn"]
    tn = counts["tn"]
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    fpr = _safe_div(fp, fp + tn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return {
        "threshold": _round_metric(threshold),
        **counts,
        "precision": _round_metric(precision),
        "recall": _round_metric(recall),
        "tpr": _round_metric(recall),
        "fpr": _round_metric(fpr),
        "f1": _round_metric(f1),
        "youden_j": _round_metric(recall - fpr),
    }


def build_roc_curve(
    scored_pairs: Sequence[dict[str, Any]],
    *,
    thresholds: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    return [
        metrics_at_threshold(scored_pairs, threshold)
        for threshold in (list(thresholds) if thresholds is not None else threshold_grid())
    ]


def select_operating_point(
    roc_points: Sequence[dict[str, Any]],
    *,
    strategy: str = "f1",
) -> dict[str, Any]:
    if not roc_points:
        raise ValueError("roc_points must not be empty")
    if strategy == "f1":
        return dict(
            max(
                roc_points,
                key=lambda point: (
                    point["f1"],
                    point["youden_j"],
                    -point["fpr"],
                    point["threshold"],
                ),
            )
        )
    if strategy == "youden":
        return dict(
            max(
                roc_points,
                key=lambda point: (
                    point["youden_j"],
                    point["f1"],
                    -point["fpr"],
                    point["threshold"],
                ),
            )
        )
    raise ValueError("Unsupported selection strategy: {!r}".format(strategy))


def _split_metrics(records: Sequence[dict[str, Any]], scored_pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    clone_count = sum(1 for pair in scored_pairs if pair["label"] == "clone")
    return {
        "documents": len(records),
        "pairs_total": len(scored_pairs),
        "pairs_clone": clone_count,
        "pairs_non_clone": len(scored_pairs) - clone_count,
    }


def calibrate_on_splits(
    *,
    train_records: Sequence[dict[str, Any]],
    test_records: Sequence[dict[str, Any]],
    thresholds: Sequence[float] | None = None,
    selected_layers: Sequence[str] = DEFAULT_SELECTED_LAYERS,
    metric: str = DEFAULT_METRIC,
    selection_strategy: str = "f1",
    current_threshold: float = CURRENT_THRESH_002,
) -> dict[str, Any]:
    thresholds = list(thresholds) if thresholds is not None else threshold_grid()
    train_pairs = build_ground_truth_pairs(train_records)
    test_pairs = build_ground_truth_pairs(test_records)
    train_scored = score_pairs(
        train_records,
        train_pairs,
        selected_layers=selected_layers,
        metric=metric,
    )
    test_scored = score_pairs(
        test_records,
        test_pairs,
        selected_layers=selected_layers,
        metric=metric,
    )
    train_roc = build_roc_curve(train_scored, thresholds=thresholds)
    optimal = select_operating_point(train_roc, strategy=selection_strategy)
    optimal_threshold = float(optimal["threshold"])
    train_metrics = metrics_at_threshold(train_scored, optimal_threshold)
    test_metrics = metrics_at_threshold(test_scored, optimal_threshold)
    train_current = metrics_at_threshold(train_scored, current_threshold)
    test_current = metrics_at_threshold(test_scored, current_threshold)

    return {
        "config": {
            "selected_layers": list(selected_layers),
            "metric": metric,
            "threshold_grid": list(thresholds),
            "selection_strategy": selection_strategy,
            "current_threshold": float(current_threshold),
            "overfit_margin": OVERFIT_MARGIN,
        },
        "optimal": optimal,
        "train": {
            **_split_metrics(train_records, train_scored),
            "roc_curve": train_roc,
            "metrics": train_metrics,
            "current_threshold_metrics": train_current,
        },
        "test": {
            **_split_metrics(test_records, test_scored),
            "metrics": test_metrics,
            "current_threshold_metrics": test_current,
        },
        "validation": {
            "test_f1_not_better_than_train_with_margin": (
                float(test_metrics["f1"]) <= float(train_metrics["f1"]) + OVERFIT_MARGIN
            ),
            "test_f1_minus_train_f1": _round_metric(
                float(test_metrics["f1"]) - float(train_metrics["f1"])
            ),
        },
    }


def discover_apks(corpus_dir: str | Path) -> list[Path]:
    root = Path(corpus_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("APK corpus directory not found: {}".format(root))
    return sorted(path for path in root.rglob("*.apk") if path.is_file())


def load_app_records(apk_paths: Sequence[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for apk_path in apk_paths:
        layers = extract_layers_from_apk(Path(apk_path))
        records.append(
            {
                "app_id": Path(apk_path).stem,
                "apk_path": str(Path(apk_path).resolve()),
                "layers": layers,
            }
        )
    return records


def build_report(
    *,
    corpus_dir: str | Path,
    seed: int = DEFAULT_SEED,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
    selected_layers: Sequence[str] = DEFAULT_SELECTED_LAYERS,
    metric: str = DEFAULT_METRIC,
    selection_strategy: str = "f1",
    current_threshold: float = CURRENT_THRESH_002,
) -> dict[str, Any]:
    corpus_path = Path(corpus_dir).expanduser().resolve()
    apk_paths = discover_apks(corpus_path)
    train_paths, test_paths = split_train_test(apk_paths, train_ratio=train_ratio, seed=seed)
    train_records = load_app_records(train_paths)
    test_records = load_app_records(test_paths)
    calibration = calibrate_on_splits(
        train_records=train_records,
        test_records=test_records,
        thresholds=threshold_grid(),
        selected_layers=selected_layers,
        metric=metric,
        selection_strategy=selection_strategy,
        current_threshold=current_threshold,
    )
    optimal_threshold = float(calibration["optimal"]["threshold"])
    changed = abs(optimal_threshold - float(current_threshold)) > 1e-9
    warnings: list[str] = []
    if len(apk_paths) != 350:
        warnings.append("Expected 350 F-Droid v2 APKs, found {}.".format(len(apk_paths)))
    if calibration["train"]["pairs_clone"] == 0:
        warnings.append("Train split has no clone pairs under package/signature heuristic.")
    if calibration["test"]["pairs_clone"] == 0:
        warnings.append("Test split has no clone pairs under package/signature heuristic.")
    if not calibration["validation"]["test_f1_not_better_than_train_with_margin"]:
        warnings.append("Test F1 is higher than train F1 beyond the configured margin.")

    return {
        "schema_version": "screening-thresh-calibration-train-test-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_id": ARTIFACT_ID,
        "status": "ok" if not warnings else "ok_with_warnings",
        "corpus": {
            "name": "F-Droid v2",
            "path": str(corpus_path),
            "apk_count": len(apk_paths),
            "train_apk_count": len(train_paths),
            "test_apk_count": len(test_paths),
            "train_ratio": float(train_ratio),
            "seed": int(seed),
            "train_apks": [path.name for path in train_paths],
            "test_apks": [path.name for path in test_paths],
        },
        "ground_truth_policy": {
            "label_clone_when": "same package_name and same signing key",
            "package_source": "metadata package_name token, fallback to F-Droid stem before final _version",
            "signature_source": "APK signing certificate SHA-256 chain, fallback to metadata signing_prefix token",
        },
        "decision": {
            "current_threshold": float(current_threshold),
            "optimal_threshold": optimal_threshold,
            "production_threshold_changed": changed,
            "recommendation": (
                "shift_threshold_to_train_optimum"
                if changed
                else "keep_current_threshold"
            ),
            "train_f1_delta_vs_current": _round_metric(
                float(calibration["train"]["metrics"]["f1"])
                - float(calibration["train"]["current_threshold_metrics"]["f1"])
            ),
            "test_f1_delta_vs_current": _round_metric(
                float(calibration["test"]["metrics"]["f1"])
                - float(calibration["test"]["current_threshold_metrics"]["f1"])
            ),
        },
        "warnings": warnings,
        **calibration,
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
        description="Calibrate THRESH-002 on F-Droid v2 with a fixed train/test split."
    )
    parser.add_argument(
        "--corpus-dir",
        "--corpus_dir",
        default=str(fdroid_v2_apks_dir()),
        help="F-Droid v2 APK corpus directory.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output report.json path.",
    )
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
    parser.add_argument(
        "--selection-strategy",
        "--selection_strategy",
        choices=("f1", "youden"),
        default="f1",
    )
    parser.add_argument("--current-threshold", "--current_threshold", type=float, default=0.28)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(
        corpus_dir=args.corpus_dir,
        seed=args.seed,
        train_ratio=args.train_ratio,
        selected_layers=args.selected_layers,
        metric=args.metric,
        selection_strategy=args.selection_strategy,
        current_threshold=args.current_threshold,
    )
    report_path = write_report(args.out, report)
    print(report_path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Automatic post-hoc metrics for hint explanations.

The module implements three label-free metrics:
- faithfulness: correlation between hint importance and absolute score drop
  after masking hinted features one by one.
- sufficiency: score retained by the hint-only feature subset.
- comprehensiveness: score drop after removing all hinted features.

The metric framing follows post-hoc faithfulness discussion from Arrieta et al.
(Information Fusion, 2020) and sufficiency/comprehensiveness from DeYoung et al.
(ACL 2020, ERASER). For sufficiency we use the retained-score variant, so a
value close to 1.0 means that hint-only features preserve the original score
when `score_fn` is normalized against the full pair.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import statistics
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Union


FeatureInput = Optional[Union[Mapping[str, float], Iterable[str]]]


@dataclass(frozen=True)
class HintEvalResult:
    hint_id: str
    faithfulness: float
    sufficiency: float
    comprehensiveness: float


def _as_feature_dict(features: FeatureInput) -> dict[str, float]:
    if features is None:
        return {}
    if isinstance(features, Mapping):
        normalized: dict[str, float] = {}
        for name, value in features.items():
            key = str(name).strip()
            if not key:
                continue
            try:
                normalized[key] = float(value)
            except (TypeError, ValueError):
                continue
        return normalized
    if isinstance(features, str):
        stripped = features.strip()
        return {stripped: 1.0} if stripped else {}

    normalized = {}
    for item in features:
        key = str(item).strip()
        if key:
            normalized[key] = 1.0
    return normalized


def _mask_features(pair_features: FeatureInput, feature_names: Iterable[str]) -> dict[str, float]:
    masked = _as_feature_dict(pair_features)
    for name in feature_names:
        masked.pop(name, None)
    return masked


def _score(score_fn: Callable[[dict[str, float]], float], features: FeatureInput) -> float:
    score = score_fn(_as_feature_dict(features))
    try:
        return float(score)
    except (TypeError, ValueError) as exc:
        raise ValueError("score_fn must return a numeric value") from exc


def _rankdata(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + end + 2) / 2.0
        for offset in range(cursor, end + 1):
            original_index = indexed[offset][0]
            ranks[original_index] = average_rank
        cursor = end + 1
    return ranks


def _pearson(x_values: list[float], y_values: list[float]) -> float:
    x_mean = statistics.mean(x_values)
    y_mean = statistics.mean(y_values)
    numerator = sum((x_value - x_mean) * (y_value - y_mean) for x_value, y_value in zip(x_values, y_values))
    x_norm = math.sqrt(sum((x_value - x_mean) ** 2 for x_value in x_values))
    y_norm = math.sqrt(sum((y_value - y_mean) ** 2 for y_value in y_values))
    if x_norm == 0.0 and y_norm == 0.0:
        return 1.0
    if x_norm == 0.0 or y_norm == 0.0:
        return 0.0
    return numerator / (x_norm * y_norm)


def _spearman(x_values: list[float], y_values: list[float]) -> float:
    if len(x_values) != len(y_values):
        raise ValueError("Spearman correlation requires vectors of equal length")
    if not x_values:
        return 0.0
    if len(x_values) == 1:
        return 1.0 if x_values[0] != 0.0 and y_values[0] != 0.0 else 0.0
    return _pearson(_rankdata(x_values), _rankdata(y_values))


def faithfulness(
    score_fn: Callable[[dict[str, float]], float],
    pair_features: FeatureInput,
    hint_features: FeatureInput,
) -> float:
    """Return a faithfulness score in [-1, 1].

    `hint_features` may be a mapping `feature -> importance` or any iterable of
    feature names. For each hinted feature, the metric masks the feature from
    `pair_features`, recomputes the score, and correlates `|delta score|` with
    the hint importance using Spearman correlation.
    """

    full_features = _as_feature_dict(pair_features)
    hinted = _as_feature_dict(hint_features)
    if not hinted:
        return 0.0

    full_score = _score(score_fn, full_features)
    importances: list[float] = []
    deltas: list[float] = []
    for feature_name, importance in hinted.items():
        if feature_name not in full_features:
            continue
        masked = dict(full_features)
        masked.pop(feature_name, None)
        importances.append(abs(float(importance)))
        deltas.append(abs(full_score - _score(score_fn, masked)))

    if not importances:
        return 0.0
    return _spearman(importances, deltas)


def sufficiency(
    score_fn: Callable[[dict[str, float]], float],
    hint_only_features: FeatureInput,
) -> float:
    """Return the score retained by the hint-only feature subset."""

    return _score(score_fn, hint_only_features)


def comprehensiveness(
    score_fn: Callable[[dict[str, float]], float],
    pair_features: FeatureInput,
    hint_features: FeatureInput,
) -> float:
    """Return the score drop after removing all hinted features."""

    full_features = _as_feature_dict(pair_features)
    hinted = _as_feature_dict(hint_features)
    if not hinted:
        return 0.0

    full_score = _score(score_fn, full_features)
    without_hint = _mask_features(full_features, hinted.keys())
    return full_score - _score(score_fn, without_hint)


def build_normalized_linear_score(reference_features: FeatureInput) -> Callable[[dict[str, float]], float]:
    reference = _as_feature_dict(reference_features)
    denominator = sum(abs(value) for value in reference.values())
    if denominator == 0.0:
        denominator = 1.0

    def score_fn(features: dict[str, float]) -> float:
        return sum(abs(value) for value in _as_feature_dict(features).values()) / denominator

    return score_fn


def evaluate_hint(
    pair_features: FeatureInput,
    hint_features: FeatureInput,
    *,
    hint_id: str,
    hint_only_features: Optional[FeatureInput] = None,
    score_fn: Optional[Callable[[dict[str, float]], float]] = None,
) -> HintEvalResult:
    score = score_fn or build_normalized_linear_score(pair_features)
    hint_subset = hint_only_features
    if hint_subset is None:
        full = _as_feature_dict(pair_features)
        hint_keys = _as_feature_dict(hint_features).keys()
        hint_subset = {name: full[name] for name in hint_keys if name in full}

    return HintEvalResult(
        hint_id=hint_id,
        faithfulness=faithfulness(score, pair_features, hint_features),
        sufficiency=sufficiency(score, hint_subset),
        comprehensiveness=comprehensiveness(score, pair_features, hint_features),
    )


def aggregate_results(results: list[HintEvalResult]) -> dict[str, dict[str, float] | int]:
    if not results:
        return {
            "count": 0,
            "faithfulness": {"mean": 0.0, "median": 0.0, "stddev": 0.0},
            "sufficiency": {"mean": 0.0, "median": 0.0, "stddev": 0.0},
            "comprehensiveness": {"mean": 0.0, "median": 0.0, "stddev": 0.0},
        }

    def summarize(metric_name: str) -> dict[str, float]:
        values = [float(getattr(result, metric_name)) for result in results]
        return {
            "mean": round(statistics.mean(values), 6),
            "median": round(statistics.median(values), 6),
            "stddev": round(statistics.pstdev(values), 6),
        }

    return {
        "count": len(results),
        "faithfulness": summarize("faithfulness"),
        "sufficiency": summarize("sufficiency"),
        "comprehensiveness": summarize("comprehensiveness"),
    }


def _parse_feature_payload(raw_value: str | None) -> dict[str, float]:
    if raw_value is None:
        return {}
    stripped = raw_value.strip()
    if not stripped:
        return {}

    parsed = None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(stripped)
            break
        except (ValueError, SyntaxError, json.JSONDecodeError):
            parsed = None
    if parsed is None:
        if "|" in stripped:
            return _as_feature_dict(part.strip() for part in stripped.split("|") if part.strip())
        if "," in stripped:
            return _as_feature_dict(part.strip() for part in stripped.split(",") if part.strip())
        return _as_feature_dict([stripped])
    return _as_feature_dict(parsed)


def _first_present(row: Mapping[str, str], column_names: Iterable[str]) -> str | None:
    for column_name in column_names:
        value = row.get(column_name)
        if value not in (None, ""):
            return value
    return None


def load_csv_rows(csv_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            pair_features = _parse_feature_payload(
                _first_present(row, ("pair_features", "pair_features_json", "full_features", "features"))
            )
            hint_features = _parse_feature_payload(
                _first_present(row, ("hint_features", "hint_features_json", "rationale_features", "hint"))
            )
            hint_only_features = _parse_feature_payload(
                _first_present(row, ("hint_only_features", "hint_only_features_json"))
            )
            hint_id = _first_present(row, ("hint_id", "id", "row_id")) or f"ROW-{index:03d}"
            if not pair_features or not hint_features:
                continue
            rows.append(
                {
                    "hint_id": hint_id,
                    "pair_features": pair_features,
                    "hint_features": hint_features,
                    "hint_only_features": hint_only_features or None,
                }
            )
    return rows


def build_synthetic_rows() -> list[dict[str, object]]:
    pair_features = {
        "code_overlap": 0.6,
        "resource_overlap": 0.3,
        "permission_overlap": 0.1,
    }
    return [
        {
            "hint_id": "SYN-HINT-001",
            "pair_features": pair_features,
            "hint_features": {
                "code_overlap": 0.9,
                "resource_overlap": 0.5,
                "permission_overlap": 0.1,
            },
        },
        {
            "hint_id": "SYN-HINT-002",
            "pair_features": pair_features,
            "hint_features": {
                "code_overlap": 0.6,
                "permission_overlap": 0.1,
            },
        },
        {
            "hint_id": "SYN-HINT-003",
            "pair_features": pair_features,
            "hint_features": {
                "resource_overlap": 0.7,
                "permission_overlap": 0.2,
            },
        },
        {
            "hint_id": "SYN-HINT-004",
            "pair_features": pair_features,
            "hint_features": {
                "code_overlap": 0.1,
                "resource_overlap": 0.5,
                "permission_overlap": 0.9,
            },
        },
    ]


def _canonical_pair_feature_name(signal_type: str, ref: str) -> str:
    return f"{signal_type}:{ref}"


def _to_float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_pairwise_explainer():
    try:
        from script.pairwise_explainer import build_output_rows
    except Exception:
        from pairwise_explainer import build_output_rows  # type: ignore
    return build_output_rows


def load_pairwise_rows(json_path: Path) -> list[dict[str, object]]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("pairs", "pairwise", "results", "candidates", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _feature_map_from_evidence(evidence: object) -> dict[str, float]:
    if not isinstance(evidence, list):
        return {}

    features: dict[str, float] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        signal_type = item.get("signal_type")
        ref = item.get("ref")
        magnitude = _to_float_or_none(item.get("magnitude"))
        if not isinstance(signal_type, str) or not signal_type.strip():
            continue
        if not isinstance(ref, str) or not ref.strip():
            continue
        if magnitude is None:
            continue
        features[_canonical_pair_feature_name(signal_type.strip(), ref.strip())] = magnitude
    return features


def _pair_features_from_row(pair_row: Mapping[str, object]) -> dict[str, float]:
    features = _feature_map_from_evidence(pair_row.get("evidence"))

    full_score = _to_float_or_none(pair_row.get("full_similarity_score"))
    if full_score is not None:
        features["pair:full_similarity_score"] = full_score

    library_reduced = _to_float_or_none(pair_row.get("library_reduced_score"))
    if library_reduced is not None and (full_score is None or not math.isclose(library_reduced, full_score)):
        features["pair:library_reduced_score"] = library_reduced

    if not features:
        per_view = pair_row.get("per_view_scores")
        if isinstance(per_view, dict):
            for layer_name, score in per_view.items():
                if not isinstance(layer_name, str) or not layer_name.strip():
                    continue
                parsed = _to_float_or_none(score)
                if parsed is None:
                    continue
                features[_canonical_pair_feature_name("layer_score", layer_name.strip())] = parsed

        signature_match = pair_row.get("signature_match")
        if isinstance(signature_match, dict):
            signature_score = _to_float_or_none(signature_match.get("score"))
            if signature_score is not None:
                features[_canonical_pair_feature_name("signature_match", "apk_signature")] = signature_score

    return features


def _hint_features_from_explanations(explanation_hints: object, fallback_evidence: object) -> dict[str, float]:
    features: dict[str, float] = {}
    if isinstance(explanation_hints, list):
        for hint in explanation_hints:
            if not isinstance(hint, dict):
                continue
            signal_type = hint.get("signal") or hint.get("type")
            ref = hint.get("entity") or hint.get("ref")
            score = _to_float_or_none(hint.get("score"))
            if not isinstance(signal_type, str) or not signal_type.strip():
                continue
            if not isinstance(ref, str) or not ref.strip():
                continue
            if score is None:
                continue
            features[_canonical_pair_feature_name(signal_type.strip(), ref.strip())] = score
    if features:
        return features
    return _feature_map_from_evidence(fallback_evidence)


def _pair_hint_id(pair_row: Mapping[str, object]) -> str:
    pair_id = pair_row.get("pair_id")
    if isinstance(pair_id, str) and pair_id.strip():
        return pair_id.strip()
    app_a = str(pair_row.get("app_a") or "unknown_app_a").strip() or "unknown_app_a"
    app_b = str(pair_row.get("app_b") or "unknown_app_b").strip() or "unknown_app_b"
    return f"{app_a}__{app_b}"


def build_real_data_rows_from_pairwise(pair_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not pair_rows:
        return []

    build_output_rows = _load_pairwise_explainer()
    explained_rows = build_output_rows(pair_rows)

    rows: list[dict[str, object]] = []
    for pair_row, explained_row in zip(pair_rows, explained_rows):
        pair_features = _pair_features_from_row(pair_row)
        hint_features = _hint_features_from_explanations(
            explained_row.get("explanation_hints"),
            pair_row.get("evidence"),
        )
        if not pair_features or not hint_features:
            continue
        hint_only_features = {
            name: pair_features[name]
            for name in hint_features
            if name in pair_features
        }
        if not hint_only_features:
            continue
        rows.append(
            {
                "hint_id": _pair_hint_id(pair_row),
                "pair_features": pair_features,
                "hint_features": hint_features,
                "hint_only_features": hint_only_features,
            }
        )
    return rows


def _build_run_section(rows: list[dict[str, object]], source: dict[str, object]) -> dict[str, object]:
    results = [
        evaluate_hint(
            row["pair_features"],
            row["hint_features"],
            hint_id=str(row["hint_id"]),
            hint_only_features=row.get("hint_only_features"),
        )
        for row in rows
    ]

    aggregate = aggregate_results(results)
    return {
        "source": source,
        "n_hints": len(results),
        "results": [
            {
                "hint_id": result.hint_id,
                "faithfulness": round(result.faithfulness, 6),
                "sufficiency": round(result.sufficiency, 6),
                "comprehensiveness": round(result.comprehensiveness, 6),
            }
            for result in results
        ],
        "aggregate": aggregate,
        "faithfulness_mean": aggregate["faithfulness"]["mean"],
        "faithfulness_median": aggregate["faithfulness"]["median"],
        "faithfulness_stddev": aggregate["faithfulness"]["stddev"],
        "sufficiency_mean": aggregate["sufficiency"]["mean"],
        "sufficiency_median": aggregate["sufficiency"]["median"],
        "sufficiency_stddev": aggregate["sufficiency"]["stddev"],
        "comprehensiveness_mean": aggregate["comprehensiveness"]["mean"],
        "comprehensiveness_median": aggregate["comprehensiveness"]["median"],
        "comprehensiveness_stddev": aggregate["comprehensiveness"]["stddev"],
    }


def generate_report(
    input_csv: Optional[Path],
    output_json: Path,
    *,
    pairwise_json: Optional[Path] = None,
) -> dict[str, object]:
    synthetic_rows = build_synthetic_rows()
    synthetic_run = _build_run_section(
        synthetic_rows,
        {
            "type": "synthetic",
            "input_csv": str(input_csv) if input_csv is not None else None,
            "rows_evaluated": len(synthetic_rows),
        },
    )

    real_data_run: dict[str, object] | None = None
    if input_csv is not None and input_csv.exists():
        real_rows = load_csv_rows(input_csv)
        real_data_run = _build_run_section(
            real_rows,
            {
                "type": "csv",
                "input_csv": str(input_csv),
                "rows_loaded": len(real_rows),
            },
        )
    elif pairwise_json is not None and pairwise_json.exists():
        pair_rows = load_pairwise_rows(pairwise_json)
        real_rows = build_real_data_rows_from_pairwise(pair_rows)
        real_data_run = _build_run_section(
            real_rows,
            {
                "type": "pairwise_json",
                "pairwise_json": str(pairwise_json),
                "pairs_loaded": len(pair_rows),
            },
        )

    report = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "synthetic_run": synthetic_run,
        "real_data_run": real_data_run,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    root_dir = Path(__file__).resolve().parent.parent
    default_input = root_dir / "experiments" / "artifacts" / "E-HINT-004" / "deep-184-annotated.csv"
    default_output = root_dir / "experiments" / "artifacts" / "E-HINT-FAITHFULNESS" / "report.json"

    parser = argparse.ArgumentParser(description="Generate automatic hint faithfulness metrics report.")
    parser.add_argument("--input-csv", default=str(default_input), help="Input CSV with exported hint features.")
    parser.add_argument(
        "--pairwise-json",
        default=None,
        help="Optional pairwise JSON to use when --input-csv is absent.",
    )
    parser.add_argument("--output-json", default=str(default_output), help="Path to JSON report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input_csv)
    pairwise_json = Path(args.pairwise_json) if args.pairwise_json else None
    output_json = Path(args.output_json)
    report = generate_report(input_csv, output_json, pairwise_json=pairwise_json)
    print(
        json.dumps(
            {
                "synthetic_hints": report["synthetic_run"]["n_hints"],
                "real_source_type": (
                    report["real_data_run"]["source"]["type"]
                    if report.get("real_data_run") is not None
                    else None
                ),
                "real_hints": (
                    report["real_data_run"]["n_hints"]
                    if report.get("real_data_run") is not None
                    else 0
                ),
                "output_json": str(output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

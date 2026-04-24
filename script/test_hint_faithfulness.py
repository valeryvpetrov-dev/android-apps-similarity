"""Tests for automatic hint faithfulness metrics."""

from __future__ import annotations

import math
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from hint_faithfulness import (  # noqa: E402
    HintEvalResult,
    build_real_data_rows_from_pairwise,
    comprehensiveness,
    faithfulness,
    generate_report,
    sufficiency,
)


PAIR_FEATURES = {
    "code_overlap": 0.6,
    "resource_overlap": 0.3,
    "permission_overlap": 0.1,
}


def linear_score(features: dict[str, float]) -> float:
    return round(sum(float(value) for value in features.values()), 6)


def test_faithfulness_is_positive_one_for_aligned_importance():
    aligned_hint = {
        "code_overlap": 0.9,
        "resource_overlap": 0.5,
        "permission_overlap": 0.1,
    }

    assert faithfulness(linear_score, PAIR_FEATURES, aligned_hint) == pytest.approx(1.0)


def test_faithfulness_is_negative_one_for_reversed_importance():
    reversed_hint = {
        "code_overlap": 0.1,
        "resource_overlap": 0.5,
        "permission_overlap": 0.9,
    }

    assert faithfulness(linear_score, PAIR_FEATURES, reversed_hint) == pytest.approx(-1.0)


def test_sufficiency_matches_hint_only_score():
    hint_only = {
        "code_overlap": 0.6,
        "permission_overlap": 0.1,
    }

    assert sufficiency(linear_score, hint_only) == pytest.approx(0.7)


def test_comprehensiveness_matches_removed_hint_mass():
    hint = {
        "code_overlap": 1.0,
        "permission_overlap": 1.0,
    }

    assert comprehensiveness(linear_score, PAIR_FEATURES, hint) == pytest.approx(0.7)


def test_comprehensiveness_is_zero_for_empty_hint():
    assert comprehensiveness(linear_score, PAIR_FEATURES, {}) == pytest.approx(0.0)


def test_sufficiency_is_one_for_full_hint():
    assert sufficiency(linear_score, PAIR_FEATURES) == pytest.approx(1.0)


def test_faithfulness_for_single_feature_uses_direct_score_drop():
    assert faithfulness(
        linear_score,
        PAIR_FEATURES,
        {"code_overlap": 0.4},
    ) == pytest.approx(1.0)


def test_faithfulness_is_zero_for_empty_hint():
    assert faithfulness(linear_score, PAIR_FEATURES, {}) == pytest.approx(0.0)


def test_dataclass_keeps_metric_values():
    result = HintEvalResult(
        hint_id="HINT-001",
        faithfulness=0.75,
        sufficiency=0.6,
        comprehensiveness=0.4,
    )

    assert result.hint_id == "HINT-001"
    assert math.isclose(result.faithfulness, 0.75)
    assert math.isclose(result.sufficiency, 0.6)
    assert math.isclose(result.comprehensiveness, 0.4)


def test_build_real_data_rows_from_pairwise_uses_pair_scores_and_evidence():
    pair_rows = [
        {
            "app_a": "alpha",
            "app_b": "beta",
            "pair_id": "PAIR-001",
            "full_similarity_score": 0.75,
            "library_reduced_score": 0.75,
            "evidence": [
                {
                    "source_stage": "pairwise",
                    "signal_type": "layer_score",
                    "magnitude": 0.6,
                    "ref": "code",
                },
                {
                    "source_stage": "pairwise",
                    "signal_type": "layer_score",
                    "magnitude": 0.4,
                    "ref": "metadata",
                },
                {
                    "source_stage": "signing",
                    "signal_type": "signature_match",
                    "magnitude": 1.0,
                    "ref": "apk_signature",
                },
            ],
        }
    ]

    rows = build_real_data_rows_from_pairwise(pair_rows)

    assert rows == [
        {
            "hint_id": "PAIR-001",
            "pair_features": {
                "pair:full_similarity_score": 0.75,
                "layer_score:code": 0.6,
                "layer_score:metadata": 0.4,
                "signature_match:apk_signature": 1.0,
            },
            "hint_features": {
                "layer_score:code": 0.6,
                "layer_score:metadata": 0.4,
                "signature_match:apk_signature": 1.0,
            },
            "hint_only_features": {
                "layer_score:code": 0.6,
                "layer_score:metadata": 0.4,
                "signature_match:apk_signature": 1.0,
            },
        }
    ]


def test_generate_report_keeps_synthetic_run_and_adds_real_data_run_from_pairwise_json():
    pair_rows = [
        {
            "app_a": "alpha",
            "app_b": "beta",
            "pair_id": "PAIR-001",
            "full_similarity_score": 0.75,
            "library_reduced_score": 0.75,
            "evidence": [
                {
                    "source_stage": "pairwise",
                    "signal_type": "layer_score",
                    "magnitude": 0.6,
                    "ref": "code",
                },
                {
                    "source_stage": "pairwise",
                    "signal_type": "layer_score",
                    "magnitude": 0.4,
                    "ref": "metadata",
                },
            ],
        }
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pairwise_json = root / "pairwise.json"
        output_json = root / "report.json"
        pairwise_json.write_text(json.dumps(pair_rows), encoding="utf-8")

        report = generate_report(
            input_csv=root / "missing.csv",
            output_json=output_json,
            pairwise_json=pairwise_json,
        )

        written = json.loads(output_json.read_text(encoding="utf-8"))

    assert "synthetic_run" in report
    assert "real_data_run" in report
    assert written["synthetic_run"]["source"]["type"] == "synthetic"
    assert written["real_data_run"]["source"]["type"] == "pairwise_json"
    assert written["real_data_run"]["n_hints"] == 1
    assert written["real_data_run"]["results"][0]["hint_id"] == "PAIR-001"
    assert written["real_data_run"]["faithfulness_mean"] == pytest.approx(1.0)

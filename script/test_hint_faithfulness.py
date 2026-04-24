"""Tests for automatic hint faithfulness metrics."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from hint_faithfulness import (  # noqa: E402
    HintEvalResult,
    comprehensiveness,
    faithfulness,
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

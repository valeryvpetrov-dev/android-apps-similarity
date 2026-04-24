#!/usr/bin/env python3
"""Tests for Tversky and overlap similarity helpers."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from library_view_v2 import compare_libraries_v2


def _load_tversky_module():
    spec = importlib.util.find_spec("tversky_overlap")
    assert spec is not None, "script/tversky_overlap.py must exist"
    return importlib.import_module("tversky_overlap")


def test_tversky_and_overlap_both_empty_are_one() -> None:
    mod = _load_tversky_module()

    assert mod.tversky_index(set(), set()) == pytest.approx(1.0)
    assert mod.szymkiewicz_simpson_overlap(set(), set()) == pytest.approx(1.0)


def test_tversky_and_overlap_one_empty_are_zero() -> None:
    mod = _load_tversky_module()

    assert mod.tversky_index(set(), {"a"}) == pytest.approx(0.0)
    assert mod.szymkiewicz_simpson_overlap(set(), {"a"}) == pytest.approx(0.0)


def test_subset_case_keeps_overlap_one_but_jaccard_below_one() -> None:
    mod = _load_tversky_module()
    left = {"a", "b"}
    right = {"a", "b", "c", "d"}

    jaccard = len(left & right) / len(left | right)

    assert jaccard == pytest.approx(0.5)
    assert mod.szymkiewicz_simpson_overlap(left, right) == pytest.approx(1.0)


def test_identical_sets_return_one_for_all_scores() -> None:
    mod = _load_tversky_module()
    left = {"a", "b", "c"}
    right = {"a", "b", "c"}

    assert mod.tversky_index(left, right) == pytest.approx(1.0)
    assert mod.tversky_index(left, right, alpha=0.9, beta=0.1) == pytest.approx(1.0)
    assert mod.szymkiewicz_simpson_overlap(left, right) == pytest.approx(1.0)


def test_disjoint_sets_return_zero() -> None:
    mod = _load_tversky_module()

    assert mod.tversky_index({"a"}, {"b"}) == pytest.approx(0.0)
    assert mod.szymkiewicz_simpson_overlap({"a"}, {"b"}) == pytest.approx(0.0)


def test_tversky_with_unit_weights_matches_jaccard() -> None:
    mod = _load_tversky_module()
    left = {"a", "b", "c"}
    right = {"b", "c", "d", "e"}

    expected_jaccard = 2.0 / 5.0

    assert mod.tversky_index(left, right, alpha=1.0, beta=1.0) == pytest.approx(expected_jaccard)


def test_tversky_alpha_one_beta_zero_matches_intersection_over_a() -> None:
    mod = _load_tversky_module()
    left = {"a", "b", "c"}
    right = {"b", "c", "d", "e"}

    assert mod.tversky_index(left, right, alpha=1.0, beta=0.0) == pytest.approx(2.0 / 3.0)


def test_tversky_alpha_zero_beta_one_matches_intersection_over_b() -> None:
    mod = _load_tversky_module()
    left = {"a", "b", "c"}
    right = {"b", "c", "d", "e"}

    assert mod.tversky_index(left, right, alpha=0.0, beta=1.0) == pytest.approx(2.0 / 4.0)


def test_single_element_tuple_inputs_are_supported() -> None:
    mod = _load_tversky_module()
    left = {(1, 2)}
    right = {(1, 2), (3, 4)}

    assert mod.tversky_index(left, right, alpha=0.5, beta=0.5) == pytest.approx(2.0 / 3.0)
    assert mod.szymkiewicz_simpson_overlap(left, right) == pytest.approx(1.0)


def test_compare_libraries_v2_exposes_asymmetric_and_overlap_scores() -> None:
    comparison = compare_libraries_v2(
        {
            "libraries": {
                "lib_a": {},
                "lib_b": {},
            }
        },
        {
            "libraries": {
                "lib_a": {},
                "lib_b": {},
                "lib_c": {},
                "lib_d": {},
            }
        },
    )

    assert comparison["jaccard"] == pytest.approx(0.5)
    assert comparison["score_jaccard"] == pytest.approx(0.5)
    assert comparison["score_overlap"] == pytest.approx(1.0)
    assert comparison["score_tversky_asym_ab"] == pytest.approx(2.0 / 2.2)
    assert comparison["score_tversky_asym_ba"] == pytest.approx(2.0 / 3.8)
    assert comparison["score_tversky_asym_ab"] > comparison["score_tversky_asym_ba"]

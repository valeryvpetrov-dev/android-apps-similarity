#!/usr/bin/env python3
"""TDD tests for SCREENING-27 threshold train/test calibration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_thresh_calibrate_train_test import (  # noqa: E402
    build_ground_truth_pairs,
    build_roc_curve,
    calibrate_on_splits,
    score_pairs,
    select_operating_point,
    split_train_test,
    threshold_grid,
)


def _make_app(
    app_id: str,
    *,
    package_name: str,
    signing_prefix: str,
    code_features: set[str],
) -> dict:
    return {
        "app_id": app_id,
        "layers": {
            "code": set(code_features),
            "component": set(),
            "resource": set(),
            "metadata": {
                "package_name:{}".format(package_name),
                "signing_present:1",
                "signing_prefix:{}".format(signing_prefix),
            },
            "library": set(),
        },
    }


def _overlap_pair(prefix: str, package_name: str, signing_prefix: str) -> tuple[dict, dict]:
    common = {"{}:shared:{}".format(prefix, index) for index in range(6)}
    left = common | {"{}:left:{}".format(prefix, index) for index in range(2)}
    right = common | {"{}:right:{}".format(prefix, index) for index in range(2)}
    return (
        _make_app(
            "{}_1".format(prefix),
            package_name=package_name,
            signing_prefix=signing_prefix,
            code_features=left,
        ),
        _make_app(
            "{}_2".format(prefix),
            package_name=package_name,
            signing_prefix=signing_prefix,
            code_features=right,
        ),
    )


def test_synthetic_known_pairs_select_expected_f1_threshold() -> None:
    alpha_a, alpha_b = _overlap_pair("alpha", "org.example.alpha", "aaaabbbb")
    beta_a, beta_b = _overlap_pair("beta", "org.example.beta", "ccccdddd")
    records = [alpha_a, alpha_b, beta_a, beta_b]

    pairs = build_ground_truth_pairs(records)
    scored_pairs = score_pairs(records, pairs, selected_layers=["code"])
    roc = build_roc_curve(scored_pairs, thresholds=threshold_grid())
    optimal = select_operating_point(roc, strategy="f1")

    assert optimal["threshold"] == pytest.approx(0.60)
    assert optimal["precision"] == pytest.approx(1.0)
    assert optimal["recall"] == pytest.approx(1.0)
    assert optimal["f1"] == pytest.approx(1.0)


def test_train_test_split_is_deterministic_for_fixed_seed() -> None:
    apk_paths = [Path("app_{:03d}.apk".format(index)) for index in range(10)]

    train_a, test_a = split_train_test(apk_paths, train_ratio=0.7, seed=2027)
    train_b, test_b = split_train_test(apk_paths, train_ratio=0.7, seed=2027)

    assert train_a == train_b
    assert test_a == test_b
    assert len(train_a) == 7
    assert len(test_a) == 3
    assert set(train_a).isdisjoint(test_a)
    assert sorted(train_a + test_a) == apk_paths


def test_test_metrics_do_not_exceed_train_by_more_than_margin() -> None:
    alpha_a, alpha_b = _overlap_pair("alpha", "org.example.alpha", "aaaabbbb")
    beta_a, beta_b = _overlap_pair("beta", "org.example.beta", "ccccdddd")

    hard_a = _make_app(
        "hard_1",
        package_name="org.example.hard",
        signing_prefix="eeeeffff",
        code_features={"hard:shared:{}".format(index) for index in range(2)}
        | {"hard:left:{}".format(index) for index in range(8)},
    )
    hard_b = _make_app(
        "hard_2",
        package_name="org.example.hard",
        signing_prefix="eeeeffff",
        code_features={"hard:shared:{}".format(index) for index in range(2)}
        | {"hard:right:{}".format(index) for index in range(8)},
    )
    distractor = _make_app(
        "distractor",
        package_name="org.example.distractor",
        signing_prefix="99990000",
        code_features={"distractor:{}".format(index) for index in range(10)},
    )

    report = calibrate_on_splits(
        train_records=[alpha_a, alpha_b, beta_a, beta_b],
        test_records=[hard_a, hard_b, distractor],
        thresholds=threshold_grid(),
        selected_layers=["code"],
        metric="jaccard",
        selection_strategy="f1",
    )

    assert report["optimal"]["threshold"] == pytest.approx(0.60)
    assert report["train"]["metrics"]["f1"] == pytest.approx(1.0)
    assert report["test"]["metrics"]["f1"] <= report["train"]["metrics"]["f1"] + 0.02

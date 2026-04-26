#!/usr/bin/env python3
"""TDD tests for SCREENING-24-LSH-RECALL-IMPROVE."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from calibrate_lsh_recall import run_lsh_recall_grid


def _make_app(app_id: str, code_features: set[str]) -> dict:
    return {
        "app_id": app_id,
        "layers": {
            "code": set(code_features),
            "component": set(),
            "resource": set(),
            "metadata": set(),
            "library": set(),
        },
    }


def _overlap_pair(prefix: str, shared: int, left_only: int, right_only: int) -> tuple[dict, dict]:
    common = {"{}:shared:{}".format(prefix, index) for index in range(shared)}
    left = common | {"{}:left:{}".format(prefix, index) for index in range(left_only)}
    right = common | {"{}:right:{}".format(prefix, index) for index in range(right_only)}
    return _make_app("{}-A".format(prefix), left), _make_app("{}-B".format(prefix), right)


def test_run_lsh_recall_grid_returns_per_config_and_best_summaries() -> None:
    app_a, app_b = _overlap_pair("strong", shared=18, left_only=4, right_only=4)
    app_c = _make_app("noise-C", {"noise:c:{}".format(index) for index in range(30)})
    app_d = _make_app("noise-D", {"noise:d:{}".format(index) for index in range(30)})

    report = run_lsh_recall_grid(
        [app_a, app_b, app_c, app_d],
        num_perm_grid=[64, 128, 256],
        bands_grid=[16, 32, 64],
        thresh=0.28,
    )

    assert report["status"] == "ok"
    assert sorted(report.keys()) == [
        "best_by_balanced",
        "best_by_recall",
        "config",
        "per_config",
        "status",
        "warnings",
    ]
    assert len(report["per_config"]) == 9

    first = report["per_config"][0]
    for field in (
        "num_perm",
        "bands",
        "recall_at_shortlist",
        "shortlist_size",
        "false_negative_rate",
    ):
        assert field in first

    assert report["best_by_recall"]["recall_at_shortlist"] >= 0.0
    assert report["best_by_balanced"]["shortlist_size"] >= 0


def test_grid_finds_higher_recall_geometry_for_synthetic_near_threshold_pairs() -> None:
    strong_a, strong_b = _overlap_pair("known-high", shared=24, left_only=8, right_only=8)
    near_a, near_b = _overlap_pair("near-thresh", shared=8, left_only=8, right_only=8)
    app_noise = _make_app("noise", {"noise:{}".format(index) for index in range(40)})

    report = run_lsh_recall_grid(
        [strong_a, strong_b, near_a, near_b, app_noise],
        num_perm_grid=[128, 256],
        bands_grid=[32, 64],
        thresh=0.28,
    )

    current = next(
        row for row in report["per_config"] if row["num_perm"] == 128 and row["bands"] == 32
    )
    tuned = [
        row
        for row in report["per_config"]
        if (row["num_perm"], row["bands"]) in {(128, 64), (256, 64)}
    ]

    assert ("known-high-A", "known-high-B") in {
        tuple(pair) for pair in current["shortlist_positive_pairs"]
    }
    assert any(row["recall_at_shortlist"] > current["recall_at_shortlist"] for row in tuned)


def test_run_lsh_recall_grid_reports_insufficient_corpus_without_raising() -> None:
    report = run_lsh_recall_grid(
        [_make_app("only-one", {"a", "b", "c"})],
        num_perm_grid=[64, 128],
        bands_grid=[16, 32],
        thresh=0.28,
    )

    assert report["status"] == "insufficient_corpus"
    assert report["per_config"] == []
    assert report["best_by_recall"] is None
    assert report["best_by_balanced"] is None
    assert report["warnings"]

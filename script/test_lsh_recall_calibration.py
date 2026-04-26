#!/usr/bin/env python3
"""TDD tests for SCREENING-24-LSH-RECALL-IMPROVE."""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from calibrate_lsh_recall import run_lsh_recall_grid

FDROID_V2_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
FDROID_ARTIFACT_REPORT = Path(
    "experiments/artifacts/SCREENING-25-LSH-FDROID/report.json"
)


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


def _require_fdroid_v2_corpus() -> Path:
    if not FDROID_V2_CORPUS_DIR.exists():
        pytest.fail("F-Droid v2 APK corpus not found: {}".format(FDROID_V2_CORPUS_DIR))
    apk_count = len(list(FDROID_V2_CORPUS_DIR.glob("*.apk")))
    assert apk_count >= 200
    return FDROID_V2_CORPUS_DIR


@functools.lru_cache(maxsize=1)
def _fdroid_v2_grid_report() -> dict:
    return run_lsh_recall_grid(
        corpus_dir=_require_fdroid_v2_corpus(),
        num_perm_grid=[64, 128, 256],
        bands_grid=[16, 32, 64],
        thresh=0.28,
        clone_threshold=0.50,
    )


def test_fdroid_v2_lsh_grid_filters_shortlists_on_real_corpus() -> None:
    report = _fdroid_v2_grid_report()

    assert report["status"] == "ok"
    assert report["corpus"]["n_documents"] >= 200
    assert report["n_pairs_total"] == (
        report["corpus"]["n_documents"] * (report["corpus"]["n_documents"] - 1)
    ) // 2
    assert report["n_pairs_clone"] > 0
    assert len(report["per_config"]) == 9

    ok_rows = [row for row in report["per_config"] if row["status"] == "ok"]
    assert ok_rows
    assert any(row["shortlist_size"] < report["n_pairs_total"] for row in ok_rows)
    assert all(row["shortlist_size"] < report["n_pairs_total"] for row in ok_rows)


def test_fdroid_v2_grid_finds_production_sized_optimal_config() -> None:
    report = _fdroid_v2_grid_report()

    optimal = report["optimal_config"]
    assert optimal is not None
    assert optimal["recall_at_shortlist"] >= 0.85
    assert optimal["shortlist_size"] <= int(report["n_pairs_total"] * 0.30)
    assert (optimal["num_perm"], optimal["bands"]) in {
        (row["num_perm"], row["bands"]) for row in report["per_config"]
    }


def test_screening25_fdroid_artifact_report_schema() -> None:
    assert FDROID_ARTIFACT_REPORT.exists()

    report = json.loads(FDROID_ARTIFACT_REPORT.read_text(encoding="utf-8"))
    assert report["corpus"]["name"] == "F-Droid v2"
    assert report["corpus"]["n_documents"] >= 200
    assert report["n_pairs_total"] > report["corpus"]["n_documents"]
    assert report["n_pairs_clone"] > 0
    assert isinstance(report["per_config"], list)
    assert report["per_config"]
    assert report["optimal_config"] is not None
    assert report["decision"]["production_default_changed"] in {True, False}

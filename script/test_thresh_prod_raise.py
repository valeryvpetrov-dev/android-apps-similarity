#!/usr/bin/env python3
"""TDD tests for SCREENING-28 production THRESH-002 raise."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner  # noqa: E402


OLD_THRESH_002 = 0.28


def _empty_layers() -> dict[str, set[str]]:
    return {
        "code": set(),
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": set(),
    }


def _app(app_id: str, code_features: set[str]) -> dict:
    layers = _empty_layers()
    layers["code"] = set(code_features)
    return {"app_id": app_id, "layers": layers}


def _app_pair(
    prefix: str,
    *,
    shared_count: int,
    left_unique_count: int,
    right_unique_count: int,
) -> tuple[dict, dict]:
    common = {"{}:common:{}".format(prefix, index) for index in range(shared_count)}
    left = common | {
        "{}:left:{}".format(prefix, index) for index in range(left_unique_count)
    }
    right = common | {
        "{}:right:{}".format(prefix, index) for index in range(right_unique_count)
    }
    return _app("{}-a".format(prefix), left), _app("{}-b".format(prefix), right)


def _write_config(tmp_path: Path, threshold: float) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "cascade.yaml"
    config_path.write_text(
        """\
stages:
  screening:
    features: [code]
    metric: jaccard
    threshold: {threshold}
""".format(threshold=threshold),
        encoding="utf-8",
    )
    return config_path


def test_production_thresh_002_constant_and_config_are_070() -> None:
    assert screening_runner.THRESH_002_PRODUCTION == pytest.approx(0.70)
    assert abs(screening_runner.THRESH_002_PRODUCTION - OLD_THRESH_002) > 1e-9

    config = screening_runner.load_yaml_or_json(
        PROJECT_ROOT / "exp" / "configs" / "optimal-cascade-v4.yaml"
    )
    _, _, threshold = screening_runner.extract_screening_stage(config)

    assert threshold == pytest.approx(screening_runner.THRESH_002_PRODUCTION)


@pytest.mark.parametrize(
    ("shared_count", "left_unique_count", "right_unique_count", "expected_score"),
    [
        (3, 3, 4, 0.30),
        (6, 2, 2, 0.60),
    ],
)
def test_midrange_jaccard_pairs_flip_from_old_positive_to_new_negative(
    shared_count: int,
    left_unique_count: int,
    right_unique_count: int,
    expected_score: float,
) -> None:
    left, right = _app_pair(
        "midrange-{}".format(shared_count),
        shared_count=shared_count,
        left_unique_count=left_unique_count,
        right_unique_count=right_unique_count,
    )
    score = screening_runner.calculate_pair_score(
        app_a=left,
        app_b=right,
        metric="jaccard",
        selected_layers=["code"],
        ins_block_sim_threshold=0.80,
        ged_timeout_sec=30,
        processes_count=1,
        threads_count=2,
    )

    assert score == pytest.approx(expected_score)
    assert OLD_THRESH_002 <= score < screening_runner.THRESH_002_PRODUCTION

    old_shortlist = screening_runner.build_candidate_list(
        app_records=[left, right],
        selected_layers=["code"],
        metric="jaccard",
        threshold=OLD_THRESH_002,
        ins_block_sim_threshold=0.80,
        ged_timeout_sec=30,
        processes_count=1,
        threads_count=2,
    )
    new_shortlist = screening_runner.build_candidate_list(
        app_records=[left, right],
        selected_layers=["code"],
        metric="jaccard",
        threshold=screening_runner.THRESH_002_PRODUCTION,
        ins_block_sim_threshold=0.80,
        ged_timeout_sec=30,
        processes_count=1,
        threads_count=2,
    )

    assert [row["screening_status"] for row in old_shortlist] == [
        "preliminary_positive"
    ]
    assert new_shortlist == []


def test_run_screening_mini_corpus_returns_smaller_shortlist_with_new_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIMILARITY_SKIP_REQ_CHECK", "1")
    strong_a, strong_b = _app_pair(
        "strong", shared_count=6, left_unique_count=1, right_unique_count=1
    )
    medium_a, medium_b = _app_pair(
        "medium", shared_count=4, left_unique_count=2, right_unique_count=2
    )
    weak_a, weak_b = _app_pair(
        "weak", shared_count=3, left_unique_count=3, right_unique_count=3
    )
    app_records = [strong_a, strong_b, medium_a, medium_b, weak_a, weak_b]

    old_shortlist = screening_runner.run_screening(
        _write_config(tmp_path / "old", OLD_THRESH_002),
        app_records=app_records,
    )
    new_shortlist = screening_runner.run_screening(
        _write_config(tmp_path / "new", screening_runner.THRESH_002_PRODUCTION),
        app_records=app_records,
    )

    assert len(old_shortlist) == 3
    assert len(new_shortlist) == 1
    assert len(new_shortlist) < len(old_shortlist)
    assert all(
        row["screening_status"] == "preliminary_positive"
        for row in old_shortlist + new_shortlist
    )

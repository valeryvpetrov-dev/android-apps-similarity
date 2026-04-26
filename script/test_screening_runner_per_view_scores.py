#!/usr/bin/env python3
"""Tests for screening_runner per-view scores (EXEC-087.1).

The tests pin down the contract of ``compute_per_view_scores`` and verify that
``build_candidate_list`` writes the ``per_view_scores`` field onto every emitted
candidate so downstream deepening/pairwise stages can reuse screening evidence
without recomputation (unblocks EXEC-086 per-view weight calibration).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import (
    build_candidate_list,
    compute_per_view_scores,
)


BASE_CHANNELS = ("jaccard", "tversky_a", "tversky_b", "overlap_min")


def _make_app(app_id: str, layers: dict[str, set[str]]) -> dict:
    return {"app_id": app_id, "layers": layers}


def _assert_layer_channels(testcase: unittest.TestCase, value: dict) -> None:
    testcase.assertIsInstance(value, dict)
    for channel in BASE_CHANNELS:
        testcase.assertIn(channel, value)
        testcase.assertIsInstance(value[channel], float)
        testcase.assertGreaterEqual(value[channel], 0.0)
        testcase.assertLessEqual(value[channel], 1.0)


class TestComputePerViewScores(unittest.TestCase):
    def test_returns_dict_with_entry_per_requested_layer(self) -> None:
        app_a = _make_app(
            "A",
            {
                "code": {"f1", "f2"},
                "component": {"c1"},
                "resource": {"r1", "r2", "r3"},
                "metadata": {"m1"},
                "library": {"l1", "l2"},
            },
        )
        app_b = _make_app(
            "B",
            {
                "code": {"f2", "f3"},
                "component": {"c1", "c2"},
                "resource": {"r2"},
                "metadata": {"m2"},
                "library": {"l1"},
            },
        )

        scores = compute_per_view_scores(
            app_a=app_a,
            app_b=app_b,
            layers=["code", "component", "resource", "metadata", "library"],
            metric="jaccard",
        )

        self.assertEqual(
            set(scores.keys()),
            {"code", "component", "resource", "metadata", "library"},
        )
        for layer, value in scores.items():
            _assert_layer_channels(self, value)

    def test_identical_features_yield_score_one_per_layer(self) -> None:
        app = _make_app(
            "X",
            {
                "code": {"alpha", "beta"},
                "component": {"gamma"},
                "resource": {"delta", "epsilon"},
                "metadata": {"zeta"},
                "library": {"eta", "theta"},
            },
        )

        scores = compute_per_view_scores(
            app_a=app,
            app_b=app,
            layers=["code", "component", "resource", "metadata", "library"],
            metric="jaccard",
        )

        self.assertEqual(
            set(scores),
            {"code", "component", "resource", "metadata", "library"},
        )
        for value in scores.values():
            _assert_layer_channels(self, value)
            for channel in BASE_CHANNELS:
                self.assertEqual(value[channel], 1.0)

    def test_empty_features_yield_score_zero_per_layer(self) -> None:
        empty_a = _make_app(
            "EMPTY-A",
            {
                "code": set(),
                "component": set(),
                "resource": set(),
                "metadata": set(),
                "library": set(),
            },
        )
        empty_b = _make_app(
            "EMPTY-B",
            {
                "code": set(),
                "component": set(),
                "resource": set(),
                "metadata": set(),
                "library": set(),
            },
        )

        scores = compute_per_view_scores(
            app_a=empty_a,
            app_b=empty_b,
            layers=["code", "component", "resource", "metadata", "library"],
            metric="jaccard",
        )

        self.assertEqual(
            set(scores),
            {"code", "component", "resource", "metadata", "library"},
        )
        for value in scores.values():
            _assert_layer_channels(self, value)
            for channel in BASE_CHANNELS:
                self.assertEqual(value[channel], 0.0)


class TestBuildCandidateListPerViewScores(unittest.TestCase):
    def test_build_candidate_list_writes_per_view_scores_for_every_candidate(self) -> None:
        app_a = _make_app(
            "APP-A",
            {
                "code": {"m1", "m2", "m3"},
                "component": {"activity:Main"},
                "resource": {"res_type:layout", "res_ext:xml"},
                "metadata": {"package_name:com.example.a"},
                "library": {"lib_abi:arm64-v8a"},
            },
        )
        app_b = _make_app(
            "APP-B",
            {
                "code": {"m2", "m3", "m4"},
                "component": {"activity:Main", "activity:Detail"},
                "resource": {"res_type:layout"},
                "metadata": {"package_name:com.example.b"},
                "library": {"lib_abi:arm64-v8a", "lib_abi:x86_64"},
            },
        )
        selected_layers = ["code", "component", "resource", "metadata", "library"]

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=selected_layers,
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("per_view_scores", row)
        per_view = row["per_view_scores"]
        self.assertEqual(set(per_view.keys()), set(selected_layers))
        for layer, value in per_view.items():
            _assert_layer_channels(self, value)
        # Sanity: code jaccard on {m1,m2,m3} vs {m2,m3,m4} = 2/4 = 0.5
        self.assertAlmostEqual(per_view["code"]["jaccard"], 0.5, places=6)

    def test_build_candidate_list_writes_per_view_scores_for_ged_metric(self) -> None:
        app_records = [
            {"app_id": "APP-A", "layers": {"code": {"a", "b"}}},
            {"app_id": "APP-B", "layers": {"code": {"b", "c"}}},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.42  # type: ignore[assignment]
            candidate_list = build_candidate_list(
                app_records=app_records,
                selected_layers=["code"],
                metric="ged",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        finally:
            screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("per_view_scores", row)
        self.assertAlmostEqual(
            row["per_view_scores"]["code"]["jaccard"], 1.0 / 3.0, places=6
        )
        self.assertEqual(row["retrieval_rank"], 1)
        self.assertEqual(row["retrieval_features_used"], ["code"])


if __name__ == "__main__":
    unittest.main()

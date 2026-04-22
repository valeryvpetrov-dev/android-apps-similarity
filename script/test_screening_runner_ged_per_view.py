#!/usr/bin/env python3
"""Regression tests for GED per-view screening evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import (
    aggregate_features,
    build_candidate_list,
    compute_per_view_scores,
    jaccard_similarity,
)


def _make_app(app_id: str, layers: dict[str, set[str]]) -> dict:
    return {"app_id": app_id, "layers": layers}


class TestGedPerViewScores(unittest.TestCase):
    def test_compute_per_view_scores_returns_jaccard_scores_for_ged(self) -> None:
        app_a = _make_app(
            "APP-A",
            {
                "code": {"a", "b", "c"},
                "metadata": {"package:one"},
            },
        )
        app_b = _make_app(
            "APP-B",
            {
                "code": {"b", "c", "d"},
                "metadata": {"package:one", "sdk:34"},
            },
        )

        scores = compute_per_view_scores(
            app_a=app_a,
            app_b=app_b,
            layers=["code", "metadata"],
            metric="ged",
        )

        self.assertEqual(set(scores), {"code", "metadata"})
        self.assertAlmostEqual(scores["code"], 0.5, places=6)
        self.assertAlmostEqual(scores["metadata"], 0.5, places=6)

    def test_ged_scores_match_layer_jaccard_on_same_input(self) -> None:
        app_a = _make_app(
            "APP-A",
            {
                "code": {"method:a", "method:b", "method:c"},
                "component": {"activity:Main", "service:Sync"},
                "library": {"retrofit", "okhttp"},
            },
        )
        app_b = _make_app(
            "APP-B",
            {
                "code": {"method:b", "method:c", "method:d"},
                "component": {"activity:Main", "receiver:Boot"},
                "library": {"okhttp", "coil"},
            },
        )
        layers = ["code", "component", "library"]

        scores = compute_per_view_scores(
            app_a=app_a,
            app_b=app_b,
            layers=layers,
            metric="ged",
        )

        for layer in layers:
            expected = jaccard_similarity(
                aggregate_features(app_a, [layer]),
                aggregate_features(app_b, [layer]),
            )
            self.assertAlmostEqual(scores[layer], expected, places=6)

    def test_build_candidate_list_writes_per_view_scores_for_ged_metric(self) -> None:
        app_a = _make_app("APP-A", {"code": {"x", "y", "z"}})
        app_b = _make_app("APP-B", {"code": {"y", "z", "w"}})

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.75  # type: ignore[assignment]
            candidate_list = build_candidate_list(
                app_records=[app_a, app_b],
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
        self.assertAlmostEqual(row["per_view_scores"]["code"], 0.5, places=6)


if __name__ == "__main__":
    unittest.main()

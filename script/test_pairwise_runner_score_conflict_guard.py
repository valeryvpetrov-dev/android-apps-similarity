#!/usr/bin/env python3
"""Tests for conflict guards in the pairwise score decision policy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


class TestPairwiseScoreConflictGuard(unittest.TestCase):
    def test_zero_code_fingerprint_overlap_caps_library_reduced_review_score(self) -> None:
        pair_row = {
            "full_similarity_score": 0.625,
            "library_reduced_score": 0.625,
            "code_stats_containment_policy_applied": False,
            "code_stats_added_code_policy_applied": False,
            "preserved_core_similarity": 0.0,
            "preserved_core_method_count": 0,
            "added_code_representation": "code_fingerprint",
        }

        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)

        self.assertEqual(pair_row["status"], "low_similarity")
        self.assertAlmostEqual(pair_row["similarity_score"], 0.29)
        self.assertEqual(
            pair_row["similarity_score_source"],
            "code_conflict_guarded_library_reduced_score",
        )
        self.assertTrue(pair_row["score_conflict_guard_applied"])
        self.assertAlmostEqual(
            pair_row["score_conflict_guard_original_score"],
            0.625,
        )
        self.assertEqual(
            pair_row["score_conflict_guard_reason"],
            "zero_code_fingerprint_overlap_for_library_reduced_review_score",
        )

    def test_guard_uses_review_band_even_when_runtime_threshold_is_low(self) -> None:
        pair_row = {
            "full_similarity_score": 0.625,
            "library_reduced_score": 0.625,
            "code_stats_containment_policy_applied": False,
            "code_stats_added_code_policy_applied": False,
            "preserved_core_similarity": 0.0,
            "preserved_core_method_count": 0,
            "added_code_representation": "code_fingerprint",
        }

        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.30)

        self.assertAlmostEqual(pair_row["similarity_score"], 0.29)
        self.assertTrue(pair_row["score_conflict_guard_applied"])

    def test_score_conflict_guard_is_exposed_as_pairwise_evidence(self) -> None:
        pair_row = {
            "full_similarity_score": 0.625,
            "library_reduced_score": 0.625,
            "views_used": ["code", "resource", "library"],
            "code_stats_containment_policy_applied": False,
            "code_stats_added_code_policy_applied": False,
            "preserved_core_similarity": 0.0,
            "preserved_core_method_count": 0,
            "added_code_representation": "code_fingerprint",
        }

        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)
        evidence = pairwise_runner.collect_evidence_from_pairwise(pair_row)
        guard_items = [
            item
            for item in evidence
            if item["signal_type"] == "score_conflict_guard"
        ]

        self.assertEqual(len(guard_items), 1)
        self.assertEqual(guard_items[0]["source_stage"], "pairwise")
        self.assertEqual(
            guard_items[0]["ref"],
            "score_conflict_guard_zero_code_fingerprint_v1",
        )
        self.assertEqual(guard_items[0]["magnitude"], 1.0)


if __name__ == "__main__":
    unittest.main()

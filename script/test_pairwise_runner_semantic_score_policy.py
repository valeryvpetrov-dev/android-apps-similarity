#!/usr/bin/env python3
"""Tests for semantic_multiview as a guarded pairwise score source."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


def _semantic_result(
    *,
    band: str,
    relation: str,
    code_stats: float,
    resource_identity: float,
    resource_structure: float,
) -> dict:
    return {
        "profile_id": "R_semantic_multiview_decision_policy_v0",
        "status": "success",
        "semantic_band": band,
        "semantic_relation": relation,
        "semantic_score": 0.65,
        "scores": {
            "R_code_identity": 0.0,
            "R_code_stats": code_stats,
            "R_code_packaging": 1.0,
            "R_resource_identity": resource_identity,
            "R_resource_structure": resource_structure,
        },
    }


class TestSemanticMultiviewScorePolicy(unittest.TestCase):
    def test_high_same_resources_code_stats_promotes_core_score(self) -> None:
        pair_row = {
            "full_similarity_score": 0.18,
            "library_reduced_score": 0.18,
            "views_used": ["code", "resource"],
        }
        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)
        pair_row["semantic_multiview"] = _semantic_result(
            band="high",
            relation="same_resources_code_stats_match",
            code_stats=0.91,
            resource_identity=0.94,
            resource_structure=0.88,
        )

        pairwise_runner.apply_semantic_multiview_score_policy(
            pair_row,
            threshold=0.70,
        )

        self.assertEqual(pair_row["status"], "success")
        self.assertAlmostEqual(pair_row["similarity_score"], 0.91)
        self.assertEqual(
            pair_row["similarity_score_source"],
            "semantic_multiview_high_same_resources_code_stats_match",
        )
        self.assertEqual(pair_row["score_decision_selected_priority"], "P0_guard")
        self.assertTrue(pair_row["semantic_multiview_score_policy_applied"])

    def test_semantic_high_can_resolve_library_reduced_conflict_guard(self) -> None:
        pair_row = {
            "full_similarity_score": 0.31,
            "library_reduced_score": 0.31,
            "code_stats_containment_policy_applied": False,
            "code_stats_added_code_policy_applied": False,
            "preserved_core_similarity": 0.0,
            "preserved_core_method_count": 0,
            "added_code_representation": "code_fingerprint",
            "views_used": ["code", "resource"],
        }
        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)
        self.assertEqual(
            pair_row["similarity_score_source"],
            "code_conflict_guarded_library_reduced_score",
        )
        pair_row["semantic_multiview"] = _semantic_result(
            band="high",
            relation="same_resources_code_stats_match",
            code_stats=1.0,
            resource_identity=1.0,
            resource_structure=1.0,
        )

        pairwise_runner.apply_semantic_multiview_score_policy(
            pair_row,
            threshold=0.70,
        )

        self.assertEqual(pair_row["status"], "success")
        self.assertAlmostEqual(pair_row["similarity_score"], 1.0)
        self.assertEqual(
            pair_row["similarity_score_source"],
            "semantic_multiview_high_same_resources_code_stats_match",
        )
        self.assertEqual(pair_row["score_decision_selected_priority"], "P0_guard")
        self.assertTrue(pair_row["score_conflict_guard_applied"])
        self.assertTrue(pair_row["semantic_multiview_score_policy_applied"])

    def test_semantic_review_does_not_promote_score(self) -> None:
        pair_row = {
            "full_similarity_score": 0.18,
            "library_reduced_score": 0.18,
            "views_used": ["code", "resource"],
        }
        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)
        pair_row["semantic_multiview"] = _semantic_result(
            band="review",
            relation="same_resources_without_code_identity",
            code_stats=0.20,
            resource_identity=0.94,
            resource_structure=0.88,
        )

        pairwise_runner.apply_semantic_multiview_score_policy(
            pair_row,
            threshold=0.70,
        )

        self.assertEqual(pair_row["status"], "low_similarity")
        self.assertAlmostEqual(pair_row["similarity_score"], 0.18)
        self.assertEqual(pair_row["similarity_score_source"], "library_reduced_score")
        self.assertFalse(pair_row["semantic_multiview_score_policy_applied"])

    def test_semantic_high_does_not_mask_analysis_failed(self) -> None:
        pair_row = {
            "full_similarity_score": None,
            "library_reduced_score": None,
            "status": "analysis_failed",
            "views_used": ["code", "resource"],
            "semantic_multiview": _semantic_result(
                band="high",
                relation="same_resources_code_stats_match",
                code_stats=0.91,
                resource_identity=0.94,
                resource_structure=0.88,
            ),
        }

        pairwise_runner.apply_semantic_multiview_score_policy(
            pair_row,
            threshold=0.70,
        )

        self.assertEqual(pair_row["status"], "analysis_failed")
        self.assertIsNone(pair_row.get("similarity_score"))
        self.assertFalse(pair_row["semantic_multiview_score_policy_applied"])

    def test_semantic_promotion_is_exposed_as_pairwise_evidence(self) -> None:
        pair_row = {
            "full_similarity_score": 0.18,
            "library_reduced_score": 0.18,
            "views_used": ["code", "resource"],
        }
        pairwise_runner.apply_code_stats_score_policy(pair_row, threshold=0.70)
        pair_row["semantic_multiview"] = _semantic_result(
            band="high",
            relation="same_resources_code_stats_match",
            code_stats=0.91,
            resource_identity=0.94,
            resource_structure=0.88,
        )
        pairwise_runner.apply_semantic_multiview_score_policy(
            pair_row,
            threshold=0.70,
        )

        evidence = pairwise_runner.collect_evidence_from_pairwise(pair_row)
        semantic_items = [
            item
            for item in evidence
            if item["signal_type"] == "semantic_multiview_score_promotion"
        ]

        self.assertEqual(len(semantic_items), 1)
        self.assertEqual(semantic_items[0]["source_stage"], "pairwise")
        self.assertAlmostEqual(semantic_items[0]["magnitude"], 0.91)
        self.assertEqual(
            semantic_items[0]["ref"],
            "R_semantic_multiview_score_promotion",
        )


if __name__ == "__main__":
    unittest.main()

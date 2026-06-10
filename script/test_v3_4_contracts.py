#!/usr/bin/env python3
"""Tests for minimal v3.4 architecture contracts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
from v3_4_contracts import (
    V3_4_SCHEMA_VERSION,
    build_compatibility_check_record,
    build_pair_evidence_record,
    build_pair_similarity_result,
)


class TestV34PairEvidenceRecord(unittest.TestCase):
    def test_pair_evidence_record_wraps_existing_evidence_without_rewriting(self) -> None:
        evidence = [
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.91,
                "ref": "component",
            }
        ]

        record = build_pair_evidence_record(
            pair_id="PAIR-001",
            evidence=evidence,
            status="success",
        )

        self.assertEqual(record["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(record["record_type"], "PairEvidenceRecord")
        self.assertEqual(record["pair_id"], "PAIR-001")
        self.assertEqual(record["evidence_count"], 1)
        self.assertEqual(record["evidence"], evidence)


class TestV34CompatibilityCheckRecord(unittest.TestCase):
    def test_compatibility_check_reports_missing_inputs_separately(self) -> None:
        record = build_compatibility_check_record(
            pair_id="PAIR-002",
            app_a="A",
            app_b=None,
        )

        self.assertEqual(record["record_type"], "CompatibilityCheckRecord")
        self.assertEqual(record["status"], "incompatible_inputs")
        self.assertIn("missing_app_b", record["reasons"])


class TestV34PairSimilarityResult(unittest.TestCase):
    def test_low_similarity_is_successful_comparison_with_low_score(self) -> None:
        result = build_pair_similarity_result(
            {
                "pair_id": "PAIR-003",
                "app_a": "A",
                "app_b": "B",
                "status": "low_similarity",
                "full_similarity_score": 0.12,
                "library_reduced_score": 0.05,
                "views_used": ["component"],
                "evidence": [],
            }
        )

        self.assertEqual(result["record_type"], "PairSimilarityResult")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source_status"], "low_similarity")
        self.assertEqual(result["scores"]["similarity_score"], 0.05)
        self.assertEqual(result["compatibility_check"]["status"], "compatible")

    def test_analysis_failed_keeps_score_unknown_instead_of_zero(self) -> None:
        result = build_pair_similarity_result(
            {
                "pair_id": "PAIR-004",
                "app_a": "A",
                "app_b": "B",
                "status": "analysis_failed",
                "analysis_failed_reason": "view_build_failed",
                "full_similarity_score": None,
                "library_reduced_score": None,
                "views_used": ["component"],
                "evidence": [],
            }
        )

        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["scores"]["similarity_score"])
        self.assertIn("view_build_failed", result["limitations"])


class TestV34DetailedJsonIntegration(unittest.TestCase):
    def test_export_pairwise_detailed_json_adds_v3_4_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[
                    {
                        "app_a": "A",
                        "app_b": "B",
                        "full_similarity_score": 0.91,
                        "library_reduced_score": 0.87,
                        "status": "success",
                        "views_used": ["component", "resource"],
                        "signature_match": {"score": 1.0, "status": "match"},
                        "evidence": [
                            {
                                "source_stage": "pairwise",
                                "signal_type": "layer_score",
                                "magnitude": 0.91,
                                "ref": "component",
                            }
                        ],
                    }
                ],
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        item = payload["pairs"][0]
        self.assertIn("pair_evidence_record", item)
        self.assertIn("compatibility_check", item)
        self.assertIn("pair_similarity_result", item)
        self.assertEqual(item["pair_similarity_result"]["record_type"], "PairSimilarityResult")
        self.assertEqual(item["pair_similarity_result"]["status"], "success")
        self.assertEqual(item["pair_evidence_record"]["record_type"], "PairEvidenceRecord")
        self.assertEqual(item["compatibility_check"]["record_type"], "CompatibilityCheckRecord")


if __name__ == "__main__":
    unittest.main()


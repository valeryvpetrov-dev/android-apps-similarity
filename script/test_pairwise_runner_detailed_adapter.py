#!/usr/bin/env python3
"""Tests for pairwise_runner detailed result adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


class TestPairwiseRunnerDetailedAdapter(unittest.TestCase):
    def test_run_pairwise_detailed_builds_contract_record_for_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource, library]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "pair_id": "PAIR-001",
                                "dataset_id": "DS-TEST",
                                "prototype_id": "P-TEST",
                                "prototype_sha": "abc123",
                                "representation_mode": "R_multiview_partial",
                                "candidate_list_row_ref": "candidate://PAIR-001",
                                "screening_explanation_ref": "screening://PAIR-001",
                                "noise_summary_ref": "noise://summary/PAIR-001",
                                "noise_profile_ref": "noise://profile/PAIR-001",
                                "deepening_artifact_refs": ["deep://PAIR-001/component"],
                                "app_a": {
                                    "app_id": "A",
                                    "apk_path": str(apk_a),
                                    "decoded_dir": "/tmp/decoded-a",
                                },
                                "app_b": {
                                    "app_id": "B",
                                    "apk_path": str(apk_b),
                                    "decoded_dir": "/tmp/decoded-b",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {"androidx.appcompat": {"class_count": 10}},
                },
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ):
                payload = pairwise_runner.run_pairwise_detailed(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        self.assertEqual(len(payload), 1)
        result = payload[0]
        self.assertEqual(result["pair_id"], "PAIR-001")
        self.assertEqual(result["apps"]["app_a"]["app_id"], "A")
        self.assertEqual(result["apps"]["app_b"]["app_id"], "B")
        self.assertEqual(result["analysis_status"], "success")
        self.assertIsNone(result["failure_reason"])
        self.assertEqual(result["representation_mode"], "R_multiview_partial")
        self.assertEqual(result["views"]["component"]["view_status"], "success")
        self.assertEqual(result["views"]["resource"]["view_status"], "success")
        self.assertEqual(result["views"]["library"]["view_status"], "success")
        self.assertAlmostEqual(result["scores"]["full_similarity_score"], 1.0)
        self.assertAlmostEqual(result["scores"]["library_reduced_score"], 1.0)
        self.assertAlmostEqual(result["scores"]["selected_similarity_score"], 1.0)
        self.assertEqual(result["explanation"]["explanation_status"], "not_available")
        self.assertEqual(result["artifacts"]["artifacts_path"], "pairwise://PAIR-001")
        self.assertEqual(result["artifacts"]["candidate_list_row_ref"], "candidate://PAIR-001")
        self.assertEqual(result["artifacts"]["screening_explanation_ref"], "screening://PAIR-001")
        self.assertEqual(result["artifacts"]["noise_summary_ref"], "noise://summary/PAIR-001")
        self.assertEqual(result["artifacts"]["noise_profile_ref"], "noise://profile/PAIR-001")
        self.assertEqual(result["artifacts"]["deepening_artifact_refs"], ["deep://PAIR-001/component"])
        self.assertEqual(result["run_context"]["dataset_id"], "DS-TEST")
        self.assertEqual(result["run_context"]["prototype_sha"], "abc123")

    def test_run_pairwise_detailed_preserves_failure_without_zero_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "pair_id": "PAIR-FAIL",
                                "dataset_id": "DS-TEST",
                                "prototype_id": "P-TEST",
                                "prototype_sha": "abc123",
                                "representation_mode": "R_multiview_partial",
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = pairwise_runner.run_pairwise_detailed(
                config_path=config_path,
                enriched_path=enriched_path,
                ins_block_sim_threshold=0.8,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )

        result = payload[0]
        self.assertEqual(result["analysis_status"], "analysis_failed")
        self.assertEqual(result["failure_reason"], "view_build_failed")
        self.assertIsNone(result["scores"]["similarity_score"])
        self.assertIsNone(result["scores"]["full_similarity_score"])
        self.assertIsNone(result["scores"]["library_reduced_score"])
        self.assertIsNone(result["scores"]["selected_similarity_score"])
        self.assertEqual(result["views"]["component"]["view_status"], "failed")
        self.assertIn("missing_decoded_dir", result["views"]["component"]["errors"])
        self.assertEqual(result["explanation"]["explanation_status"], "not_available")
        self.assertFalse(result["explanation"]["library_impact_flag"])


if __name__ == "__main__":
    unittest.main()

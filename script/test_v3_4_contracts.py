#!/usr/bin/env python3
"""Tests for minimal v3.4 architecture contracts."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
import screening_runner
import v3_4_contracts
from v3_4_contracts import (
    V3_4_SCHEMA_VERSION,
    build_compatibility_check_record,
    build_pair_evidence_record,
    build_pair_similarity_result,
)


class TestV34PairEvidenceRecord(unittest.TestCase):
    def test_all_required_v3_4_contract_builders_exist(self) -> None:
        required_builder_names = [
            "build_architecture_profile",
            "build_representation_spec",
            "build_view_registry",
            "build_view_type_field_policy",
            "build_measure_registry",
            "build_noise_policy_registry",
            "build_noise_decision_record",
            "build_corpus_manifest",
            "build_apk_analysis_record",
            "build_view_artifact_record",
            "build_candidate_index_snapshot",
            "build_candidate_selection_record",
            "build_pair_precheck_record",
            "build_pair_check_registry",
            "build_pair_check_run",
            "build_pair_evidence_record",
            "build_pair_aggregation_policy",
            "build_pair_similarity_result",
            "build_search_similarity_result",
            "build_compatibility_check_record",
            "build_explanation_render_record",
            "build_benchmark_slice",
        ]

        missing = [
            name
            for name in required_builder_names
            if not callable(getattr(v3_4_contracts, name, None))
        ]

        self.assertEqual(missing, [])

    def test_build_architecture_profile_lists_active_extensions(self) -> None:
        builder = getattr(v3_4_contracts, "build_architecture_profile", None)
        self.assertIsNotNone(builder, "build_architecture_profile must exist")

        profile = builder(
            profile_id="demo-v3.4",
            active_views=["code", "component"],
            active_measures=["jaccard"],
            active_noise_policies=["library_view_v2"],
            active_pair_checks=["pairwise_jaccard"],
            aggregation_policy="library_reduced_score",
        )

        self.assertEqual(profile["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(profile["record_type"], "ArchitectureProfile")
        self.assertEqual(profile["profile_id"], "demo-v3.4")
        self.assertEqual(profile["active_views"], ["code", "component"])
        self.assertEqual(profile["active_measures"], ["jaccard"])
        self.assertEqual(profile["aggregation_policy"], "library_reduced_score")

    def test_build_representation_spec_links_views_measures_and_policies(self) -> None:
        builder = getattr(v3_4_contracts, "build_representation_spec", None)
        self.assertIsNotNone(builder, "build_representation_spec must exist")

        spec = builder(
            profile_id="demo-v3.4",
            views=["code", "component"],
            measures=["jaccard"],
            noise_policies=["library_view_v2"],
        )

        self.assertEqual(spec["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(spec["record_type"], "RepresentationSpec")
        self.assertEqual(spec["profile_id"], "demo-v3.4")
        self.assertEqual(spec["view_registry"]["record_type"], "ViewRegistry")
        self.assertEqual(spec["measure_registry"]["record_type"], "MeasureRegistry")
        self.assertEqual(spec["noise_policy_registry"]["record_type"], "NoisePolicyRegistry")

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


class TestV34CandidateSelectionRecord(unittest.TestCase):
    def test_build_noise_decision_record_wraps_noise_envelope(self) -> None:
        builder = getattr(v3_4_contracts, "build_noise_decision_record", None)
        self.assertIsNotNone(builder, "build_noise_decision_record must exist")

        record = builder(
            apk_id="APP-A",
            envelope={
                "schema_version": "nc-v1",
                "detector_source": "library_view_v2",
                "confidence": "high",
                "status": "success",
                "noise_reason": "library_dominant",
                "downstream_warnings": ["library_noise"],
                "evidence_refs": ["apk_path:/tmp/a.apk"],
                "noise_status": "noisy",
            },
        )

        self.assertEqual(record["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(record["record_type"], "NoiseDecisionRecord")
        self.assertEqual(record["apk_id"], "APP-A")
        self.assertEqual(record["detector_source"], "library_view_v2")
        self.assertEqual(record["noise_status"], "noisy")
        self.assertEqual(record["warnings"], ["library_noise"])
        self.assertEqual(record["evidence_refs"], ["apk_path:/tmp/a.apk"])

    def test_build_candidate_index_snapshot_records_lsh_params(self) -> None:
        builder = getattr(v3_4_contracts, "build_candidate_index_snapshot", None)
        self.assertIsNotNone(builder, "build_candidate_index_snapshot must exist")

        snapshot = builder(
            candidate_index_params={
                "type": "minhash_lsh",
                "num_perm": 128,
                "bands": 32,
                "seed": 42,
                "features": ["code", "resource"],
            },
            corpus_size=2,
        )

        self.assertEqual(snapshot["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(snapshot["record_type"], "CandidateIndexSnapshot")
        self.assertEqual(snapshot["index_type"], "minhash_lsh")
        self.assertEqual(snapshot["seed"], 42)
        self.assertEqual(snapshot["features"], ["code", "resource"])
        self.assertEqual(snapshot["corpus_size"], 2)

    def test_build_candidate_selection_record_wraps_screening_row(self) -> None:
        builder = getattr(v3_4_contracts, "build_candidate_selection_record", None)
        self.assertIsNotNone(builder, "build_candidate_selection_record must exist")

        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "screening_status": "preliminary_positive",
            "retrieval_score": 0.42,
            "retrieval_rank": 1,
            "retrieval_features_used": ["code"],
            "screening_cost_ms": 7,
            "screening_warnings": [],
            "evidence": [
                {
                    "source_stage": "screening",
                    "signal_type": "layer_score",
                    "magnitude": 0.42,
                    "ref": "code",
                }
            ],
        }

        record = builder(row)

        self.assertEqual(record["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(record["record_type"], "CandidateSelectionRecord")
        self.assertEqual(record["query_app_id"], "APP-A")
        self.assertEqual(record["candidate_app_id"], "APP-B")
        self.assertEqual(record["retrieval_score"], 0.42)
        self.assertEqual(record["retrieval_rank"], 1)
        self.assertEqual(record["budget"]["screening_cost_ms"], 7)
        self.assertEqual(record["evidence_record"]["record_type"], "PairEvidenceRecord")

    def test_screening_candidate_row_contains_v3_4_record_after_ranking(self) -> None:
        app_records = [
            {"app_id": "APP-A"},
            {"app_id": "APP-B"},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.42  # type: ignore[assignment]
            candidate_list = screening_runner.build_candidate_list(
                app_records=app_records,
                selected_layers=["code"],
                metric="jaccard",
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
        self.assertIn("candidate_selection_record", row)
        self.assertEqual(row["candidate_selection_record"]["record_type"], "CandidateSelectionRecord")
        self.assertEqual(row["candidate_selection_record"]["retrieval_rank"], 1)

    def test_screening_candidate_row_contains_noise_decision_record(self) -> None:
        app_records = [
            {
                "app_id": "APP-A",
                "noise_profile_envelope": {
                    "schema_version": "nc-v1",
                    "detector_source": "library_view_v2",
                    "confidence": "high",
                    "status": "success",
                    "noise_reason": "library_dominant",
                    "downstream_warnings": ["library_noise"],
                    "evidence_refs": ["apk_path:/tmp/a.apk"],
                    "noise_status": "noisy",
                },
            },
            {"app_id": "APP-B"},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.42  # type: ignore[assignment]
            candidate_list = screening_runner.build_candidate_list(
                app_records=app_records,
                selected_layers=["code"],
                metric="jaccard",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        finally:
            screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]

        row = candidate_list[0]
        self.assertEqual(row["noise_decision_record"]["record_type"], "NoiseDecisionRecord")
        self.assertEqual(row["noise_decision_record"]["apk_id"], "APP-A")
        self.assertEqual(
            row["candidate_selection_record"]["noise_decision_record"]["record_type"],
            "NoiseDecisionRecord",
        )

    def test_screening_candidate_row_contains_candidate_index_snapshot(self) -> None:
        app_records = [
            {
                "app_id": "APP-A",
                "layers": {"code": {"a", "b"}, "component": set(), "resource": set(), "metadata": set(), "library": set()},
            },
            {
                "app_id": "APP-B",
                "layers": {"code": {"a", "b"}, "component": set(), "resource": set(), "metadata": set(), "library": set()},
            },
        ]

        candidate_list = screening_runner.build_candidate_list(
            app_records=app_records,
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.10,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params={
                "type": "minhash_lsh",
                "num_perm": 128,
                "bands": 32,
                "seed": 42,
                "features": ["code"],
            },
        )

        self.assertEqual(len(candidate_list), 1)
        snapshot = candidate_list[0]["candidate_index_snapshot"]
        self.assertEqual(snapshot["record_type"], "CandidateIndexSnapshot")
        self.assertEqual(snapshot["index_type"], "minhash_lsh")
        self.assertEqual(snapshot["features"], ["code"])


class TestV34SearchSimilarityResult(unittest.TestCase):
    def test_build_search_similarity_result_wraps_candidate_list(self) -> None:
        builder = getattr(v3_4_contracts, "build_search_similarity_result", None)
        self.assertIsNotNone(builder, "build_search_similarity_result must exist")

        candidate_row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "screening_status": "preliminary_positive",
            "retrieval_score": 0.42,
            "retrieval_rank": 1,
            "retrieval_features_used": ["code"],
            "screening_cost_ms": 7,
            "screening_warnings": [],
        }

        result = builder(query_app_id="APP-A", candidate_list=[candidate_row])

        self.assertEqual(result["schema_version"], V3_4_SCHEMA_VERSION)
        self.assertEqual(result["record_type"], "SearchSimilarityResult")
        self.assertEqual(result["query_app_id"], "APP-A")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["record_type"], "CandidateSelectionRecord")

    def test_run_screening_search_result_returns_v3_4_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "cascade.yaml"
            config_path.write_text(
                """
stages:
  screening:
    features: [code]
    metric: jaccard
    threshold: 0.10
""".strip(),
                encoding="utf-8",
            )
            app_records = [
                {
                    "app_id": "APP-A",
                    "layers": {
                        "code": {"a", "b"},
                        "component": set(),
                        "resource": set(),
                        "metadata": set(),
                        "library": set(),
                    },
                },
                {
                    "app_id": "APP-B",
                    "layers": {
                        "code": {"a", "b"},
                        "component": set(),
                        "resource": set(),
                        "metadata": set(),
                        "library": set(),
                    },
                },
            ]

            previous = os.environ.get("SIMILARITY_SKIP_REQ_CHECK")
            os.environ["SIMILARITY_SKIP_REQ_CHECK"] = "1"
            try:
                runner = getattr(screening_runner, "run_screening_search_result", None)
                self.assertIsNotNone(runner, "run_screening_search_result must exist")
                result = runner(
                    cascade_config_path=config_path,
                    app_records=app_records,
                    query_app_id="APP-A",
                )
            finally:
                if previous is None:
                    os.environ.pop("SIMILARITY_SKIP_REQ_CHECK", None)
                else:
                    os.environ["SIMILARITY_SKIP_REQ_CHECK"] = previous

        self.assertEqual(result["record_type"], "SearchSimilarityResult")
        self.assertEqual(result["query_app_id"], "APP-A")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["record_type"], "CandidateSelectionRecord")


if __name__ == "__main__":
    unittest.main()

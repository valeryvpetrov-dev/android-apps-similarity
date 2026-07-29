#!/usr/bin/env python3
"""Contract tests for the runtime similarity profile."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import feature_extractors
import m_static_views
import pairwise_runner


EXPECTED_LIGHT_VIEWS = (
    "code",
    "component",
    "resource",
    "metadata",
    "library",
)
EXPECTED_LIGHT_VIEW_SCHEMAS = {
    view: "zip-light-{}-v1".format(view) for view in EXPECTED_LIGHT_VIEWS
}
EXPECTED_LAYERS = (
    "code",
    "component",
    "resource",
    "metadata",
    "library",
    "api",
    "code_v4",
    "code_v4_shingled",
    "resource_v2",
)
EXPECTED_PUBLIC_PAIR_CHECK_IDS = frozenset(
    {
        "set_based_multiview_similarity",
        "apk_signature_match",
        "semantic_multiview_similarity",
    }
)
EXPECTED_CONDITIONAL_PAIR_CHECK_IDS = frozenset(
    {"semantic_multiview_similarity"}
)
EXPECTED_GUARDED_SCORE_POLICY_IDS = frozenset(
    {
        "R_code_stats_containment_corroboration_policy_v1",
        "R_code_stats_added_code_corroboration_policy_v1",
        "R_code_stats_resource_change_identity_policy_v1",
        "R_code_stats_repack_core_policy_v1",
        "R_code_stats_payload_resource_support_policy_v1",
        "R_code_stats_payload_resource_bridge_policy_v1",
        "score_conflict_guard_zero_code_fingerprint_v1",
        "R_semantic_multiview_score_promotion_policy_v1",
        "R_c05_static_manifest_relation_high_score_policy_v1",
    }
)
EXPECTED_SIMILARITY_SCORE_SOURCES = frozenset(
    {
        "similarity_score",
        "library_reduced_score",
        "full_similarity_score",
        "code_conflict_guarded_library_reduced_score",
        "code_stats_containment_with_resource_corroboration",
        "code_stats_added_code_with_resource_support",
        "code_stats_resource_change_tolerant_code_identity",
        "code_stats_repack_core_with_bounded_code_delta",
        "code_stats_payload_resource_support",
        "code_stats_payload_resource_bridge",
        "semantic_multiview_high_same_resources_code_stats_match",
        "c05_static_manifest_relation_high_score",
    }
)
EXPECTED_EVIDENCE_ONLY_POLICY_IDS = frozenset(
    {
        "R_framework_shift_anchor_evidence_policy_v1",
        "R_c05_static_evidence_policy_v1",
        "R_code_core_evidence_policy_v1",
        "R_added_code_direct_evidence_policy_v1",
        "R_deleted_code_direct_evidence_policy_v1",
        "R_library_noise_direct_evidence_policy_v1",
        "R_obfuscation_direct_evidence_policy_v1",
        "R_apk_packaging_evidence_policy_v1",
        "R_packaging_package_rename_evidence_v1",
        "R_packaging_apk_layout_evidence_v1",
    }
)


def _success_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "app_a": "A",
        "app_b": "B",
        "status": "success",
        "similarity_score": 0.83,
        "similarity_score_source": "library_reduced_score",
        "full_similarity_score": 0.91,
        "library_reduced_score": 0.83,
        "views_used": ["code", "component", "resource"],
        "signature_match": {"score": 1.0, "status": "match"},
        "evidence": [],
    }
    row.update(overrides)
    return row


def test_pairwise_runtime_registries_are_complete_and_immutable() -> None:
    assert (
        getattr(pairwise_runner, "PUBLIC_PAIR_CHECK_IDS", None)
        == EXPECTED_PUBLIC_PAIR_CHECK_IDS
    )
    assert isinstance(pairwise_runner.PUBLIC_PAIR_CHECK_IDS, frozenset)
    assert (
        getattr(pairwise_runner, "CONDITIONAL_PAIR_CHECK_IDS", None)
        == EXPECTED_CONDITIONAL_PAIR_CHECK_IDS
    )
    assert isinstance(pairwise_runner.CONDITIONAL_PAIR_CHECK_IDS, frozenset)
    assert (
        getattr(pairwise_runner, "GUARDED_SCORE_POLICY_IDS", None)
        == EXPECTED_GUARDED_SCORE_POLICY_IDS
    )
    assert isinstance(pairwise_runner.GUARDED_SCORE_POLICY_IDS, frozenset)
    assert (
        getattr(pairwise_runner, "SIMILARITY_SCORE_SOURCES", None)
        == EXPECTED_SIMILARITY_SCORE_SOURCES
    )
    assert isinstance(pairwise_runner.SIMILARITY_SCORE_SOURCES, frozenset)
    assert (
        getattr(pairwise_runner, "EVIDENCE_ONLY_POLICY_IDS", None)
        == EXPECTED_EVIDENCE_ONLY_POLICY_IDS
    )
    assert isinstance(pairwise_runner.EVIDENCE_ONLY_POLICY_IDS, frozenset)


def test_runtime_profile_manifest_matches_current_runtime() -> None:
    module_spec = importlib.util.find_spec("runtime_profile_contract")
    assert module_spec is not None
    runtime_profile_contract = importlib.import_module("runtime_profile_contract")
    build_manifest = getattr(
        runtime_profile_contract,
        "build_runtime_profile_manifest",
        None,
    )
    assert build_manifest is not None

    manifest = build_manifest()

    assert manifest == {
        "light_views": list(EXPECTED_LIGHT_VIEWS),
        "light_view_schema_versions": EXPECTED_LIGHT_VIEW_SCHEMAS,
        "active_measures": ["jaccard"],
        "default_layers": list(EXPECTED_LAYERS),
        "available_layers": list(EXPECTED_LAYERS),
        "public_pair_check_ids": sorted(EXPECTED_PUBLIC_PAIR_CHECK_IDS),
        "conditional_pair_check_ids": sorted(
            EXPECTED_CONDITIONAL_PAIR_CHECK_IDS
        ),
        "guarded_score_policy_ids": sorted(
            EXPECTED_GUARDED_SCORE_POLICY_IDS
        ),
        "similarity_score_sources": sorted(
            EXPECTED_SIMILARITY_SCORE_SOURCES
        ),
        "evidence_only_policy_ids": sorted(
            EXPECTED_EVIDENCE_ONLY_POLICY_IDS
        ),
        "aggregation_policy": {
            "policy_id": "deep_m2_score_decision_policy_v1",
            "strategy": "select_content_similarity_score_with_evidence_guards",
            "selected_score_field": "similarity_score",
            "limitations": [
                "full_similarity_score_is_diagnostic_not_final_verdict",
                "library_reduced_score_requires_real_computation",
                "packaging_and_signature_are_evidence_only",
                "analysis_failed_similarity_is_undefined_not_zero",
            ],
        },
    }
    assert manifest["light_views"] == list(
        feature_extractors.ZIP_LIGHT_SUPPORTED_VIEWS
    )
    assert manifest["default_layers"] == list(m_static_views.ALL_LAYERS)
    assert manifest["available_layers"] == list(m_static_views.ALL_LAYERS)


def test_registered_explicit_score_sources_survive_detailed_score_building() -> None:
    sources = getattr(pairwise_runner, "SIMILARITY_SCORE_SOURCES", frozenset())
    assert sources == EXPECTED_SIMILARITY_SCORE_SOURCES

    for source in sources:
        scores = pairwise_runner.build_detailed_scores(
            _success_row(similarity_score_source=source),
            analysis_status="success",
        )
        assert scores["similarity_score"] == 0.83
        assert scores["selected_similarity_score"] == 0.83
        assert scores["similarity_score_source"] == source
        assert scores["failure_similarity_semantics"] is None


def test_unregistered_explicit_score_source_fails_closed_in_detailed_scores() -> None:
    scores = pairwise_runner.build_detailed_scores(
        _success_row(similarity_score_source="unregistered_test_source"),
        analysis_status="success",
    )

    assert scores["similarity_score"] is None
    assert scores["full_similarity_score"] is None
    assert scores["library_reduced_score"] is None
    assert scores["selected_similarity_score"] is None
    assert scores["similarity_score_source"] == "analysis_failed"
    assert scores["library_reduced_status"] == "not_applicable"
    assert scores["failure_similarity_semantics"] == "undefined_not_zero"
    assert scores["analysis_failed_reason"] == "unregistered_score_source"


def test_unregistered_explicit_score_source_fails_closed_in_detailed_json() -> None:
    item = pairwise_runner._build_detailed_json_item(
        _success_row(similarity_score_source="unregistered_test_source"),
        index=0,
    )

    assert item["status"] == "analysis_failed"
    assert item["analysis_failed_reason"] == "unregistered_score_source"
    assert item["similarity_score"] is None
    assert item["full_similarity_score"] is None
    assert item["library_reduced_score"] is None
    assert item["selected_similarity_score"] is None
    assert item["similarity_score_source"] == "analysis_failed"
    assert item["failure_similarity_semantics"] == "undefined_not_zero"
    content_check = next(
        check
        for check in item["pair_check_runs"]
        if check["check_id"] == "set_based_multiview_similarity"
    )
    assert content_check["status"] == "analysis_failed"
    assert content_check["outputs"]["similarity_score"] is None


def test_public_pair_checks_match_runtime_and_semantic_is_conditional() -> None:
    regular_item = pairwise_runner._build_detailed_json_item(_success_row(), index=0)
    assert {
        check["check_id"] for check in regular_item["pair_check_runs"]
    } == {
        "set_based_multiview_similarity",
        "apk_signature_match",
    }

    semantic_item = pairwise_runner._build_detailed_json_item(
        _success_row(
            semantic_multiview={
                "status": "success",
                "semantic_score": 0.78,
                "scores": {"R_code_stats": 0.8},
            }
        ),
        index=1,
    )
    assert {
        check["check_id"] for check in semantic_item["pair_check_runs"]
    } == EXPECTED_PUBLIC_PAIR_CHECK_IDS

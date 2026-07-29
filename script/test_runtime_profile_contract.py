#!/usr/bin/env python3
"""Contract tests for the runtime similarity profile."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


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


def _assert_analysis_failed_score_fields(
    record: dict[str, object],
    reason: str = "unregistered_score_source",
) -> None:
    assert record["similarity_score"] is None
    assert record["full_similarity_score"] is None
    assert record["library_reduced_score"] is None
    assert record["selected_similarity_score"] is None
    assert record["similarity_score_source"] == "analysis_failed"
    assert record["library_reduced_status"] == "not_applicable"
    assert record["failure_similarity_semantics"] == "undefined_not_zero"
    assert record["analysis_failed_reason"] == reason


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


def test_runtime_profile_contract_uses_package_context_for_imports() -> None:
    direct_module = importlib.import_module("runtime_profile_contract")
    package_module = importlib.import_module("script.runtime_profile_contract")

    assert direct_module.feature_extractors.__name__ == "feature_extractors"
    assert direct_module.m_static_views.__name__ == "m_static_views"
    assert direct_module.pairwise_runner.__name__ == "pairwise_runner"
    assert package_module.feature_extractors.__name__ == "script.feature_extractors"
    assert package_module.m_static_views.__name__ == "script.m_static_views"
    assert package_module.pairwise_runner.__name__ == "script.pairwise_runner"


def test_runtime_profile_contract_propagates_internal_import_errors() -> None:
    runtime_profile_contract = importlib.import_module("runtime_profile_contract")
    load_runtime_modules = getattr(
        runtime_profile_contract,
        "_load_runtime_modules",
        None,
    )
    assert load_runtime_modules is not None

    def raise_internal_error(_module_name: str) -> object:
        raise RuntimeError("internal import failure")

    with pytest.raises(RuntimeError, match="internal import failure"):
        load_runtime_modules(
            "script",
            module_importer=raise_internal_error,
        )


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

        item = pairwise_runner._build_detailed_json_item(
            _success_row(similarity_score_source=source),
            index=0,
        )
        assert item["status"] == "success"
        assert item["similarity_score"] == 0.83
        assert item["selected_similarity_score"] == 0.83
        assert item["similarity_score_source"] == source
        content_check = next(
            check
            for check in item["pair_check_runs"]
            if check["check_id"] == "set_based_multiview_similarity"
        )
        assert content_check["outputs"]["similarity_score_source"] == source


def test_unregistered_explicit_score_source_fails_closed_in_detailed_scores() -> None:
    scores = pairwise_runner.build_detailed_scores(
        _success_row(similarity_score_source="unregistered_test_source"),
        analysis_status="success",
    )

    _assert_analysis_failed_score_fields(scores)


def test_unregistered_explicit_score_source_fails_closed_in_detailed_json() -> None:
    item = pairwise_runner._build_detailed_json_item(
        _success_row(similarity_score_source="unregistered_test_source"),
        index=0,
    )

    assert item["status"] == "analysis_failed"
    assert item["analysis_failed_reason"] == "unregistered_score_source"
    _assert_analysis_failed_score_fields(item)
    content_check = next(
        check
        for check in item["pair_check_runs"]
        if check["check_id"] == "set_based_multiview_similarity"
    )
    assert content_check["status"] == "analysis_failed"
    assert content_check["outputs"]["similarity_score"] is None


def test_unregistered_source_without_explicit_score_fails_before_fallback() -> None:
    row = _success_row(
        similarity_score=None,
        similarity_score_source="unregistered_test_source",
    )

    scores = pairwise_runner.build_detailed_scores(
        row,
        analysis_status="success",
    )
    _assert_analysis_failed_score_fields(scores)

    item = pairwise_runner._build_detailed_json_item(row, index=0)
    assert item["status"] == "analysis_failed"
    _assert_analysis_failed_score_fields(item)
    assert item["pair_similarity_result"]["status"] == "analysis_failed"
    assert item["pair_similarity_result"]["scores"]["similarity_score"] is None


def test_unregistered_nested_source_fails_closed_and_sanitizes_nested_scores() -> None:
    row = _success_row(similarity_score=None)
    row.pop("similarity_score_source")
    row["scores"] = {
        "similarity_score": None,
        "similarity_score_source": "unregistered_nested_source",
        "full_similarity_score": 0.92,
        "library_reduced_score": 0.81,
        "selected_similarity_score": 0.81,
    }

    scores = pairwise_runner.build_detailed_scores(
        row,
        analysis_status="success",
    )
    _assert_analysis_failed_score_fields(scores)

    item = pairwise_runner._build_detailed_json_item(row, index=0)
    assert item["status"] == "analysis_failed"
    _assert_analysis_failed_score_fields(item)
    _assert_analysis_failed_score_fields(item["scores"])
    assert item["pair_similarity_result"]["status"] == "analysis_failed"
    assert item["pair_similarity_result"]["scores"]["similarity_score"] is None


def test_pair_check_id_validation_rejects_unregistered_and_unbacked_conditional() -> None:
    with pytest.raises(ValueError, match="unregistered_pair_check_id"):
        pairwise_runner._validated_pair_check_id("unregistered_pair_check")

    with pytest.raises(ValueError, match="conditional_pair_check_without_result"):
        pairwise_runner._validated_pair_check_id(
            "semantic_multiview_similarity"
        )

    assert (
        pairwise_runner._validated_pair_check_id(
            "semantic_multiview_similarity",
            semantic_result={"status": "success"},
        )
        == "semantic_multiview_similarity"
    )


@pytest.mark.parametrize(
    "aggregation_policy",
    [
        {
            "policy_id": "unregistered_policy",
            "strategy": "select_content_similarity_score_with_evidence_guards",
            "selected_score_field": "similarity_score",
        },
        {
            "policy_id": "deep_m2_score_decision_policy_v1",
            "strategy": "unregistered_strategy",
            "selected_score_field": "similarity_score",
        },
        {
            "policy_id": "deep_m2_score_decision_policy_v1",
            "strategy": "select_content_similarity_score_with_evidence_guards",
            "selected_score_field": "malicious_score",
        },
    ],
)
def test_unregistered_aggregation_policy_cannot_diverge_nested_score(
    aggregation_policy: dict[str, object],
) -> None:
    item = pairwise_runner._build_detailed_json_item(
        _success_row(
            aggregation_policy=aggregation_policy,
            malicious_score=0.99,
        ),
        index=0,
    )

    assert item["status"] == "analysis_failed"
    _assert_analysis_failed_score_fields(
        item,
        reason="unregistered_aggregation_policy",
    )
    assert item["aggregation_policy"]["policy_id"] == (
        "deep_m2_score_decision_policy_v1"
    )
    assert item["aggregation_policy"]["strategy"] == (
        "select_content_similarity_score_with_evidence_guards"
    )
    assert item["aggregation_policy"]["selected_score_field"] == (
        "similarity_score"
    )
    nested_result = item["pair_similarity_result"]
    assert nested_result["status"] == "analysis_failed"
    assert nested_result["scores"]["selected_score_field"] == (
        "similarity_score"
    )
    assert nested_result["scores"]["similarity_score"] is None
    assert nested_result["selected_similarity_score"] is None
    assert item["similarity_score"] == nested_result["scores"]["similarity_score"]
    assert nested_result["scores"]["similarity_score"] != item["malicious_score"]


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

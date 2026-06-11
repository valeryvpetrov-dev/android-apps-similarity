#!/usr/bin/env python3
"""Минимальные контракты архитектуры v3.4.

Модуль не меняет существующую логику сравнения. Он только оборачивает уже
построенный pair_row в канонические записи v3.4:

- PairEvidenceRecord;
- CompatibilityCheckRecord;
- PairSimilarityResult.
"""
from __future__ import annotations

from typing import Any


V3_4_SCHEMA_VERSION = "similarity-v3.4-minimal"

ARCHITECTURE_PROFILE = "ArchitectureProfile"
REPRESENTATION_SPEC = "RepresentationSpec"
VIEW_REGISTRY = "ViewRegistry"
VIEW_TYPE_FIELD_POLICY = "ViewTypeFieldPolicy"
MEASURE_REGISTRY = "MeasureRegistry"
NOISE_POLICY_REGISTRY = "NoisePolicyRegistry"
CORPUS_MANIFEST = "CorpusManifest"
APK_ANALYSIS_RECORD = "ApkAnalysisRecord"
VIEW_ARTIFACT_RECORD = "ViewArtifactRecord"
PAIR_EVIDENCE_RECORD = "PairEvidenceRecord"
COMPATIBILITY_CHECK_RECORD = "CompatibilityCheckRecord"
PAIR_PRECHECK_RECORD = "PairPrecheckRecord"
PAIR_CHECK_REGISTRY = "PairCheckRegistry"
PAIR_CHECK_RUN = "PairCheckRun"
PAIR_AGGREGATION_POLICY = "PairAggregationPolicy"
PAIR_SIMILARITY_RESULT = "PairSimilarityResult"
CANDIDATE_SELECTION_RECORD = "CandidateSelectionRecord"
CANDIDATE_INDEX_SNAPSHOT = "CandidateIndexSnapshot"
NOISE_DECISION_RECORD = "NoiseDecisionRecord"
SEARCH_SIMILARITY_RESULT = "SearchSimilarityResult"
EXPLANATION_RENDER_RECORD = "ExplanationRenderRecord"
BENCHMARK_SLICE = "BenchmarkSlice"

STATUS_SUCCESS = "success"
STATUS_PARTIAL_RESULT = "partial_result"
STATUS_ANALYSIS_FAILED = "analysis_failed"
STATUS_MODEL_FAILED = "model_failed"
STATUS_COMPARISON_FAILED = "comparison_failed"
STATUS_TIMEOUT = "timeout"
STATUS_INCOMPATIBLE_INPUTS = "incompatible_inputs"
STATUS_INCOMPATIBLE_INDEX = "incompatible_index"
STATUS_NOISE_UNKNOWN = "noise_unknown"

_SUCCESS_LIKE_SOURCE_STATUSES = {
    "success",
    "success_shortcut",
    "low_similarity",
}
_KNOWN_V3_4_STATUSES = {
    STATUS_SUCCESS,
    STATUS_PARTIAL_RESULT,
    STATUS_ANALYSIS_FAILED,
    STATUS_MODEL_FAILED,
    STATUS_COMPARISON_FAILED,
    STATUS_TIMEOUT,
    STATUS_INCOMPATIBLE_INPUTS,
    STATUS_INCOMPATIBLE_INDEX,
    STATUS_NOISE_UNKNOWN,
}


def _as_non_empty_string(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return list(value)
    return []


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _base_record(record_type: str) -> dict[str, Any]:
    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": record_type,
    }


def _pair_id_from_apps(app_a: object, app_b: object) -> str:
    left = _as_non_empty_string(app_a, "APP-A-UNKNOWN")
    right = _as_non_empty_string(app_b, "APP-B-UNKNOWN")
    return "{}::{}".format(left, right)


def build_architecture_profile(
    *,
    profile_id: str,
    active_views: object,
    active_measures: object,
    active_noise_policies: object = None,
    active_pair_checks: object = None,
    aggregation_policy: object = None,
    candidate_budget: object = None,
) -> dict[str, Any]:
    """Собрать ArchitectureProfile как профиль реализации, а не ядро."""
    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": ARCHITECTURE_PROFILE,
        "profile_id": _as_non_empty_string(profile_id, "default-v3.4"),
        "active_views": _as_list(active_views),
        "active_measures": _as_list(active_measures),
        "active_noise_policies": _as_list(active_noise_policies),
        "active_pair_checks": _as_list(active_pair_checks),
        "aggregation_policy": aggregation_policy,
        "candidate_budget": candidate_budget,
    }


def build_view_registry(views: object) -> dict[str, Any]:
    record = _base_record(VIEW_REGISTRY)
    record["views"] = _as_list(views)
    return record


def build_view_type_field_policy(
    *,
    view_type: str,
    required_fields: object = None,
    optional_fields: object = None,
    forbidden_fields: object = None,
) -> dict[str, Any]:
    record = _base_record(VIEW_TYPE_FIELD_POLICY)
    record.update(
        {
            "view_type": _as_non_empty_string(view_type, "unknown_view"),
            "required_fields": _as_list(required_fields),
            "optional_fields": _as_list(optional_fields),
            "forbidden_fields": _as_list(forbidden_fields),
        }
    )
    return record


def build_measure_registry(measures: object) -> dict[str, Any]:
    record = _base_record(MEASURE_REGISTRY)
    record["measures"] = _as_list(measures)
    return record


def build_noise_policy_registry(noise_policies: object) -> dict[str, Any]:
    record = _base_record(NOISE_POLICY_REGISTRY)
    record["noise_policies"] = _as_list(noise_policies)
    return record


def build_representation_spec(
    *,
    profile_id: str,
    views: object,
    measures: object,
    noise_policies: object = None,
) -> dict[str, Any]:
    """Собрать RepresentationSpec: связь представлений, мер и шумовых политик."""
    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": REPRESENTATION_SPEC,
        "profile_id": _as_non_empty_string(profile_id, "default-v3.4"),
        "view_registry": build_view_registry(views),
        "measure_registry": build_measure_registry(measures),
        "noise_policy_registry": build_noise_policy_registry(noise_policies),
    }


def build_corpus_manifest(
    *,
    corpus_id: str,
    apk_ids: object = None,
    source_refs: object = None,
) -> dict[str, Any]:
    record = _base_record(CORPUS_MANIFEST)
    record.update(
        {
            "corpus_id": _as_non_empty_string(corpus_id, "CORPUS-UNKNOWN"),
            "apk_ids": _as_list(apk_ids),
            "source_refs": _as_list(source_refs),
        }
    )
    return record


def build_apk_analysis_record(
    *,
    apk_id: str,
    status: str = STATUS_SUCCESS,
    apk_sha256: object = None,
    views: object = None,
    warnings: object = None,
) -> dict[str, Any]:
    record = _base_record(APK_ANALYSIS_RECORD)
    record.update(
        {
            "apk_id": _as_non_empty_string(apk_id, "APP-UNKNOWN"),
            "status": status,
            "apk_sha256": apk_sha256,
            "views": _as_list(views),
            "warnings": _as_list(warnings),
        }
    )
    return record


def build_view_artifact_record(
    *,
    apk_id: str,
    view_type: str,
    artifact_ref: object = None,
    status: str = STATUS_SUCCESS,
    view_schema_version: object = None,
) -> dict[str, Any]:
    record = _base_record(VIEW_ARTIFACT_RECORD)
    record.update(
        {
            "apk_id": _as_non_empty_string(apk_id, "APP-UNKNOWN"),
            "view_type": _as_non_empty_string(view_type, "unknown_view"),
            "artifact_ref": artifact_ref,
            "status": status,
            "view_schema_version": view_schema_version,
        }
    )
    return record


def build_pair_precheck_record(
    *,
    pair_id: str,
    status: str = STATUS_SUCCESS,
    checks: object = None,
    warnings: object = None,
) -> dict[str, Any]:
    record = _base_record(PAIR_PRECHECK_RECORD)
    record.update(
        {
            "pair_id": _as_non_empty_string(pair_id, "PAIR-UNKNOWN"),
            "status": status,
            "checks": _as_dict(checks),
            "warnings": _as_list(warnings),
        }
    )
    return record


def build_pair_check_registry(pair_checks: object) -> dict[str, Any]:
    record = _base_record(PAIR_CHECK_REGISTRY)
    record["pair_checks"] = _as_list(pair_checks)
    return record


def build_pair_check_run(
    *,
    pair_id: str,
    check_id: str,
    status: str = STATUS_SUCCESS,
    duration_ms: object = None,
    inputs: object = None,
    outputs: object = None,
) -> dict[str, Any]:
    record = _base_record(PAIR_CHECK_RUN)
    record.update(
        {
            "pair_id": _as_non_empty_string(pair_id, "PAIR-UNKNOWN"),
            "check_id": _as_non_empty_string(check_id, "pair_check"),
            "status": status,
            "duration_ms": duration_ms,
            "inputs": _as_dict(inputs),
            "outputs": _as_dict(outputs),
        }
    )
    return record


def build_pair_aggregation_policy(
    *,
    policy_id: str,
    strategy: str,
    weights: object = None,
) -> dict[str, Any]:
    record = _base_record(PAIR_AGGREGATION_POLICY)
    record.update(
        {
            "policy_id": _as_non_empty_string(policy_id, "default_pair_aggregation"),
            "strategy": _as_non_empty_string(strategy, "single_score"),
            "weights": _as_dict(weights),
        }
    )
    return record


def _status_from_pair_row(pair_row: dict[str, Any]) -> str:
    source_status = pair_row.get("status")
    if pair_row.get("timeout_info") is not None:
        return STATUS_TIMEOUT
    if source_status == "partial":
        return STATUS_PARTIAL_RESULT
    if source_status in _SUCCESS_LIKE_SOURCE_STATUSES:
        return STATUS_SUCCESS
    if source_status in _KNOWN_V3_4_STATUSES:
        return str(source_status)
    if source_status == "analysis_failed":
        return STATUS_ANALYSIS_FAILED
    if source_status is None:
        return STATUS_PARTIAL_RESULT
    return STATUS_COMPARISON_FAILED


def _selected_similarity_score(pair_row: dict[str, Any]) -> object:
    reduced = pair_row.get("library_reduced_score")
    if reduced is not None:
        return reduced
    return pair_row.get("full_similarity_score")


def build_pair_evidence_record(
    *,
    pair_id: str,
    evidence: object,
    status: str | None = None,
    source_stage: str = "pairwise",
) -> dict[str, Any]:
    """Собрать PairEvidenceRecord из текущего evidence[]."""
    evidence_items = _as_list(evidence)
    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": PAIR_EVIDENCE_RECORD,
        "pair_id": _as_non_empty_string(pair_id, "PAIR-UNKNOWN"),
        "status": status or STATUS_SUCCESS,
        "source_stage": source_stage,
        "evidence_count": len(evidence_items),
        "evidence": evidence_items,
    }


def build_compatibility_check_record(
    *,
    pair_id: str,
    app_a: object = None,
    app_b: object = None,
    profile_ref: object = None,
    index_ref: object = None,
    warnings: object = None,
    explicit_status: object = None,
) -> dict[str, Any]:
    """Собрать CompatibilityCheckRecord для пары или результата поиска."""
    reasons: list[str] = []
    normalized_status = None
    if isinstance(explicit_status, str) and explicit_status.strip():
        normalized_status = explicit_status.strip()

    if normalized_status in {STATUS_INCOMPATIBLE_INPUTS, STATUS_INCOMPATIBLE_INDEX}:
        reasons.append(normalized_status)

    if not app_a:
        reasons.append("missing_app_a")
    if not app_b:
        reasons.append("missing_app_b")

    if reasons:
        status = STATUS_INCOMPATIBLE_INDEX if STATUS_INCOMPATIBLE_INDEX in reasons else STATUS_INCOMPATIBLE_INPUTS
    else:
        status = "compatible"

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": COMPATIBILITY_CHECK_RECORD,
        "pair_id": _as_non_empty_string(pair_id, "PAIR-UNKNOWN"),
        "status": status,
        "profile_ref": profile_ref,
        "index_ref": index_ref,
        "warnings": _as_list(warnings),
        "reasons": reasons,
    }


def build_pair_similarity_result(
    pair_row: dict[str, Any],
    *,
    pair_id: str | None = None,
    profile_ref: object = None,
    index_ref: object = None,
) -> dict[str, Any]:
    """Собрать PairSimilarityResult v3.4 поверх существующего pair_row."""
    if not isinstance(pair_row, dict):
        raise TypeError("build_pair_similarity_result expects dict pair_row")

    resolved_pair_id = _as_non_empty_string(pair_id or pair_row.get("pair_id"), "PAIR-UNKNOWN")
    source_status = pair_row.get("status")
    result_status = _status_from_pair_row(pair_row)
    evidence_record = build_pair_evidence_record(
        pair_id=resolved_pair_id,
        evidence=pair_row.get("evidence"),
        status=result_status,
        source_stage="pairwise",
    )
    compatibility = build_compatibility_check_record(
        pair_id=resolved_pair_id,
        app_a=pair_row.get("app_a"),
        app_b=pair_row.get("app_b"),
        profile_ref=profile_ref or pair_row.get("profile_ref"),
        index_ref=index_ref or pair_row.get("index_ref"),
        warnings=pair_row.get("warnings") or pair_row.get("screening_warnings"),
        explicit_status=source_status,
    )

    limitations: list[str] = []
    failure_reason = pair_row.get("analysis_failed_reason")
    if failure_reason:
        limitations.append(str(failure_reason))
    if pair_row.get("timeout_info") is not None:
        limitations.append("timeout_info_present")
    if compatibility["status"] != "compatible":
        limitations.extend(str(reason) for reason in compatibility["reasons"])

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": PAIR_SIMILARITY_RESULT,
        "pair_id": resolved_pair_id,
        "status": result_status,
        "source_status": source_status,
        "apps": {
            "app_a": pair_row.get("app_a"),
            "app_b": pair_row.get("app_b"),
        },
        "scores": {
            "similarity_score": _selected_similarity_score(pair_row),
            "full_similarity_score": pair_row.get("full_similarity_score"),
            "library_reduced_score": pair_row.get("library_reduced_score"),
        },
        "views_used": _as_list(pair_row.get("views_used")),
        "signature_match": pair_row.get("signature_match"),
        "evidence_record": evidence_record,
        "compatibility_check": compatibility,
        "limitations": limitations,
    }


def build_candidate_selection_record(candidate_row: dict[str, Any]) -> dict[str, Any]:
    """Собрать CandidateSelectionRecord поверх строки первичного отбора."""
    if not isinstance(candidate_row, dict):
        raise TypeError("build_candidate_selection_record expects dict candidate_row")

    query_app_id = _as_non_empty_string(candidate_row.get("query_app_id"), "APP-QUERY-UNKNOWN")
    candidate_app_id = _as_non_empty_string(
        candidate_row.get("candidate_app_id"), "APP-CANDIDATE-UNKNOWN"
    )
    pair_id = _pair_id_from_apps(query_app_id, candidate_app_id)
    evidence_record = build_pair_evidence_record(
        pair_id=pair_id,
        evidence=candidate_row.get("evidence"),
        status=STATUS_SUCCESS,
        source_stage="screening",
    )

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": CANDIDATE_SELECTION_RECORD,
        "pair_id": pair_id,
        "status": STATUS_SUCCESS,
        "query_app_id": query_app_id,
        "candidate_app_id": candidate_app_id,
        "screening_status": candidate_row.get("screening_status"),
        "retrieval_score": candidate_row.get("retrieval_score"),
        "retrieval_rank": candidate_row.get("retrieval_rank"),
        "retrieval_features_used": _as_list(candidate_row.get("retrieval_features_used")),
        "budget": {
            "screening_cost_ms": candidate_row.get("screening_cost_ms"),
        },
        "candidate_index_snapshot": candidate_row.get("candidate_index_snapshot"),
        "noise_decision_record": candidate_row.get("noise_decision_record"),
        "warnings": _as_list(candidate_row.get("screening_warnings")),
        "evidence_record": evidence_record,
    }


def build_noise_decision_record(
    *,
    apk_id: str,
    envelope: object,
) -> dict[str, Any] | None:
    """Собрать NoiseDecisionRecord из NoiseProfileEnvelope-like dict."""
    if not isinstance(envelope, dict):
        return None

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": NOISE_DECISION_RECORD,
        "apk_id": _as_non_empty_string(apk_id, "APP-UNKNOWN"),
        "source_schema_version": envelope.get("schema_version"),
        "detector_source": envelope.get("detector_source"),
        "confidence": envelope.get("confidence"),
        "status": envelope.get("status"),
        "noise_status": envelope.get("noise_status"),
        "noise_reason": envelope.get("noise_reason"),
        "warnings": _as_list(envelope.get("downstream_warnings")),
        "evidence_refs": _as_list(envelope.get("evidence_refs")),
    }


def build_candidate_index_snapshot(
    *,
    candidate_index_params: object,
    corpus_size: int | None = None,
) -> dict[str, Any] | None:
    """Собрать CandidateIndexSnapshot из параметров быстрого индекса."""
    if not isinstance(candidate_index_params, dict):
        return None

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": CANDIDATE_INDEX_SNAPSHOT,
        "index_type": candidate_index_params.get("type"),
        "num_perm": candidate_index_params.get("num_perm"),
        "bands": candidate_index_params.get("bands"),
        "seed": candidate_index_params.get("seed"),
        "features": _as_list(candidate_index_params.get("features")),
        "corpus_size": corpus_size,
    }


def build_search_similarity_result(
    *,
    query_app_id: str,
    candidate_list: object,
    pair_results: object = None,
    status: str = STATUS_SUCCESS,
) -> dict[str, Any]:
    """Собрать SearchSimilarityResult из списка кандидатов и результатов пар."""
    rows = _as_list(candidate_list)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        existing = row.get("candidate_selection_record")
        if isinstance(existing, dict):
            candidates.append(existing)
        else:
            candidates.append(build_candidate_selection_record(row))

    return {
        "schema_version": V3_4_SCHEMA_VERSION,
        "record_type": SEARCH_SIMILARITY_RESULT,
        "query_app_id": _as_non_empty_string(query_app_id, "APP-QUERY-UNKNOWN"),
        "status": status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "pair_results": _as_list(pair_results),
        "limitations": [],
    }


def build_explanation_render_record(
    *,
    pair_id: str,
    evidence_record: object = None,
    text: object = None,
    status: str = STATUS_SUCCESS,
) -> dict[str, Any]:
    """Зафиксировать текст объяснения как отображение фактов, а не факт."""
    record = _base_record(EXPLANATION_RENDER_RECORD)
    record.update(
        {
            "pair_id": _as_non_empty_string(pair_id, "PAIR-UNKNOWN"),
            "status": status,
            "evidence_record": evidence_record if isinstance(evidence_record, dict) else None,
            "rendered_text": text,
        }
    )
    return record


def build_benchmark_slice(
    *,
    slice_id: str,
    dataset_ref: object = None,
    metrics: object = None,
    limitations: object = None,
) -> dict[str, Any]:
    """Зафиксировать внешний срез оценки качества, не рабочий вход системы."""
    record = _base_record(BENCHMARK_SLICE)
    record.update(
        {
            "slice_id": _as_non_empty_string(slice_id, "BENCHMARK-UNKNOWN"),
            "dataset_ref": dataset_ref,
            "metrics": _as_dict(metrics),
            "limitations": _as_list(limitations),
        }
    )
    return record

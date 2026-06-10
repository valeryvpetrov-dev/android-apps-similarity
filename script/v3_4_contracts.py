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

PAIR_EVIDENCE_RECORD = "PairEvidenceRecord"
COMPATIBILITY_CHECK_RECORD = "CompatibilityCheckRecord"
PAIR_SIMILARITY_RESULT = "PairSimilarityResult"

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

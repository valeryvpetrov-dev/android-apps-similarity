#!/usr/bin/env python3
"""Evidence-only direct deleted-code method delta."""
from __future__ import annotations

from collections import Counter
from typing import Any


DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID = "R_deleted_code_direct_evidence_policy_v1"
DELETED_CODE_DIRECT_EVIDENCE_REF = "R_deleted_code_method_delta"
DELETED_CODE_DIRECT_EVIDENCE_ROLE = "evidence_only"
DELETED_CODE_DIRECT_SCORE_EFFECT = "none"
DELETED_CODE_DIRECT_SAMPLE_LIMIT = 20
DELETED_CODE_DIRECT_PREFIX_LIMIT = 5


def _method_fingerprints(layers: dict[str, Any]) -> dict[str, str]:
    raw = layers.get("code_method_fingerprints")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if key and value}


def _method_prefix(method_id: str, segments: int = 4) -> str:
    class_name = method_id.split(";->", 1)[0].lstrip("L")
    parts = [part for part in class_name.split("/") if part]
    if not parts:
        return ""
    return ".".join(parts[:segments])


def _top_prefixes(method_ids: list[str]) -> list[dict[str, Any]]:
    counts = Counter(
        prefix
        for prefix in (_method_prefix(method_id) for method_id in method_ids)
        if prefix
    )
    return [
        {"prefix": prefix, "count": count}
        for prefix, count in counts.most_common(DELETED_CODE_DIRECT_PREFIX_LIMIT)
    ]


def _deleted_fingerprint_count(left: dict[str, str], right: dict[str, str]) -> int:
    left_counts = Counter(left.values())
    right_counts = Counter(right.values())
    return sum(
        max(0, count - right_counts.get(fingerprint, 0))
        for fingerprint, count in left_counts.items()
    )


def default_deleted_code_direct_evidence_fields(
    error: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "deleted_code_direct_evidence_policy_id": (
            DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID
        ),
        "deleted_code_direct_evidence_ref": DELETED_CODE_DIRECT_EVIDENCE_REF,
        "deleted_code_direct_evidence_role": DELETED_CODE_DIRECT_EVIDENCE_ROLE,
        "deleted_code_direct_score_effect": DELETED_CODE_DIRECT_SCORE_EFFECT,
        "deleted_code_direct_score_included": False,
        "deleted_code_direct_evidence_applied": False,
        "deleted_code_direct_evidence_score": 0.0,
        "deleted_code_direct_left_method_count": 0,
        "deleted_code_direct_right_method_count": 0,
        "deleted_code_direct_common_method_id_count": 0,
        "deleted_code_direct_left_only_method_count": 0,
        "deleted_code_direct_right_only_method_count": 0,
        "deleted_code_direct_deleted_fingerprint_count": 0,
        "deleted_code_direct_left_only_method_ratio": 0.0,
        "deleted_code_direct_top_method_prefixes": [],
        "deleted_code_direct_method_sample": [],
        "deleted_code_direct_representation": "method_id_delta",
    }
    if error is not None:
        fields["deleted_code_direct_evidence_error"] = error
    return fields


def build_deleted_code_direct_evidence_fields(
    layers_a: dict[str, Any],
    layers_b: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only fields for methods present only in the left APK."""
    if "code" not in set(selected_layers):
        return default_deleted_code_direct_evidence_fields(
            error="code_layer_not_selected"
        )

    left = _method_fingerprints(layers_a)
    right = _method_fingerprints(layers_b)
    left_ids = set(left)
    right_ids = set(right)
    left_only = sorted(left_ids - right_ids)
    right_only = sorted(right_ids - left_ids)
    common = left_ids & right_ids
    left_count = len(left)
    left_only_ratio = len(left_only) / left_count if left_count else 0.0
    score = left_only_ratio if left_only else 0.0

    fields = default_deleted_code_direct_evidence_fields()
    fields.update(
        {
            "deleted_code_direct_evidence_applied": bool(left_only),
            "deleted_code_direct_evidence_score": float(score),
            "deleted_code_direct_left_method_count": left_count,
            "deleted_code_direct_right_method_count": len(right),
            "deleted_code_direct_common_method_id_count": len(common),
            "deleted_code_direct_left_only_method_count": len(left_only),
            "deleted_code_direct_right_only_method_count": len(right_only),
            "deleted_code_direct_deleted_fingerprint_count": (
                _deleted_fingerprint_count(left, right)
            ),
            "deleted_code_direct_left_only_method_ratio": left_only_ratio,
            "deleted_code_direct_top_method_prefixes": _top_prefixes(left_only),
            "deleted_code_direct_method_sample": left_only[
                :DELETED_CODE_DIRECT_SAMPLE_LIMIT
            ],
        }
    )
    return fields

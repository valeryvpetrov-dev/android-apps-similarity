#!/usr/bin/env python3
"""Evidence-only direct added-code method delta."""
from __future__ import annotations

from collections import Counter
from typing import Any


ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID = "R_added_code_direct_evidence_policy_v1"
ADDED_CODE_DIRECT_EVIDENCE_REF = "R_added_code_method_delta"
ADDED_CODE_DIRECT_EVIDENCE_ROLE = "evidence_only"
ADDED_CODE_DIRECT_SCORE_EFFECT = "none"
ADDED_CODE_DIRECT_SAMPLE_LIMIT = 20
ADDED_CODE_DIRECT_PREFIX_LIMIT = 5


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
        for prefix, count in counts.most_common(ADDED_CODE_DIRECT_PREFIX_LIMIT)
    ]


def _added_fingerprint_count(left: dict[str, str], right: dict[str, str]) -> int:
    left_counts = Counter(left.values())
    right_counts = Counter(right.values())
    return sum(
        max(0, count - left_counts.get(fingerprint, 0))
        for fingerprint, count in right_counts.items()
    )


def default_added_code_direct_evidence_fields(
    error: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "added_code_direct_evidence_policy_id": (
            ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID
        ),
        "added_code_direct_evidence_ref": ADDED_CODE_DIRECT_EVIDENCE_REF,
        "added_code_direct_evidence_role": ADDED_CODE_DIRECT_EVIDENCE_ROLE,
        "added_code_direct_score_effect": ADDED_CODE_DIRECT_SCORE_EFFECT,
        "added_code_direct_score_included": False,
        "added_code_direct_evidence_applied": False,
        "added_code_direct_evidence_score": 0.0,
        "added_code_direct_left_method_count": 0,
        "added_code_direct_right_method_count": 0,
        "added_code_direct_common_method_id_count": 0,
        "added_code_direct_right_only_method_count": 0,
        "added_code_direct_left_only_method_count": 0,
        "added_code_direct_added_fingerprint_count": 0,
        "added_code_direct_right_only_method_ratio": 0.0,
        "added_code_direct_top_method_prefixes": [],
        "added_code_direct_method_sample": [],
        "added_code_direct_representation": "method_id_delta",
    }
    if error is not None:
        fields["added_code_direct_evidence_error"] = error
    return fields


def build_added_code_direct_evidence_fields(
    layers_a: dict[str, Any],
    layers_b: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only fields for methods present only in the right APK."""
    if "code" not in set(selected_layers):
        return default_added_code_direct_evidence_fields(
            error="code_layer_not_selected"
        )

    left = _method_fingerprints(layers_a)
    right = _method_fingerprints(layers_b)
    left_ids = set(left)
    right_ids = set(right)
    right_only = sorted(right_ids - left_ids)
    left_only = sorted(left_ids - right_ids)
    common = left_ids & right_ids
    right_count = len(right)
    right_only_ratio = len(right_only) / right_count if right_count else 0.0
    score = right_only_ratio if right_only else 0.0

    fields = default_added_code_direct_evidence_fields()
    fields.update(
        {
            "added_code_direct_evidence_applied": bool(right_only),
            "added_code_direct_evidence_score": float(score),
            "added_code_direct_left_method_count": len(left),
            "added_code_direct_right_method_count": right_count,
            "added_code_direct_common_method_id_count": len(common),
            "added_code_direct_right_only_method_count": len(right_only),
            "added_code_direct_left_only_method_count": len(left_only),
            "added_code_direct_added_fingerprint_count": _added_fingerprint_count(
                left,
                right,
            ),
            "added_code_direct_right_only_method_ratio": right_only_ratio,
            "added_code_direct_top_method_prefixes": _top_prefixes(right_only),
            "added_code_direct_method_sample": right_only[
                :ADDED_CODE_DIRECT_SAMPLE_LIMIT
            ],
        }
    )
    return fields

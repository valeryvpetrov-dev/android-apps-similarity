#!/usr/bin/env python3
"""Evidence-only direct library-noise namespace delta."""
from __future__ import annotations

from collections import Counter
from typing import Any


LIBRARY_NOISE_DIRECT_EVIDENCE_POLICY_ID = (
    "R_library_noise_direct_evidence_policy_v1"
)
LIBRARY_NOISE_DIRECT_EVIDENCE_REF = "R_library_noise_namespace_delta"
LIBRARY_NOISE_DIRECT_EVIDENCE_ROLE = "evidence_only"
LIBRARY_NOISE_DIRECT_SCORE_EFFECT = "none"
LIBRARY_NOISE_DIRECT_SAMPLE_LIMIT = 20
LIBRARY_NOISE_DIRECT_PREFIX_LIMIT = 5
LIBRARY_NOISE_MIN_NAMESPACE_METHODS = 2
LIBRARY_NOISE_MIN_NAMESPACE_RATIO = 0.75


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
    package_parts = parts[:-1] or parts
    return ".".join(package_parts[:segments])


def _top_prefixes(method_ids: list[str]) -> list[dict[str, Any]]:
    counts = Counter(
        prefix
        for prefix in (_method_prefix(method_id) for method_id in method_ids)
        if prefix
    )
    return [
        {"prefix": prefix, "count": count}
        for prefix, count in counts.most_common(LIBRARY_NOISE_DIRECT_PREFIX_LIMIT)
    ]


def default_library_noise_direct_evidence_fields(
    error: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "library_noise_direct_evidence_policy_id": (
            LIBRARY_NOISE_DIRECT_EVIDENCE_POLICY_ID
        ),
        "library_noise_direct_evidence_ref": LIBRARY_NOISE_DIRECT_EVIDENCE_REF,
        "library_noise_direct_evidence_role": LIBRARY_NOISE_DIRECT_EVIDENCE_ROLE,
        "library_noise_direct_score_effect": LIBRARY_NOISE_DIRECT_SCORE_EFFECT,
        "library_noise_direct_score_included": False,
        "library_noise_direct_evidence_applied": False,
        "library_noise_direct_evidence_score": 0.0,
        "library_noise_direct_left_method_count": 0,
        "library_noise_direct_right_method_count": 0,
        "library_noise_direct_common_method_id_count": 0,
        "library_noise_direct_right_only_method_count": 0,
        "library_noise_direct_left_only_method_count": 0,
        "library_noise_direct_top_namespace_prefix": "",
        "library_noise_direct_top_namespace_method_count": 0,
        "library_noise_direct_top_namespace_method_ratio": 0.0,
        "library_noise_direct_namespace_groups": [],
        "library_noise_direct_method_sample": [],
        "library_noise_direct_representation": "method_namespace_delta",
    }
    if error is not None:
        fields["library_noise_direct_evidence_error"] = error
    return fields


def build_library_noise_direct_evidence_fields(
    layers_a: dict[str, Any],
    layers_b: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only fields for a concentrated right-only namespace group."""
    if "code" not in set(selected_layers):
        return default_library_noise_direct_evidence_fields(
            error="code_layer_not_selected"
        )

    left = _method_fingerprints(layers_a)
    right = _method_fingerprints(layers_b)
    left_ids = set(left)
    right_ids = set(right)
    right_only = sorted(right_ids - left_ids)
    left_only = sorted(left_ids - right_ids)
    common = left_ids & right_ids
    namespace_groups = _top_prefixes(right_only)
    top_group = namespace_groups[0] if namespace_groups else {}
    top_prefix = str(top_group.get("prefix") or "")
    top_count = int(top_group.get("count") or 0)
    top_ratio = top_count / len(right_only) if right_only else 0.0
    applied = (
        bool(common)
        and top_count >= LIBRARY_NOISE_MIN_NAMESPACE_METHODS
        and top_ratio >= LIBRARY_NOISE_MIN_NAMESPACE_RATIO
    )

    fields = default_library_noise_direct_evidence_fields()
    fields.update(
        {
            "library_noise_direct_evidence_applied": applied,
            "library_noise_direct_evidence_score": float(top_ratio if applied else 0.0),
            "library_noise_direct_left_method_count": len(left),
            "library_noise_direct_right_method_count": len(right),
            "library_noise_direct_common_method_id_count": len(common),
            "library_noise_direct_right_only_method_count": len(right_only),
            "library_noise_direct_left_only_method_count": len(left_only),
            "library_noise_direct_top_namespace_prefix": top_prefix,
            "library_noise_direct_top_namespace_method_count": top_count,
            "library_noise_direct_top_namespace_method_ratio": top_ratio,
            "library_noise_direct_namespace_groups": namespace_groups,
            "library_noise_direct_method_sample": right_only[
                :LIBRARY_NOISE_DIRECT_SAMPLE_LIMIT
            ],
        }
    )
    return fields

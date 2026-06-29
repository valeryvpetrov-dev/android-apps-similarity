#!/usr/bin/env python3
"""Evidence-only name-independent code core signal."""
from __future__ import annotations

from collections import Counter
from typing import Any


CODE_CORE_EVIDENCE_POLICY_ID = "R_code_core_evidence_policy_v1"
CODE_CORE_EVIDENCE_REF = "R_code_core_fingerprint_multiset"
CODE_CORE_EVIDENCE_ROLE = "evidence_only"
CODE_CORE_SCORE_EFFECT = "none"
CODE_CORE_MIN_COUNTER_CONTAINMENT = 0.60
CODE_CORE_MIN_COMMON_FINGERPRINTS = 2
CODE_CORE_SAMPLE_LIMIT = 20


def _counter_containment(left: list[str], right: list[str]) -> float:
    left_counter = Counter(left)
    right_counter = Counter(right)
    denominator = min(sum(left_counter.values()), sum(right_counter.values()))
    if denominator == 0:
        return 0.0
    keys = set(left_counter) | set(right_counter)
    common = sum(min(left_counter.get(key, 0), right_counter.get(key, 0)) for key in keys)
    return common / denominator


def _counter_jaccard(left: list[str], right: list[str]) -> float:
    left_counter = Counter(left)
    right_counter = Counter(right)
    keys = set(left_counter) | set(right_counter)
    if not keys:
        return 0.0
    common = sum(min(left_counter.get(key, 0), right_counter.get(key, 0)) for key in keys)
    union = sum(max(left_counter.get(key, 0), right_counter.get(key, 0)) for key in keys)
    return common / union if union else 0.0


def _common_count(left: list[str], right: list[str]) -> int:
    left_counter = Counter(left)
    right_counter = Counter(right)
    keys = set(left_counter) | set(right_counter)
    return sum(min(left_counter.get(key, 0), right_counter.get(key, 0)) for key in keys)


def _common_sample(left: list[str], right: list[str]) -> list[str]:
    return sorted(set(left) & set(right))[:CODE_CORE_SAMPLE_LIMIT]


def _values(layers: dict[str, Any]) -> list[str]:
    raw = layers.get("code_fingerprint_values")
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(item) for item in raw if str(item)]


def default_code_core_evidence_fields(error: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "code_core_evidence_policy_id": CODE_CORE_EVIDENCE_POLICY_ID,
        "code_core_evidence_ref": CODE_CORE_EVIDENCE_REF,
        "code_core_evidence_role": CODE_CORE_EVIDENCE_ROLE,
        "code_core_score_effect": CODE_CORE_SCORE_EFFECT,
        "code_core_score_included": False,
        "code_core_evidence_applied": False,
        "code_core_evidence_score": 0.0,
        "code_core_counter_containment": 0.0,
        "code_core_counter_jaccard": 0.0,
        "code_core_common_fingerprint_count": 0,
        "left_code_core_fingerprint_count": 0,
        "right_code_core_fingerprint_count": 0,
        "code_core_common_fingerprint_sample": [],
        "code_core_min_counter_containment": CODE_CORE_MIN_COUNTER_CONTAINMENT,
        "code_core_min_common_fingerprints": CODE_CORE_MIN_COMMON_FINGERPRINTS,
        "code_core_representation": "code_fingerprint_value_counter",
    }
    if error is not None:
        fields["code_core_evidence_error"] = error
    return fields


def build_code_core_evidence_fields(
    layers_a: dict[str, Any],
    layers_b: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only code core fields from method body fingerprints."""
    if "code" not in set(selected_layers):
        return default_code_core_evidence_fields(error="code_layer_not_selected")

    left_values = _values(layers_a)
    right_values = _values(layers_b)
    containment = float(_counter_containment(left_values, right_values))
    jaccard = float(_counter_jaccard(left_values, right_values))
    common_count = int(_common_count(left_values, right_values))
    applied = (
        common_count >= CODE_CORE_MIN_COMMON_FINGERPRINTS
        and containment >= CODE_CORE_MIN_COUNTER_CONTAINMENT
    )

    fields = default_code_core_evidence_fields()
    fields.update(
        {
            "code_core_evidence_applied": bool(applied),
            "code_core_evidence_score": containment if applied else 0.0,
            "code_core_counter_containment": containment,
            "code_core_counter_jaccard": jaccard,
            "code_core_common_fingerprint_count": common_count,
            "left_code_core_fingerprint_count": len(left_values),
            "right_code_core_fingerprint_count": len(right_values),
            "code_core_common_fingerprint_sample": _common_sample(
                left_values,
                right_values,
            ),
        }
    )
    return fields

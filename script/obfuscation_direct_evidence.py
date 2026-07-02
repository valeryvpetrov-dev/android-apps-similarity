#!/usr/bin/env python3
"""Evidence-only direct obfuscation name-shortening delta."""
from __future__ import annotations

from collections import Counter
from typing import Any


OBFUSCATION_DIRECT_EVIDENCE_POLICY_ID = "R_obfuscation_direct_evidence_policy_v1"
OBFUSCATION_DIRECT_EVIDENCE_REF = "R_obfuscation_name_shortening_delta"
OBFUSCATION_DIRECT_EVIDENCE_ROLE = "evidence_only"
OBFUSCATION_DIRECT_SCORE_EFFECT = "none"
OBFUSCATION_DIRECT_SAMPLE_LIMIT = 5
OBFUSCATION_MIN_SHORT_CLASS_RATIO = 0.5
OBFUSCATION_MIN_SHORT_CLASS_RATIO_DELTA = 0.25


def _method_fingerprints(layers: dict[str, Any]) -> dict[str, str]:
    raw = layers.get("code_method_fingerprints")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if key and value}


def _method_parts(method_id: str) -> dict[str, str]:
    class_part, _, method_part = method_id.partition(";->")
    class_path = class_part.lstrip("L")
    package_parts = [part for part in class_path.split("/") if part]
    class_name = package_parts[-1] if package_parts else ""
    package_name = "/".join(package_parts[:-1])
    method_name = method_part.split("(", 1)[0]
    return {
        "package": package_name,
        "class_name": class_name,
        "method_name": method_name,
    }


def _is_short_name(value: str) -> bool:
    if value in {"", "<init>", "<clinit>"}:
        return False
    return len(value) <= 2 and value.replace("_", "a").isalnum()


def _unique_sample(
    values: list[str],
    *,
    limit: int = OBFUSCATION_DIRECT_SAMPLE_LIMIT,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _common_fingerprint_count(left: dict[str, str], right: dict[str, str]) -> int:
    return sum((Counter(left.values()) & Counter(right.values())).values())


def default_obfuscation_direct_evidence_fields(
    error: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "obfuscation_direct_evidence_policy_id": (
            OBFUSCATION_DIRECT_EVIDENCE_POLICY_ID
        ),
        "obfuscation_direct_evidence_ref": OBFUSCATION_DIRECT_EVIDENCE_REF,
        "obfuscation_direct_evidence_role": OBFUSCATION_DIRECT_EVIDENCE_ROLE,
        "obfuscation_direct_score_effect": OBFUSCATION_DIRECT_SCORE_EFFECT,
        "obfuscation_direct_score_included": False,
        "obfuscation_direct_evidence_applied": False,
        "obfuscation_direct_evidence_score": 0.0,
        "obfuscation_direct_same_package": False,
        "obfuscation_direct_left_method_count": 0,
        "obfuscation_direct_right_method_count": 0,
        "obfuscation_direct_common_method_id_count": 0,
        "obfuscation_direct_common_fingerprint_count": 0,
        "obfuscation_direct_left_short_class_name_count": 0,
        "obfuscation_direct_right_short_class_name_count": 0,
        "obfuscation_direct_left_short_method_name_count": 0,
        "obfuscation_direct_right_short_method_name_count": 0,
        "obfuscation_direct_left_short_class_name_ratio": 0.0,
        "obfuscation_direct_right_short_class_name_ratio": 0.0,
        "obfuscation_direct_left_short_method_name_ratio": 0.0,
        "obfuscation_direct_right_short_method_name_ratio": 0.0,
        "obfuscation_direct_left_package_sample": [],
        "obfuscation_direct_right_package_sample": [],
        "obfuscation_direct_left_class_name_sample": [],
        "obfuscation_direct_right_class_name_sample": [],
        "obfuscation_direct_left_method_name_sample": [],
        "obfuscation_direct_right_method_name_sample": [],
        "obfuscation_direct_representation": "name_shortening_delta",
    }
    if error is not None:
        fields["obfuscation_direct_evidence_error"] = error
    return fields


def build_obfuscation_direct_evidence_fields(
    layers_a: dict[str, Any],
    layers_b: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only fields for same-package method name shortening."""
    if "code" not in set(selected_layers):
        return default_obfuscation_direct_evidence_fields(
            error="code_layer_not_selected"
        )

    left = _method_fingerprints(layers_a)
    right = _method_fingerprints(layers_b)
    left_parts = [_method_parts(method_id) for method_id in left]
    right_parts = [_method_parts(method_id) for method_id in right]
    left_packages = sorted({part["package"] for part in left_parts if part["package"]})
    right_packages = sorted({part["package"] for part in right_parts if part["package"]})
    left_method_count = len(left)
    right_method_count = len(right)
    left_non_constructor = [
        part for part in left_parts if part["method_name"] not in {"<init>", "<clinit>"}
    ]
    right_non_constructor = [
        part for part in right_parts if part["method_name"] not in {"<init>", "<clinit>"}
    ]
    left_short_class_names = [
        part["class_name"] for part in left_parts if _is_short_name(part["class_name"])
    ]
    right_short_class_names = [
        part["class_name"] for part in right_parts if _is_short_name(part["class_name"])
    ]
    left_short_method_names = [
        part["method_name"]
        for part in left_non_constructor
        if _is_short_name(part["method_name"])
    ]
    right_short_method_names = [
        part["method_name"]
        for part in right_non_constructor
        if _is_short_name(part["method_name"])
    ]
    left_short_class_ratio = (
        len(left_short_class_names) / left_method_count if left_method_count else 0.0
    )
    right_short_class_ratio = (
        len(right_short_class_names) / right_method_count if right_method_count else 0.0
    )
    left_short_method_ratio = (
        len(left_short_method_names) / len(left_non_constructor)
        if left_non_constructor
        else 0.0
    )
    right_short_method_ratio = (
        len(right_short_method_names) / len(right_non_constructor)
        if right_non_constructor
        else 0.0
    )
    common_fingerprints = _common_fingerprint_count(left, right)
    common_method_ids = set(left) & set(right)
    same_package = bool(left_packages) and left_packages == right_packages
    min_method_count = min(left_method_count, right_method_count)
    applied = (
        same_package
        and min_method_count > 0
        and left_method_count == right_method_count
        and common_fingerprints >= min_method_count
        and right_short_class_ratio >= OBFUSCATION_MIN_SHORT_CLASS_RATIO
        and right_short_class_ratio
        >= left_short_class_ratio + OBFUSCATION_MIN_SHORT_CLASS_RATIO_DELTA
        and len(right_short_method_names) > len(left_short_method_names)
    )

    fields = default_obfuscation_direct_evidence_fields()
    fields.update(
        {
            "obfuscation_direct_evidence_applied": applied,
            "obfuscation_direct_evidence_score": 1.0 if applied else 0.0,
            "obfuscation_direct_same_package": same_package,
            "obfuscation_direct_left_method_count": left_method_count,
            "obfuscation_direct_right_method_count": right_method_count,
            "obfuscation_direct_common_method_id_count": len(common_method_ids),
            "obfuscation_direct_common_fingerprint_count": common_fingerprints,
            "obfuscation_direct_left_short_class_name_count": len(
                left_short_class_names
            ),
            "obfuscation_direct_right_short_class_name_count": len(
                right_short_class_names
            ),
            "obfuscation_direct_left_short_method_name_count": len(
                left_short_method_names
            ),
            "obfuscation_direct_right_short_method_name_count": len(
                right_short_method_names
            ),
            "obfuscation_direct_left_short_class_name_ratio": (
                left_short_class_ratio
            ),
            "obfuscation_direct_right_short_class_name_ratio": (
                right_short_class_ratio
            ),
            "obfuscation_direct_left_short_method_name_ratio": (
                left_short_method_ratio
            ),
            "obfuscation_direct_right_short_method_name_ratio": (
                right_short_method_ratio
            ),
            "obfuscation_direct_left_package_sample": _unique_sample(left_packages),
            "obfuscation_direct_right_package_sample": _unique_sample(right_packages),
            "obfuscation_direct_left_class_name_sample": _unique_sample(
                [part["class_name"] for part in left_parts]
            ),
            "obfuscation_direct_right_class_name_sample": _unique_sample(
                right_short_class_names
            ),
            "obfuscation_direct_left_method_name_sample": _unique_sample(
                [part["method_name"] for part in left_non_constructor]
            ),
            "obfuscation_direct_right_method_name_sample": _unique_sample(
                right_short_method_names
            ),
        }
    )
    return fields

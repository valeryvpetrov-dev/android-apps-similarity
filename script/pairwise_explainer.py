#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any


try:
    from calculate_apks_similarity.result_contract import extract_class_name
    from calculate_apks_similarity.result_contract import is_library_like_dot
except Exception:
    KNOWN_LIBRARY_PREFIXES = (
        "android.",
        "androidx.",
        "com.facebook.",
        "com.google.",
        "com.squareup.",
        "java.",
        "javax.",
        "kotlin.",
        "kotlinx.",
        "okhttp3.",
        "org.apache.",
        "org.intellij.",
        "org.jetbrains.",
        "org.json.",
        "retrofit2.",
        "rx.",
    )

    def extract_class_name(dot_name: str) -> str:
        normalized = dot_name or ""
        if "/" in normalized:
            normalized = normalized.split("/", 1)[1]
        if normalized.endswith(".dot"):
            normalized = normalized[:-4]
        parts = normalized.split(" ", 2)
        return parts[0] if parts else normalized

    def is_library_like_dot(dot_name: str) -> bool:
        class_name = extract_class_name(dot_name)
        if class_name.endswith(".BuildConfig") or class_name.endswith("$BuildConfig"):
            return True
        if class_name.endswith(".R") or class_name.endswith("$R") or ".R." in class_name or "$R$" in class_name:
            return True
        return any(class_name.startswith(prefix) for prefix in KNOWN_LIBRARY_PREFIXES)


COMPONENT_CLASS_PATTERN = re.compile(
    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:Activity|Service|Receiver|Provider))\b"
)
TARGET_COMPONENT_SUFFIXES = ("Activity", "Service", "Receiver", "Provider")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate pairwise explanation hints (LibraryImpact/NewMethodCall/ComponentChange/ResourceChange/PermissionChange/NativeLibChange/CertificateMismatch/CodeRemoval)."
    )
    parser.add_argument("--enriched", required=True, help="Path to enriched pairwise JSON.")
    parser.add_argument("--output", required=True, help="Path to output JSON with explanation hints.")
    return parser.parse_args()


def to_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_string(item) for item in value if clean_string(item)]
    if isinstance(value, tuple) or isinstance(value, set):
        return [clean_string(item) for item in value if clean_string(item)]
    if isinstance(value, str):
        if "|" in value:
            return [part.strip() for part in value.split("|") if part.strip()]
        stripped = value.strip()
        return [stripped] if stripped else []
    return []


def dedupe_elements(elements: list[dict], limit: int = 8) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for element in elements:
        key = (
            clean_string(element.get("type")),
            clean_string(element.get("value")),
            clean_string(element.get("side")),
            clean_string(element.get("change")),
        )
        if not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(element)
        if len(result) >= limit:
            break
    return result


def pick_nested(source: dict, paths: list[tuple[str, ...]]) -> Any:
    for path in paths:
        current: Any = source
        ok = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                ok = False
                break
            current = current[key]
        if ok and current not in (None, ""):
            return current
    return None


def normalize_pair_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("pairs", "pairwise", "results", "candidates", "enriched_candidates", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(
            key in payload
            for key in (
                "app_a",
                "app_b",
                "query_app_id",
                "candidate_app_id",
                "pair_id",
                "pair_key",
                "similarity_score",
            )
        ):
            return [payload]

    return []


def resolve_pair_ids(pair: dict) -> tuple[str, str]:
    app_a = pick_nested(
        pair,
        [
            ("app_a",),
            ("query_app_id",),
            ("query_app",),
            ("first_app_id",),
            ("apps", "app_a", "app_id"),
            ("apps", "app_a", "id"),
            ("query", "app_id"),
            ("query", "id"),
        ],
    )
    app_b = pick_nested(
        pair,
        [
            ("app_b",),
            ("candidate_app_id",),
            ("candidate_app",),
            ("second_app_id",),
            ("apps", "app_b", "app_id"),
            ("apps", "app_b", "id"),
            ("candidate", "app_id"),
            ("candidate", "id"),
        ],
    )
    return clean_string(app_a) or "unknown_app_a", clean_string(app_b) or "unknown_app_b"


def resolve_similarity_score(pair: dict) -> float:
    score = pick_nested(
        pair,
        [
            ("similarity_score",),
            ("scores", "similarity_score"),
            ("deepening_score",),
            ("score",),
            ("pairwise_score",),
        ],
    )
    parsed = to_float(score, 0.0)
    return parsed if parsed is not None else 0.0


def resolve_pair_records(pair: dict) -> list[dict]:
    records = pick_nested(pair, [("matched_pairs",), ("sim_pairs", "pairs"), ("pairs",)])
    if isinstance(records, list):
        return [item for item in records if isinstance(item, dict)]
    return []


def resolve_dot_names(pair: dict, side: str) -> list[str]:
    if side == "a":
        candidates = [
            ("dots_1",),
            ("app_a_dots",),
            ("app_a_graph_names",),
            ("query_graph_names",),
            ("query_payload", "graph_names"),
            ("apps", "app_a", "graph_names"),
        ]
    else:
        candidates = [
            ("dots_2",),
            ("app_b_dots",),
            ("app_b_graph_names",),
            ("candidate_graph_names",),
            ("candidate_payload", "graph_names"),
            ("apps", "app_b", "graph_names"),
        ]

    value = pick_nested(pair, candidates)
    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    names.append(item.strip())
            elif isinstance(item, dict):
                for key in ("name", "dot_name", "graph_name", "value"):
                    raw = clean_string(item.get(key))
                    if raw:
                        names.append(raw)
                        break
    return names


def parse_feature_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {clean_string(item) for item in value if clean_string(item)}
    if isinstance(value, dict):
        if "features" in value:
            return parse_feature_set(value.get("features"))
        return set()
    if isinstance(value, str):
        return {item for item in split_values(value) if item}
    return set()


def resolve_side_feature_set(pair: dict, feature_kind: str, side: str) -> set[str]:
    if feature_kind == "component":
        if side == "a":
            candidates = [
                ("component_features_a",),
                ("app_a_component_features",),
                ("query_component_features",),
                ("app_a", "component_features"),
                ("query_payload", "component_features"),
                ("query_model", "component_features"),
            ]
        else:
            candidates = [
                ("component_features_b",),
                ("app_b_component_features",),
                ("candidate_component_features",),
                ("app_b", "component_features"),
                ("candidate_payload", "component_features"),
                ("candidate_model", "component_features"),
            ]
    elif feature_kind == "resource":
        if side == "a":
            candidates = [
                ("resource_features_a",),
                ("app_a_resource_features",),
                ("query_resource_features",),
                ("app_a", "resource_features"),
                ("query_payload", "resource_features"),
                ("query_model", "resource_features"),
            ]
        else:
            candidates = [
                ("resource_features_b",),
                ("app_b_resource_features",),
                ("candidate_resource_features",),
                ("app_b", "resource_features"),
                ("candidate_payload", "resource_features"),
                ("candidate_model", "resource_features"),
            ]
    else:
        return set()

    value = pick_nested(pair, candidates)
    return parse_feature_set(value)


def component_kind(component_name: str) -> str:
    simple_name = component_name.split(".")[-1]
    for suffix in TARGET_COMPONENT_SUFFIXES:
        if simple_name.endswith(suffix):
            return suffix.lower().replace("activity", "activity").replace("service", "service").replace("receiver", "receiver").replace("provider", "provider")
    return "unknown"


def component_name_from_feature(feature: str) -> str:
    if feature.startswith("manifest_component:"):
        return feature.split(":", 1)[1].strip()
    if any(feature.endswith(suffix) for suffix in TARGET_COMPONENT_SUFFIXES):
        return feature.strip()
    return ""


def package_from_class(class_name: str) -> str:
    parts = class_name.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def collect_component_names_from_pair(pair: dict) -> set[str]:
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            for match in COMPONENT_CLASS_PATTERN.findall(value):
                names.add(match)

    walk(pair)
    return names


def ensure_fallback_elements(elements: list[dict], fallback_type: str, fallback_value: str) -> list[dict]:
    if elements:
        return elements
    return [{"type": fallback_type, "value": fallback_value}]


def build_library_impact_hint(pair: dict) -> dict:
    full_score = to_float(pick_nested(pair, [("full_similarity_score",), ("scores", "full_similarity_score")]))
    reduced_score = to_float(pick_nested(pair, [("library_reduced_score",), ("scores", "library_reduced_score")]))
    score_delta = 0.0
    if full_score is not None and reduced_score is not None:
        score_delta = reduced_score - full_score

    elements: list[dict] = []
    for token in split_values(
        pick_nested(
            pair,
            [
                ("top_library_profile_overlap",),
                ("library_profile_overlap",),
                ("deepening", "top_library_profile_overlap"),
            ],
        )
    ):
        if token.startswith("api_pkg:") or token.startswith("lib_pkg:"):
            elements.append({"type": "package", "value": token.split(":", 1)[1]})
        elif token.startswith("native_lib:"):
            elements.append({"type": "file", "value": f"lib/{token.split(':', 1)[1]}*.so"})

    for record in resolve_pair_records(pair):
        first = clean_string(record.get("first"))
        second = clean_string(record.get("second"))
        if first and is_library_like_dot(first):
            class_name = extract_class_name(first)
            elements.append({"type": "class", "value": class_name, "side": "app_a"})
        if second and is_library_like_dot(second):
            class_name = extract_class_name(second)
            elements.append({"type": "class", "value": class_name, "side": "app_b"})

    elements = dedupe_elements(ensure_fallback_elements(elements, "package", "java.lang"))

    abs_delta = abs(score_delta)
    if abs_delta >= 0.20:
        severity = "high"
    elif abs_delta >= 0.05:
        severity = "medium"
    else:
        severity = "low"

    if full_score is not None and reduced_score is not None:
        description = (
            "Library-aware adjustment changed similarity from "
            f"{full_score:.6f} to {reduced_score:.6f} (delta {score_delta:+.6f})."
        )
    else:
        library_jaccard = to_float(
            pick_nested(pair, [("library_profile_jaccard",), ("deepening", "library_profile_jaccard")]),
            0.0,
        )
        description = (
            "Library-related overlap is present in the pairwise evidence "
            f"(library_profile_jaccard={library_jaccard:.6f})."
        )

    return {
        "hint_type": "LibraryImpact",
        "severity": severity,
        "elements": elements,
        "description": description,
    }


def element_from_dot(dot_name: str, side: str) -> dict:
    class_name = extract_class_name(dot_name)
    return {"type": "class", "value": class_name, "side": side}


def build_new_method_call_hint(pair: dict) -> dict:
    pair_records = resolve_pair_records(pair)
    dots_a = resolve_dot_names(pair, "a")
    dots_b = resolve_dot_names(pair, "b")
    matched_a = {int(item["first_i"]) for item in pair_records if isinstance(item.get("first_i"), int)}
    matched_b = {int(item["second_i"]) for item in pair_records if isinstance(item.get("second_i"), int)}

    elements: list[dict] = []
    unmatched_count = 0

    for idx, dot_name in enumerate(dots_a):
        if idx in matched_a or is_library_like_dot(dot_name):
            continue
        unmatched_count += 1
        elements.append(element_from_dot(dot_name, "app_a"))
        if len(elements) >= 4:
            break

    for idx, dot_name in enumerate(dots_b):
        if idx in matched_b or is_library_like_dot(dot_name):
            continue
        unmatched_count += 1
        elements.append(element_from_dot(dot_name, "app_b"))
        if len(elements) >= 8:
            break

    if not elements:
        for token in split_values(pick_nested(pair, [("top_component_overlap",), ("deepening", "top_component_overlap")])):
            if token.startswith("method_shape:"):
                method_signature = token.split(":", 1)[1]
                elements.append({"type": "class", "value": method_signature})
            if len(elements) >= 4:
                break

    if not elements:
        for record in sorted(pair_records, key=lambda item: to_float(item.get("similarity"), 1.0) or 1.0):
            first = clean_string(record.get("first"))
            second = clean_string(record.get("second"))
            if first and not is_library_like_dot(first):
                elements.append(element_from_dot(first, "app_a"))
            if second and not is_library_like_dot(second):
                elements.append(element_from_dot(second, "app_b"))
            if len(elements) >= 4:
                break

    elements = dedupe_elements(ensure_fallback_elements(elements, "class", "UnknownMethodCarrier"))

    if unmatched_count >= 6:
        severity = "high"
    elif unmatched_count >= 3:
        severity = "medium"
    else:
        severity = "low"

    description = (
        "Method-level comparison indicates unmatched or newly introduced call structures "
        f"(unmatched_count={unmatched_count})."
    )

    return {
        "hint_type": "NewMethodCall",
        "severity": severity,
        "elements": elements,
        "description": description,
    }


def build_component_change_hint(pair: dict) -> dict:
    features_a = resolve_side_feature_set(pair, "component", "a")
    features_b = resolve_side_feature_set(pair, "component", "b")

    elements: list[dict] = []
    changed_components: list[tuple[str, str, str]] = []

    for feature in sorted(features_b - features_a):
        name = component_name_from_feature(feature)
        if not name:
            continue
        kind = component_kind(name)
        if kind == "unknown":
            continue
        changed_components.append((name, "app_b", "added"))

    for feature in sorted(features_a - features_b):
        name = component_name_from_feature(feature)
        if not name:
            continue
        kind = component_kind(name)
        if kind == "unknown":
            continue
        changed_components.append((name, "app_a", "removed"))

    if not changed_components:
        for name in sorted(collect_component_names_from_pair(pair)):
            kind = component_kind(name)
            if kind == "unknown":
                continue
            changed_components.append((name, "", "observed"))
            if len(changed_components) >= 6:
                break

    for name, side, change in changed_components[:8]:
        elements.append({"type": "class", "value": name, "side": side, "change": change})
        package_name = package_from_class(name)
        if package_name:
            elements.append({"type": "package", "value": package_name, "side": side})

    if not elements:
        for token in split_values(pick_nested(pair, [("top_component_overlap",), ("deepening", "top_component_overlap")])):
            if token.startswith("class_pkg:"):
                elements.append({"type": "package", "value": token.split(":", 1)[1]})
            if len(elements) >= 6:
                break

    component_jaccard = to_float(pick_nested(pair, [("component_jaccard",), ("deepening", "component_jaccard")]), 0.0) or 0.0
    change_count = len(changed_components)
    if change_count >= 6 or component_jaccard < 0.10:
        severity = "high"
    elif change_count >= 2 or component_jaccard < 0.25:
        severity = "medium"
    else:
        severity = "low"

    elements = dedupe_elements(ensure_fallback_elements(elements, "file", "AndroidManifest.xml"))
    description = (
        "Android component layer shows structural changes across Activity/Service/Receiver/Provider entries "
        f"(changed_components={change_count}, component_jaccard={component_jaccard:.6f})."
    )

    return {
        "hint_type": "ComponentChange",
        "severity": severity,
        "elements": elements,
        "description": description,
    }


def infer_resource_categories(features: set[str]) -> set[str]:
    categories: set[str] = set()
    for feature in features:
        value = feature.lower()
        if "layout" in value:
            categories.add("layout")
        if "drawable" in value or value.startswith("res_ext:png") or value.startswith("res_ext:webp") or value.startswith("res_ext:jpg"):
            categories.add("drawable")
        if "string" in value or value.startswith("res_group:values") or value.startswith("res_ext:xml"):
            categories.add("strings")
    return categories


def resource_elements_from_categories(categories: set[str]) -> list[dict]:
    elements: list[dict] = []
    if "layout" in categories:
        elements.append({"type": "file", "value": "res/layout/*"})
    if "drawable" in categories:
        elements.append({"type": "file", "value": "res/drawable/*"})
    if "strings" in categories:
        elements.append({"type": "file", "value": "res/values/strings.xml"})
    return elements


def build_resource_change_hint(pair: dict) -> dict:
    features_a = resolve_side_feature_set(pair, "resource", "a")
    features_b = resolve_side_feature_set(pair, "resource", "b")
    changed_features = (features_a - features_b) | (features_b - features_a)

    elements: list[dict] = []
    categories = infer_resource_categories(changed_features)
    elements.extend(resource_elements_from_categories(categories))

    if not elements:
        raw_top = split_values(pick_nested(pair, [("top_resource_overlap",), ("deepening", "top_resource_overlap")]))
        top_features = {item for item in raw_top if item}
        categories = infer_resource_categories(top_features)
        elements.extend(resource_elements_from_categories(categories))
        for token in raw_top:
            if token.startswith("res_group:"):
                group = token.split(":", 1)[1]
                elements.append({"type": "file", "value": f"res/{group}/*"})
            elif token.startswith("resource_name_token:"):
                name = token.split(":", 1)[1]
                elements.append({"type": "file", "value": f"res/*/{name}.*"})

    resource_jaccard = to_float(pick_nested(pair, [("resource_jaccard",), ("deepening", "resource_jaccard")]), 0.0) or 0.0
    change_count = len(changed_features)
    if change_count >= 12 or resource_jaccard < 0.15:
        severity = "high"
    elif change_count >= 4 or resource_jaccard < 0.35:
        severity = "medium"
    else:
        severity = "low"

    elements = dedupe_elements(ensure_fallback_elements(elements, "file", "res/"))
    description = (
        "Resource layer indicates changes in layout/drawable/strings related artifacts "
        f"(changed_features={change_count}, resource_jaccard={resource_jaccard:.6f})."
    )

    return {
        "hint_type": "ResourceChange",
        "severity": severity,
        "elements": elements,
        "description": description,
    }


def build_permission_change_hint(pair: dict) -> dict:
    """Detect permission changes between app_a and app_b."""
    features_a = resolve_side_feature_set(pair, "component", "a")
    features_b = resolve_side_feature_set(pair, "component", "b")

    perms_a = {f for f in features_a if f.startswith("permission:") or f.startswith("uses-permission:")}
    perms_b = {f for f in features_b if f.startswith("permission:") or f.startswith("uses-permission:")}

    added = perms_b - perms_a
    removed = perms_a - perms_b

    elements = []
    for p in sorted(added)[:4]:
        name = p.split(":", 1)[1] if ":" in p else p
        elements.append({"type": "permission", "value": name, "side": "app_b", "change": "added"})
    for p in sorted(removed)[:4]:
        name = p.split(":", 1)[1] if ":" in p else p
        elements.append({"type": "permission", "value": name, "side": "app_a", "change": "removed"})

    change_count = len(added) + len(removed)
    severity = "high" if change_count >= 4 else "medium" if change_count >= 1 else "low"

    elements = dedupe_elements(ensure_fallback_elements(elements, "file", "AndroidManifest.xml"))
    description = (
        f"Permission declarations differ between versions "
        f"(added={len(added)}, removed={len(removed)})."
    )
    return {"hint_type": "PermissionChange", "severity": severity, "elements": elements, "description": description}


def build_native_lib_change_hint(pair: dict) -> dict:
    """Detect native library (.so) additions/removals."""
    features_a = resolve_side_feature_set(pair, "resource", "a")
    features_b = resolve_side_feature_set(pair, "resource", "b")

    native_a = {f for f in features_a if ".so" in f.lower() or f.startswith("lib/")}
    native_b = {f for f in features_b if ".so" in f.lower() or f.startswith("lib/")}

    added = native_b - native_a
    removed = native_a - native_b

    elements = []
    for lib in sorted(added)[:4]:
        name = lib.split("/")[-1] if "/" in lib else lib
        elements.append({"type": "file", "value": name, "side": "app_b", "change": "added"})
    for lib in sorted(removed)[:4]:
        name = lib.split("/")[-1] if "/" in lib else lib
        elements.append({"type": "file", "value": name, "side": "app_a", "change": "removed"})

    change_count = len(added) + len(removed)
    severity = "high" if change_count >= 3 else "medium" if change_count >= 1 else "low"

    elements = dedupe_elements(ensure_fallback_elements(elements, "file", "lib/*.so"))
    description = (
        f"Native library composition changed "
        f"(added={len(added)}, removed={len(removed)})."
    )
    return {"hint_type": "NativeLibChange", "severity": severity, "elements": elements, "description": description}


def build_certificate_mismatch_hint(pair: dict) -> dict:
    """Detect certificate/signing differences via META-INF files."""
    features_a = resolve_side_feature_set(pair, "resource", "a")
    features_b = resolve_side_feature_set(pair, "resource", "b")

    cert_a = {f for f in features_a if "META-INF" in f or "CERT" in f.upper() or "RSA" in f.upper()}
    cert_b = {f for f in features_b if "META-INF" in f or "CERT" in f.upper() or "RSA" in f.upper()}

    changed = (cert_a - cert_b) | (cert_b - cert_a)
    mismatch = cert_a != cert_b

    elements = []
    for f in sorted(changed)[:4]:
        name = f.split("/")[-1] if "/" in f else f
        elements.append({"type": "file", "value": name, "side": "both"})

    severity = "high" if mismatch and changed else "medium" if mismatch else "low"
    elements = dedupe_elements(ensure_fallback_elements(elements, "file", "META-INF/"))
    description = (
        "Signing certificate or META-INF entries differ between versions "
        f"(changed_entries={len(changed)}, mismatch={mismatch})."
    )
    return {"hint_type": "CertificateMismatch", "severity": severity, "elements": elements, "description": description}


def build_code_removal_hint(pair: dict) -> dict:
    """Detect code removed from app_a that is absent in app_b."""
    dots_a = resolve_dot_names(pair, "a")
    dots_b = resolve_dot_names(pair, "b")

    set_a = {d for d in dots_a if not is_library_like_dot(d)}
    set_b = {d for d in dots_b if not is_library_like_dot(d)}

    removed = set_a - set_b

    elements = []
    for dot in sorted(removed)[:6]:
        elements.append(element_from_dot(dot, "app_a"))

    removal_count = len(removed)
    severity = "high" if removal_count >= 10 else "medium" if removal_count >= 3 else "low"

    elements = dedupe_elements(ensure_fallback_elements(elements, "class", "UnknownRemovedClass"))
    description = (
        f"Code present in app_a is absent in app_b, indicating deletion or refactoring "
        f"(removed_classes={removal_count})."
    )
    return {"hint_type": "CodeRemoval", "severity": severity, "elements": elements, "description": description}


def build_explanation_hints(pair: dict) -> list[dict]:
    hints = [
        build_library_impact_hint(pair),
        build_new_method_call_hint(pair),
        build_component_change_hint(pair),
        build_resource_change_hint(pair),
        build_permission_change_hint(pair),
        build_native_lib_change_hint(pair),
        build_certificate_mismatch_hint(pair),
        build_code_removal_hint(pair),
    ]
    return hints


def _hints_from_evidence(evidence_list: list[dict]) -> list[dict]:
    """EXEC-DESCRIBE-PAIR-EVIDENCE-CONTRACT-ALIGN: построить hints напрямую из
    evidence-записей как из единого источника правды.

    Для каждой валидной evidence-записи (dict) формируется hint вида
    ``{"type": <layer или signal>, "signal": signal, "entity": entity,
    "score": score}``:

    - ``signal`` берётся из ``signal_type`` evidence-записи (канонический
      контракт ``evidence_formatter.make_evidence``);
    - ``entity`` берётся из ``ref`` evidence-записи (имя слоя либо
      ``apk_signature`` и тому подобные стабильные указатели);
    - ``score`` — это ``magnitude`` из evidence в диапазоне [0, 1];
    - ``type`` — это ``signal_type`` evidence (например, ``layer_score``
      для per-layer сигнала или ``signature_match`` для подписи APK).
      ``signal_type`` одновременно играет роль «layer or signal»: для
      per-layer записей это общий тип ``layer_score``, а конкретный слой
      (например, ``code``/``component``) находится в поле ``entity``.

    Не-dict и записи без ``signal_type``/``ref``/``magnitude`` пропускаются.
    """
    if not isinstance(evidence_list, list):
        return []

    hints: list[dict] = []
    for item in evidence_list:
        if not isinstance(item, dict):
            continue
        signal_type = item.get("signal_type")
        ref = item.get("ref")
        magnitude_raw = item.get("magnitude")
        if not isinstance(signal_type, str) or not signal_type.strip():
            continue
        if not isinstance(ref, str) or not ref.strip():
            continue
        try:
            score = float(magnitude_raw)
        except (TypeError, ValueError):
            continue
        hints.append(
            {
                "type": signal_type,
                "signal": signal_type,
                "entity": ref,
                "score": score,
            }
        )
    return hints


def generate_hint(pair_row: dict) -> str:
    """Deprecated: формирует hint-строку для пары.

    EXEC-HINT-20-EVIDENCE-CANON: hint — производная из Evidence, а не
    независимый объект. Эта функция сохраняет прежнюю сигнатуру, но
    внутри делегирует в канонический путь:

        evidence_formatter.collect_evidence_from_pairwise(pair_row)
          -> evidence_formatter.format_hint_from_evidence(evidence)

    Таким образом pairwise_explainer больше не вычисляет hint независимо
    от Evidence, и инвариант «факты в hint ⊆ факты в Evidence»
    сохраняется по построению.

    Новый код должен пользоваться `format_hint_from_evidence` напрямую.
    Канонический документ: `system/result-interpretation-contract-v1.md`.
    """
    # Импорт внутри функции — чтобы избежать циклических зависимостей
    # и чтобы legacy-потребители модуля не тянули reader-path.
    import evidence_formatter  # noqa: WPS433

    if not isinstance(pair_row, dict):
        return evidence_formatter.format_hint_from_evidence(None)
    evidence = evidence_formatter.collect_evidence_from_pairwise(pair_row)
    return evidence_formatter.format_hint_from_evidence(evidence)


def build_output_rows(pair_rows: list[dict]) -> list[dict]:
    output_rows: list[dict] = []
    logger = logging.getLogger(__name__)
    for pair in pair_rows:
        app_a, app_b = resolve_pair_ids(pair)

        # EXEC-DESCRIBE-PAIR-EVIDENCE-CONTRACT-ALIGN: evidence — единый источник
        # правды для explanation_hints. Если пара уже имеет непустой evidence
        # (положен writer'ом pairwise_runner через collect_evidence_from_pairwise),
        # hints строятся из него. Это устраняет расхождение между двумя
        # источниками signal/entity (evidence vs legacy-логика по полям pair).
        # Fallback на legacy build_explanation_hints сохраняется ради обратной
        # совместимости со старыми pair_row без evidence (например, из прежних
        # прогонов screening/deepening до EXEC-088-WRITERS).
        raw_evidence = pair.get("evidence")
        filtered_evidence: list[dict] = []
        if isinstance(raw_evidence, list):
            filtered_evidence = [item for item in raw_evidence if isinstance(item, dict)]

        if filtered_evidence:
            explanation_hints = _hints_from_evidence(filtered_evidence)
        else:
            pair_id = clean_string(pair.get("pair_id")) or f"{app_a}__{app_b}"
            logger.warning(
                "evidence empty, falling back to legacy hint construction for pair %s",
                pair_id,
            )
            explanation_hints = build_explanation_hints(pair)

        row: dict[str, Any] = {
            "app_a": app_a,
            "app_b": app_b,
            "similarity_score": resolve_similarity_score(pair),
            "explanation_hints": explanation_hints,
        }
        # EXEC-088-WRITERS: прокинуть единый формат Evidence, если писатель
        # pairwise_runner уже записал его на pair_row. Добавляется только
        # при наличии непустого списка dict-записей.
        if filtered_evidence:
            row["evidence"] = filtered_evidence
        output_rows.append(row)
    return output_rows


def main() -> None:
    args = parse_args()
    enriched_path = Path(args.enriched)
    output_path = Path(args.output)

    payload = json.loads(enriched_path.read_text(encoding="utf-8"))
    pair_rows = normalize_pair_rows(payload)
    explained_rows = build_output_rows(pair_rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(explained_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Explained {len(explained_rows)} pair rows -> {output_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""BOR-003 CPlugin: component-level similarity view for M_static.

Reimplements SimiDroid component comparison via decoded AndroidManifest.xml.
Stdlib only — works on apktool-decoded APKs without androguard.
"""
from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from typing import Any

ANDROID_NS = "http://schemas.android.com/apk/res/android"

# Weight vector for aggregate component Jaccard score.
WEIGHTS = {
    "activities": 0.4,
    "services": 0.2,
    "receivers": 0.2,
    "providers": 0.1,
    "permissions": 0.1,
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _ns(attr: str) -> str:
    """Expand attribute name with Android namespace."""
    return f"{{{ANDROID_NS}}}{attr}"


def _parse_intent_filters(element: ET.Element) -> list[dict[str, list[str]]]:
    filters: list[dict[str, list[str]]] = []
    for intent_filter in element.findall("intent-filter"):
        actions = [
            a.get(_ns("name"), "")
            for a in intent_filter.findall("action")
        ]
        categories = [
            c.get(_ns("name"), "")
            for c in intent_filter.findall("category")
        ]
        data_entries = []
        for d in intent_filter.findall("data"):
            parts = []
            for key in ("scheme", "host", "port", "path", "pathPrefix",
                        "pathPattern", "mimeType"):
                val = d.get(_ns(key))
                if val:
                    parts.append(f"{key}={val}")
            if parts:
                data_entries.append(";".join(parts))
        entry: dict[str, list[str]] = {}
        if actions:
            entry["action"] = actions
        if categories:
            entry["category"] = categories
        if data_entries:
            entry["data"] = data_entries
        if entry:
            filters.append(entry)
    return filters


def _parse_intent_filters_structured(
    element: ET.Element,
) -> list[dict[str, Any]]:
    """Parse intent-filters with structured data entries and priority.

    Shape of each entry:
        {
            "actions": [str, ...],
            "categories": [str, ...],
            "data": [{"scheme": str, "host": str, "mimeType": str, ...}, ...],
            "priority": int | None,
        }

    Used by ``extract_icc_tuples`` to materialize ICC tuples; kept separate
    from the legacy :func:`_parse_intent_filters` to preserve backward
    compatible JSON output of component features.
    """
    filters: list[dict[str, Any]] = []
    for intent_filter in element.findall("intent-filter"):
        actions = [
            a.get(_ns("name"), "")
            for a in intent_filter.findall("action")
        ]
        categories = [
            c.get(_ns("name"), "")
            for c in intent_filter.findall("category")
        ]
        data_items: list[dict[str, str]] = []
        for d in intent_filter.findall("data"):
            raw: dict[str, str] = {}
            for key in ("scheme", "host", "port", "path", "pathPrefix",
                        "pathPattern", "mimeType"):
                val = d.get(_ns(key))
                if val:
                    raw[key] = val
            data_items.append(raw)
        priority = _safe_int(intent_filter.get(_ns("priority")))
        if actions or categories or data_items:
            filters.append({
                "actions": actions,
                "categories": categories,
                "data": data_items,
                "priority": priority,
            })
    return filters


def _parse_bool_attr(value: str | None) -> bool:
    """Parse an ``android:*`` boolean attribute. Defaults to False."""
    if value is None:
        return False
    return value.strip().lower() == "true"


def _extract_components(
    root: ET.Element,
    tag: str,
) -> list[dict[str, Any]]:
    """Extract named components (activity, service, receiver) with intent-filters."""
    app = root.find("application")
    if app is None:
        return []
    result: list[dict[str, Any]] = []
    for elem in app.findall(tag):
        name = elem.get(_ns("name"), "")
        entry: dict[str, Any] = {"name": name}
        filters = _parse_intent_filters(elem)
        if filters:
            entry["intent_filters"] = filters
        result.append(entry)
    return result


def _extract_providers(root: ET.Element) -> list[dict[str, Any]]:
    app = root.find("application")
    if app is None:
        return []
    result: list[dict[str, Any]] = []
    for elem in app.findall("provider"):
        name = elem.get(_ns("name"), "")
        authorities = elem.get(_ns("authorities"), "")
        entry: dict[str, Any] = {"name": name}
        if authorities:
            entry["authorities"] = authorities
        result.append(entry)
    return result


# ICC-specific raw extraction: pulls per-component structured intent-filters
# with ``exported`` flag. Kept separate from :func:`_extract_components` so
# the public ``extract_component_features`` return dict keeps its historic
# string-based shape (backward-compat for JSON consumers).

_ICC_ROLES = ("activity", "service", "receiver", "provider")


def _extract_icc_raw(root: ET.Element) -> list[dict[str, Any]]:
    """Extract per-component ICC raw data needed for tuple materialization.

    Each item:
        {
            "src_role": "activity" | "service" | "receiver" | "provider",
            "name": str,
            "exported": bool,
            "filters": [ {actions, categories, data, priority}, ... ],
        }
    """
    app = root.find("application")
    if app is None:
        return []
    out: list[dict[str, Any]] = []
    for role in _ICC_ROLES:
        for elem in app.findall(role):
            filters = _parse_intent_filters_structured(elem)
            if not filters:
                continue
            out.append({
                "src_role": role,
                "name": elem.get(_ns("name"), ""),
                "exported": _parse_bool_attr(elem.get(_ns("exported"))),
                "filters": filters,
            })
    return out


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_component_features(apk_unpacked_dir: str) -> dict:
    """Parse decoded AndroidManifest.xml and extract component features.

    Parameters
    ----------
    apk_unpacked_dir:
        Path to an apktool-decoded APK directory containing
        ``AndroidManifest.xml`` at its root.

    Returns
    -------
    dict with keys: package, min_sdk, target_sdk, activities, services,
    receivers, providers, permissions, features.
    """
    manifest_path = os.path.join(apk_unpacked_dir, "AndroidManifest.xml")
    tree = ET.parse(manifest_path)
    root = tree.getroot()

    package = root.get("package", "")

    # SDK versions live in <uses-sdk> element.
    uses_sdk = root.find("uses-sdk")
    min_sdk: int | None = None
    target_sdk: int | None = None
    if uses_sdk is not None:
        min_sdk = _safe_int(uses_sdk.get(_ns("minSdkVersion")))
        target_sdk = _safe_int(uses_sdk.get(_ns("targetSdkVersion")))

    permissions: set[str] = set()
    for perm in root.findall("uses-permission"):
        name = perm.get(_ns("name"), "")
        if name:
            permissions.add(name)

    features: set[str] = set()
    for feat in root.findall("uses-feature"):
        name = feat.get(_ns("name"), "")
        if name:
            features.add(name)

    return {
        "package": package,
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "activities": _extract_components(root, "activity"),
        "services": _extract_components(root, "service"),
        "receivers": _extract_components(root, "receiver"),
        "providers": _extract_providers(root),
        "permissions": permissions,
        "features": features,
        # Structured intent-filter payload used by extract_icc_tuples.
        # Kept as a new top-level field to avoid mutating the historic
        # shape of the component sub-dicts.
        "icc_raw": _extract_icc_raw(root),
    }


# ---------------------------------------------------------------------------
# ICC tuples (EXEC-085)
# ---------------------------------------------------------------------------

# Tuple field order (must stay stable — downstream callers and tests depend
# on positional indexing):
#     0 src_role
#     1 action
#     2 category
#     3 data_scheme
#     4 data_host
#     5 data_mime
#     6 exported
#     7 priority_bucket

ICC_TUPLE_FIELDS = (
    "src_role",
    "action",
    "category",
    "data_scheme",
    "data_host",
    "data_mime",
    "exported",
    "priority_bucket",
)


def _priority_bucket(priority: int | None) -> str:
    """Classify priority value into a bucket: default / high / low."""
    if priority is None or priority == 0:
        return "default"
    if priority > 0:
        return "high"
    return "low"


def extract_icc_tuples(component_features: dict) -> list[tuple]:
    """Materialize ICC tuples from extracted component features.

    Each intent-filter on each component yields the Cartesian product of its
    ``<action>`` × ``<category>`` × ``<data>`` entries. Missing parts become
    empty strings. The resulting 8-field tuple captures an inter-component
    communication contract point and is suitable for Jaccard-style
    similarity over the multiset of such tuples.

    Returns
    -------
    list[tuple]
        Tuples of the form
        ``(src_role, action, category, data_scheme, data_host, data_mime,
        exported, priority_bucket)``.
    """
    tuples: list[tuple] = []
    for comp in component_features.get("icc_raw", []):
        src_role = comp.get("src_role", "")
        exported = bool(comp.get("exported", False))
        for flt in comp.get("filters", []):
            actions = flt.get("actions") or [""]
            categories = flt.get("categories") or [""]
            data_items = flt.get("data") or [{}]
            bucket = _priority_bucket(flt.get("priority"))
            for action in actions:
                for category in categories:
                    for data in data_items:
                        tuples.append((
                            src_role,
                            action,
                            category,
                            data.get("scheme", "") if data else "",
                            data.get("host", "") if data else "",
                            data.get("mimeType", "") if data else "",
                            exported,
                            bucket,
                        ))
    return tuples


def compare_icc(
    left_tuples: list[tuple],
    right_tuples: list[tuple],
) -> dict:
    """Compute Jaccard similarity over the sets of ICC tuples.

    Uses *set* semantics (duplicates collapsed). For two empty inputs the
    score is 1.0 (trivially identical — matches ``_jaccard`` behaviour).

    Returns
    -------
    dict
        ``{"icc_jaccard_score": float, "matched": int, "union": int}``.
    """
    set_a = set(left_tuples)
    set_b = set(right_tuples)
    if not set_a and not set_b:
        return {"icc_jaccard_score": 1.0, "matched": 0, "union": 0}
    matched = len(set_a & set_b)
    union = len(set_a | set_b)
    score = matched / union if union else 1.0
    return {
        "icc_jaccard_score": score,
        "matched": matched,
        "union": union,
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _jaccard(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _component_names(components: list[dict]) -> set[str]:
    return {c["name"] for c in components}


def _diff_sets(set_a: set[str], set_b: set[str]) -> dict:
    return {
        "shared": sorted(set_a & set_b),
        "added": sorted(set_b - set_a),
        "removed": sorted(set_a - set_b),
    }


def compare_components(features_a: dict, features_b: dict) -> dict:
    """Compute Jaccard similarity across component types.

    Returns
    -------
    dict with per-type Jaccard scores, diffs, and aggregate
    ``component_jaccard_score``.

    DEEP-20-BOTH-EMPTY-AUDIT: если все признаки с обеих сторон пусты
    (нет activities/services/receivers/providers/permissions/features),
    возвращается ``component_jaccard_score=0.0, status='both_empty',
    both_empty=True``. Ранее ``_jaccard({}, {}) == 1.0`` давал
    ``component_jaccard_score=1.0`` без флага — «оба пустых манифеста
    равны клонам». Канонический контракт: `D-2026-04-DEEP-20-BOTH-EMPTY`.
    """
    # TODO(REPR-20-IDF-WEIGHTED-JACCARD): add optional IDF-weighted
    # per-type channels once we have a stable component-token snapshot
    # contract for unpacked corpora. Kept out of this wave to avoid
    # refactoring the aggregate component score shape.
    type_keys = ("activities", "services", "receivers", "providers")
    per_type: dict[str, Any] = {}

    # DEEP-20-BOTH-EMPTY-AUDIT: единая семантика both_empty.
    # Все пять компонентных множеств + permissions + features пусты
    # с обеих сторон → отсутствие сигнала, а не совпадение.
    names_by_type_a: dict[str, set[str]] = {}
    names_by_type_b: dict[str, set[str]] = {}
    for key in type_keys:
        names_by_type_a[key] = _component_names(features_a.get(key, []))
        names_by_type_b[key] = _component_names(features_b.get(key, []))
    perm_a = set(features_a.get("permissions", set()))
    perm_b = set(features_b.get("permissions", set()))
    feat_a = set(features_a.get("features", set()))
    feat_b = set(features_b.get("features", set()))
    all_empty_a = (
        all(not names_by_type_a[k] for k in type_keys)
        and not perm_a
        and not feat_a
    )
    all_empty_b = (
        all(not names_by_type_b[k] for k in type_keys)
        and not perm_b
        and not feat_b
    )
    if all_empty_a and all_empty_b:
        icc_a = extract_icc_tuples(features_a)
        icc_b = extract_icc_tuples(features_b)
        if not icc_a and not icc_b:
            empty_per_type = {
                k: {"jaccard": 0.0, "diff": _diff_sets(set(), set())}
                for k in type_keys
            }
            empty_per_type["permissions"] = {
                "jaccard": 0.0, "diff": _diff_sets(set(), set()),
            }
            empty_per_type["features"] = {
                "jaccard": 0.0, "diff": _diff_sets(set(), set()),
            }
            return {
                "component_jaccard_score": 0.0,
                "icc_jaccard_score": 0.0,
                "combined_component_icc_score": 0.0,
                "icc_matched": 0,
                "icc_union": 0,
                "icc_tuples_a": 0,
                "icc_tuples_b": 0,
                "per_type": empty_per_type,
                "package_a": features_a.get("package", ""),
                "package_b": features_b.get("package", ""),
                "status": "both_empty",
                "both_empty": True,
            }

    for key in type_keys:
        names_a = names_by_type_a[key]
        names_b = names_by_type_b[key]
        per_type[key] = {
            "jaccard": _jaccard(names_a, names_b),
            "diff": _diff_sets(names_a, names_b),
        }

    per_type["permissions"] = {
        "jaccard": _jaccard(perm_a, perm_b),
        "diff": _diff_sets(perm_a, perm_b),
    }

    per_type["features"] = {
        "jaccard": _jaccard(feat_a, feat_b),
        "diff": _diff_sets(feat_a, feat_b),
    }

    # Weighted aggregate (features not included in aggregate — only
    # activities, services, receivers, providers, permissions).
    aggregate = sum(
        WEIGHTS[k] * per_type[k]["jaccard"]
        for k in WEIGHTS
    )

    # EXEC-085: ICC signal over (src_role, action, category, data_scheme,
    # data_host, data_mime, exported, priority_bucket) tuples.
    icc_a = extract_icc_tuples(features_a)
    icc_b = extract_icc_tuples(features_b)
    icc_cmp = compare_icc(icc_a, icc_b)
    icc_score = icc_cmp["icc_jaccard_score"]
    combined = (aggregate + icc_score) / 2

    return {
        "component_jaccard_score": aggregate,
        "icc_jaccard_score": icc_score,
        "combined_component_icc_score": combined,
        "icc_matched": icc_cmp["matched"],
        "icc_union": icc_cmp["union"],
        "icc_tuples_a": len(icc_a),
        "icc_tuples_b": len(icc_b),
        "per_type": per_type,
        "package_a": features_a.get("package", ""),
        "package_b": features_b.get("package", ""),
    }


# ---------------------------------------------------------------------------
# Explanation hints
# ---------------------------------------------------------------------------

def component_explanation_hints(comparison: dict) -> list[dict[str, Any]]:
    """Generate human-readable ComponentChange hints from comparison result."""
    hints: list[dict[str, Any]] = []
    per_type = comparison.get("per_type", {})

    for comp_type in ("activities", "services", "receivers", "providers"):
        section = per_type.get(comp_type, {})
        diff = section.get("diff", {})

        for name in diff.get("added", []):
            hints.append({
                "type": "ComponentChange",
                "subtype": "added",
                "component_type": comp_type,
                "name": name,
            })
        for name in diff.get("removed", []):
            hints.append({
                "type": "ComponentChange",
                "subtype": "removed",
                "component_type": comp_type,
                "name": name,
            })

    # Permission / feature changes.
    for meta_type in ("permissions", "features"):
        section = per_type.get(meta_type, {})
        diff = section.get("diff", {})
        for name in diff.get("added", []):
            hints.append({
                "type": "ComponentChange",
                "subtype": "added",
                "component_type": meta_type,
                "name": name,
            })
        for name in diff.get("removed", []):
            hints.append({
                "type": "ComponentChange",
                "subtype": "removed",
                "component_type": meta_type,
                "name": name,
            })

    # EXEC-085: ICC overlap hint — emitted when at least one ICC tuple
    # exists AND the two sides are not identical at the ICC level. This
    # mirrors the semantics of the other hints (they describe *differences*
    # between the two apps) and keeps ``component_explanation_hints``
    # empty for identical inputs. Shape follows the canonical
    # ``ComponentChange`` form for backward compatibility.
    total_icc = (
        comparison.get("icc_tuples_a", 0)
        + comparison.get("icc_tuples_b", 0)
    )
    icc_score = comparison.get("icc_jaccard_score", 1.0)
    if total_icc > 0 and icc_score < 1.0:
        hints.append({
            "type": "ComponentChange",
            "subtype": "icc_overlap",
            "component_type": "icc",
            "name": "icc_tuples",
            "icc_jaccard_score": icc_score,
            "matched": comparison.get("icc_matched", 0),
            "union": comparison.get("icc_union", 0),
        })

    return hints


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    """JSON-safe serializer for sets."""
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BOR-003 CPlugin: component-level Jaccard similarity between two decoded APKs.",
    )
    parser.add_argument("apk_dir_a", help="Path to first apktool-decoded APK directory.")
    parser.add_argument("apk_dir_b", help="Path to second apktool-decoded APK directory.")
    parser.add_argument("--output", "-o", help="Optional output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    features_a = extract_component_features(args.apk_dir_a)
    features_b = extract_component_features(args.apk_dir_b)

    comparison = compare_components(features_a, features_b)
    hints = component_explanation_hints(comparison)

    result = {
        "features_a": features_a,
        "features_b": features_b,
        "comparison": comparison,
        "hints": hints,
    }

    payload = json.dumps(result, indent=2, default=_serialize)

    if args.output:
        with open(args.output, "w") as f:
            f.write(payload)
        print(f"Result saved to {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    main()

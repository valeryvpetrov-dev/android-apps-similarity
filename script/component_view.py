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
    """
    type_keys = ("activities", "services", "receivers", "providers")
    per_type: dict[str, Any] = {}

    for key in type_keys:
        names_a = _component_names(features_a.get(key, []))
        names_b = _component_names(features_b.get(key, []))
        per_type[key] = {
            "jaccard": _jaccard(names_a, names_b),
            "diff": _diff_sets(names_a, names_b),
        }

    perm_a = set(features_a.get("permissions", set()))
    perm_b = set(features_b.get("permissions", set()))
    per_type["permissions"] = {
        "jaccard": _jaccard(perm_a, perm_b),
        "diff": _diff_sets(perm_a, perm_b),
    }

    feat_a = set(features_a.get("features", set()))
    feat_b = set(features_b.get("features", set()))
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

    return {
        "component_jaccard_score": aggregate,
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

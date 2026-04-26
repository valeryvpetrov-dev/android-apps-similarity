#!/usr/bin/env python3
"""Unified TPL/library mask contract for noise cleanup and static views."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional


CATEGORY_LIBRARY = "library_like"
ALGORITHM_PREFIX_V1 = "prefix_v1"
ALGORITHM_JACCARD_V2 = "jaccard_v2"
DEFAULT_JACCARD_THRESHOLD = 0.30
DEFAULT_MIN_MATCHES = 1

KNOWN_LIBRARY_PREFIXES = (
    "android",
    "androidx",
    "com.airbnb",
    "com.bumptech",
    "com.facebook",
    "com.google",
    "com.squareup",
    "dagger",
    "io.reactivex",
    "java",
    "javax",
    "kotlin",
    "kotlinx",
    "okhttp3",
    "okio",
    "org.apache",
    "org.bouncycastle",
    "org.intellij",
    "org.jetbrains",
    "org.json",
    "retrofit2",
    "rx",
)

_SMALI_ROOT_RE = re.compile(r"^smali(?:_classes\d+)?$")
_CONFIG_KEYS = (
    "library_mask_config",
    "library_detection_config",
    "library_detection",
)
_PACKAGE_KEYS = (
    "packages",
    "apk_packages",
    "app_packages",
    "package_set",
    "library_packages",
)
_PATH_KEYS = (
    "paths",
    "rel_paths",
    "relative_paths",
)
_LAYER_PREFIXES = {
    "code",
    "component",
    "resource",
    "metadata",
    "library",
}


def get_library_mask(app_record: Mapping[str, Any] | None, config: Mapping[str, Any] | None = None) -> set[str]:
    """Return the package-prefix mask that defines TPL/library elements.

    The algorithm is selected explicitly from ``config`` or
    ``app_record["cascade_config"]["library_mask"]``. No environment variable
    participates in the decision.
    """
    record: Mapping[str, Any] = app_record or {}
    precomputed = _coerce_precomputed_mask(record.get("library_mask"))
    if precomputed is not None:
        return precomputed

    mask_config = _resolve_library_mask_config(record, config)
    algorithm = _resolve_algorithm(record, mask_config)
    packages = _collect_packages(record)
    paths = _collect_paths(record)

    if algorithm == ALGORITHM_PREFIX_V1:
        return _prefix_mask(packages, paths)

    threshold = _float_config(mask_config, "threshold", DEFAULT_JACCARD_THRESHOLD)
    min_matches = _int_config(mask_config, "min_matches", DEFAULT_MIN_MATCHES)
    if packages:
        tpl_hits = detect_tpl_packages_v2(
            frozenset(packages),
            threshold=threshold,
            min_matches=min_matches,
        )
        return {
            package
            for hit in tpl_hits.values()
            if hit.get("detected")
            for package in hit.get("matched_packages", [])
        }

    return _prefix_mask(packages, paths)


def detect_tpl_packages_v2(
    apk_packages: frozenset[str],
    threshold: float = DEFAULT_JACCARD_THRESHOLD,
    min_matches: int = DEFAULT_MIN_MATCHES,
) -> dict[str, dict[str, Any]]:
    """Detect TPL catalog hits using the shared v2 package-coverage rule."""
    try:
        from script.library_view_v2 import TPL_CATALOG_V2
    except Exception:
        from library_view_v2 import TPL_CATALOG_V2  # type: ignore[no-redef]

    results: dict[str, dict[str, Any]] = {}
    for tpl_id, tpl_meta in TPL_CATALOG_V2.items():
        tpl_pkgs: frozenset[str] = tpl_meta["packages"]
        matched = apk_packages & tpl_pkgs
        if not matched:
            continue
        coverage = len(matched) / len(tpl_pkgs)
        detected = len(matched) >= min_matches and coverage >= threshold
        results[tpl_id] = {
            "coverage": coverage,
            "matched_packages": sorted(matched),
            "category": tpl_meta["category"],
            "detected": detected,
        }
    return results


def detect_library_path(
    rel_path: str,
    app_record: Mapping[str, Any] | None = None,
    library_mask: Iterable[str] | None = None,
) -> Optional[tuple[str, str]]:
    """Classify a relative path as library-like using the unified mask."""
    package_path = extract_package_path(rel_path)
    if package_path is None:
        return None

    package_dotted = package_path.replace("/", ".")
    mask = set(library_mask) if library_mask is not None else get_library_mask(
        app_record or {
            "paths": {rel_path},
            "cascade_config": {"library_mask": {"algorithm": ALGORITHM_PREFIX_V1}},
        }
    )
    matched = find_matching_library_prefix(package_dotted, mask)
    if matched is None:
        return None
    return CATEGORY_LIBRARY, "library mask package {}".format(matched)


def is_library_token(token: Any, library_mask: Iterable[str]) -> bool:
    """Return whether a set-valued feature token belongs to the library mask."""
    if not isinstance(token, str):
        return False
    mask = set(library_mask)
    if not mask:
        return False
    for candidate in _token_package_candidates(token):
        if find_matching_library_prefix(candidate, mask) is not None:
            return True
    return False


def find_matching_library_prefix(package_name: str, library_mask: Iterable[str]) -> Optional[str]:
    package = _normalize_package(package_name)
    if not package:
        return None
    for mask_package in sorted({_normalize_package(item) for item in library_mask if item}, key=len, reverse=True):
        if package == mask_package or package.startswith(mask_package + "."):
            return mask_package
    return None


def extract_package_path(rel_path: str) -> Optional[str]:
    parts = rel_path.split("/")
    if not parts:
        return None

    if _SMALI_ROOT_RE.match(parts[0]):
        if len(parts) < 3:
            return None
        return "/".join(parts[1:-1])

    if rel_path == "AndroidManifest.xml":
        return None

    if parts[0] in {"res", "assets", "lib", "kotlin"}:
        return None

    if len(parts) > 1:
        return "/".join(parts[:-1])

    return None


def _resolve_library_mask_config(
    record: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)

    for key in _CONFIG_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping):
            return dict(value)

    cascade_config = record.get("cascade_config")
    if isinstance(cascade_config, Mapping):
        for key in ("library_mask", "library_detection", "library_mask_contract"):
            value = cascade_config.get(key)
            if isinstance(value, Mapping):
                return dict(value)
        stages = cascade_config.get("stages")
        if isinstance(stages, Mapping):
            for stage_name in ("pairwise", "deepening", "screening"):
                stage = stages.get(stage_name)
                if isinstance(stage, Mapping):
                    value = stage.get("library_mask")
                    if isinstance(value, Mapping):
                        return dict(value)

    return {}


def _resolve_algorithm(record: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    raw_algorithm = (
        config.get("algorithm")
        or config.get("mode")
        or record.get("library_mask_algorithm")
        or record.get("library_detection_algorithm")
    )
    if raw_algorithm is None:
        return ALGORITHM_JACCARD_V2 if _collect_packages(record) else ALGORITHM_PREFIX_V1

    normalized = str(raw_algorithm).strip().lower().replace("-", "_")
    if normalized in {"v1", "prefix", "prefix_v1", "heuristic_v1"}:
        return ALGORITHM_PREFIX_V1
    if normalized in {"v2", "jaccard", "jaccard_v2", "library_view_v2"}:
        return ALGORITHM_JACCARD_V2
    return ALGORITHM_JACCARD_V2 if _collect_packages(record) else ALGORITHM_PREFIX_V1


def _collect_packages(record: Mapping[str, Any]) -> set[str]:
    packages: set[str] = set()
    for key in _PACKAGE_KEYS:
        packages.update(_coerce_string_set(record.get(key)))

    library_features = record.get("library")
    if isinstance(library_features, Mapping):
        packages.update(_coerce_string_set(library_features.get("app_packages")))

    for rel_path in _collect_paths(record):
        package_path = extract_package_path(rel_path)
        if package_path:
            packages.add(package_path.replace("/", "."))

    return {_normalize_package(package) for package in packages if _normalize_package(package)}


def _collect_paths(record: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    for key in _PATH_KEYS:
        paths.update(_coerce_string_set(record.get(key)))

    for element in record.get("elements", []) if isinstance(record.get("elements"), Iterable) else []:
        if isinstance(element, Mapping):
            path = element.get("path")
            if isinstance(path, str):
                paths.add(path)
        elif isinstance(element, str):
            paths.add(element)

    return paths


def _prefix_mask(packages: set[str], paths: set[str]) -> set[str]:
    candidates = set(packages)
    for rel_path in paths:
        package_path = extract_package_path(rel_path)
        if package_path:
            candidates.add(package_path.replace("/", "."))

    return {
        package
        for package in candidates
        if _matches_known_library_prefix(package)
    }


def _matches_known_library_prefix(package: str) -> bool:
    normalized = _normalize_package(package)
    if not normalized:
        return False
    for prefix in KNOWN_LIBRARY_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "."):
            return True
    return normalized.startswith("android.support.")


def _token_package_candidates(token: str) -> list[str]:
    value = token.strip()
    if ":" in value:
        prefix, remainder = value.split(":", 1)
        if prefix in _LAYER_PREFIXES:
            value = remainder

    candidates = {_normalize_package(value)}

    if value.startswith("L") and ";" in value:
        class_name = value[1:value.find(";")]
        parts = class_name.split("/")
        if len(parts) > 1:
            candidates.add(".".join(parts[:-1]))

    package_path = extract_package_path(value)
    if package_path:
        candidates.add(package_path.replace("/", "."))

    if "/" in value and not package_path:
        parts = value.split("/")
        if len(parts) > 1:
            candidates.add(".".join(parts[:-1]))

    return [candidate for candidate in candidates if candidate]


def _coerce_precomputed_mask(value: Any) -> Optional[set[str]]:
    if isinstance(value, Mapping):
        if any(key in value for key in ("algorithm", "threshold", "min_matches")):
            return None
        if "packages" in value:
            return _coerce_string_set(value.get("packages"))
        return None
    if isinstance(value, (str, bytes)):
        return {_normalize_package(str(value))}
    if isinstance(value, Iterable):
        return _coerce_string_set(value)
    return None


def _coerce_string_set(value: Any) -> set[str]:
    if value is None or isinstance(value, bytes):
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {str(key) for key in value.keys() if isinstance(key, str)}
    if isinstance(value, Iterable):
        return {str(item) for item in value if isinstance(item, str)}
    return set()


def _normalize_package(value: str) -> str:
    normalized = value.strip().strip(";")
    if normalized.startswith("L") and "/" in normalized:
        normalized = normalized[1:]
    normalized = normalized.replace("/", ".")
    while ".." in normalized:
        normalized = normalized.replace("..", ".")
    return normalized.strip(".")


def _float_config(config: Mapping[str, Any], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _int_config(config: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default

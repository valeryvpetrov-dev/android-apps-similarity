#!/usr/bin/env python3
"""Семантический профиль нескольких представлений для pairwise-сравнения APK.

Модуль переводит профиль, сначала проверенный на артефактах M1, в исполняемый
код:

* ``R_code_identity`` — адресуемые идентификаторы методов из ``code_view_v4``;
* ``R_code_stats`` — распределение fingerprint тел методов, устойчивое к
  переименованию классов/методов, но только как поддерживающий сигнал;
* ``R_code_packaging`` — грубая форма упаковки DEX;
* ``R_resource_identity`` — точное совпадение путь+digest ресурса;
* ``R_resource_structure`` — форма дерева ресурсов без digest файлов.

Модуль намеренно не меняет итоговые pairwise scores и verdict. Он создаёт
дополнительную проверяемую pairwise-проверку, которую можно использовать в
экспериментах перед переносом профиля в production aggregation policy.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any
import zipfile

try:
    from code_view_v4 import extract_code_view_v4
    from resource_view import extract_resource_features
    from resource_view_v2 import extract_resource_view_v2
    from v3_4_contracts import (
        STATUS_ANALYSIS_FAILED,
        STATUS_COMPARISON_FAILED,
        STATUS_SUCCESS,
        build_view_artifact_record,
    )
except ImportError:  # pragma: no cover - package import fallback
    from script.code_view_v4 import extract_code_view_v4
    from script.resource_view import extract_resource_features
    from script.resource_view_v2 import extract_resource_view_v2
    from script.v3_4_contracts import (
        STATUS_ANALYSIS_FAILED,
        STATUS_COMPARISON_FAILED,
        STATUS_SUCCESS,
        build_view_artifact_record,
    )


PROFILE_ID = "R_semantic_multiview_decision_policy_v0"

VIEW_SCHEMA_VERSIONS = {
    "R_code_identity": "r-code-identity-v0",
    "R_code_stats": "r-code-stats-v0",
    "R_code_packaging": "r-code-packaging-v0",
    "R_resource_identity": "r-resource-identity-v0",
    "R_resource_structure": "r-resource-structure-v0",
}

CODE_IDENTITY_HIGH = 0.70
RESOURCE_IDENTITY_HIGH = 0.70
CODE_STATS_SUPPORT = 0.70
RESOURCE_STRUCTURE_SUPPORT = 0.60
PACKAGING_SUPPORT = 0.70


def _round_score(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _weighted_jaccard(left: Counter[str], right: Counter[str]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    intersection = sum(min(left.get(key, 0), right.get(key, 0)) for key in keys)
    union = sum(max(left.get(key, 0), right.get(key, 0)) for key in keys)
    if union == 0:
        return 0.0
    return intersection / union


def _coerce_resource_digest_items(resource: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not isinstance(resource, dict):
        return []
    raw = resource.get("resource_digests")
    if not isinstance(raw, (set, list, tuple)):
        return []
    items: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            path, digest = item
            if isinstance(path, str) and isinstance(digest, str):
                items.append((path, digest))
    return sorted(items)


def _resource_digest_map(resource: dict[str, Any] | None) -> dict[str, str]:
    return {path: digest for path, digest in _coerce_resource_digest_items(resource)}


def extract_dex_packaging_tokens(apk_path: str | Path | None) -> set[str]:
    """Извлечь малую форму DEX-упаковки из APK zip."""
    if apk_path is None:
        return set()
    path = Path(apk_path)
    if not path.exists() or not path.is_file():
        return set()
    try:
        with zipfile.ZipFile(path) as archive:
            dex_names = sorted(
                name for name in archive.namelist()
                if name.startswith("classes") and name.endswith(".dex") and "/" not in name
            )
    except (OSError, zipfile.BadZipFile):
        return set()
    tokens = {"dex_count:{}".format(len(dex_names))}
    tokens.update("dex_name:{}".format(name) for name in dex_names)
    return tokens


def _code_identity_tokens(code_v4: dict[str, Any] | None) -> set[str]:
    if not isinstance(code_v4, dict):
        return set()
    fingerprints = code_v4.get("method_fingerprints")
    if not isinstance(fingerprints, dict):
        return set()
    return {
        "method_id:{}".format(method_id)
        for method_id in fingerprints
        if isinstance(method_id, str) and method_id
    }


def _code_stats_counter(code_v4: dict[str, Any] | None) -> Counter[str]:
    if not isinstance(code_v4, dict):
        return Counter()
    fingerprints = code_v4.get("method_fingerprints")
    if not isinstance(fingerprints, dict):
        return Counter()
    counter: Counter[str] = Counter()
    for fingerprint in fingerprints.values():
        if isinstance(fingerprint, str) and fingerprint:
            counter["fp:{}".format(fingerprint)] += 1
    return counter


def _resource_identity_tokens(resource: dict[str, Any] | None) -> set[str]:
    return {
        "path_digest:{}:{}".format(path, digest)
        for path, digest in _coerce_resource_digest_items(resource)
    }


def _resource_family(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 2:
        return "unknown"
    if parts[0] == "res":
        return parts[1].split("-", 1)[0]
    return parts[0]


def _resource_structure_tokens(
    resource: dict[str, Any] | None,
    resource_v2: dict[str, Any] | None = None,
) -> set[str]:
    tokens: set[str] = set()
    for path, _digest in _coerce_resource_digest_items(resource):
        path_obj = Path(path)
        suffix = path_obj.suffix.lower() or "no_ext"
        stem = path_obj.stem
        parent = path_obj.parent.as_posix()
        family = _resource_family(path)
        tokens.add("path:{}".format(path))
        tokens.add("parent:{}".format(parent))
        tokens.add("family:{}".format(family))
        tokens.add("entry:{}:{}".format(family, stem))
        tokens.add("ext:{}:{}".format(family, suffix))

    if isinstance(resource_v2, dict):
        for key in ("res_strings", "res_drawables", "res_layouts", "assets_bin"):
            raw_tokens = resource_v2.get(key)
            if not isinstance(raw_tokens, (set, list, tuple)):
                continue
            for token in raw_tokens:
                if isinstance(token, str) and token:
                    tokens.add("v2:{}:{}".format(key, token))
        if resource_v2.get("icon_phash"):
            tokens.add("v2:icon_phash:present")
    return tokens


def _view_record(
    *,
    apk_id: str,
    view_type: str,
    artifact_kind: str,
    token_count: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_ref: dict[str, Any] = {
        "profile_id": PROFILE_ID,
        "artifact_kind": artifact_kind,
        "token_count": int(token_count),
    }
    if extra:
        artifact_ref.update(extra)
    return build_view_artifact_record(
        apk_id=apk_id,
        view_type=view_type,
        artifact_ref=artifact_ref,
        status=STATUS_SUCCESS,
        view_schema_version=VIEW_SCHEMA_VERSIONS.get(view_type),
    )


def build_semantic_views_from_features(
    *,
    apk_id: str,
    code_v4: dict[str, Any] | None = None,
    resource: dict[str, Any] | None = None,
    resource_v2: dict[str, Any] | None = None,
    dex_packaging_tokens: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Собрать in-memory semantic views из уже извлечённых признаков."""
    code_identity = _code_identity_tokens(code_v4)
    code_stats = _code_stats_counter(code_v4)
    packaging = set(dex_packaging_tokens or set())
    resource_identity = _resource_identity_tokens(resource)
    resource_structure = _resource_structure_tokens(resource, resource_v2)
    resource_paths = _resource_digest_map(resource)

    views = {
        "R_code_identity": {"tokens": code_identity},
        "R_code_stats": {"counter": code_stats},
        "R_code_packaging": {"tokens": packaging},
        "R_resource_identity": {"tokens": resource_identity},
        "R_resource_structure": {"tokens": resource_structure},
    }
    view_artifacts = [
        _view_record(
            apk_id=apk_id,
            view_type="R_code_identity",
            artifact_kind="token_set",
            token_count=len(code_identity),
        ),
        _view_record(
            apk_id=apk_id,
            view_type="R_code_stats",
            artifact_kind="counter",
            token_count=sum(code_stats.values()),
            extra={"unique_token_count": len(code_stats)},
        ),
        _view_record(
            apk_id=apk_id,
            view_type="R_code_packaging",
            artifact_kind="token_set",
            token_count=len(packaging),
        ),
        _view_record(
            apk_id=apk_id,
            view_type="R_resource_identity",
            artifact_kind="token_set",
            token_count=len(resource_identity),
        ),
        _view_record(
            apk_id=apk_id,
            view_type="R_resource_structure",
            artifact_kind="token_set",
            token_count=len(resource_structure),
        ),
    ]
    return {
        "profile_id": PROFILE_ID,
        "apk_id": apk_id,
        "view_schema_versions": dict(VIEW_SCHEMA_VERSIONS),
        "views": views,
        "resource_paths": resource_paths,
        "view_artifacts": view_artifacts,
    }


def extract_semantic_views(
    *,
    apk_path: str | Path | None,
    decoded_dir: str | Path | None = None,
    apk_id: str | None = None,
    feature_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Извлечь semantic views из APK и, если есть, decoded directory."""
    resolved_apk_id = apk_id or (Path(apk_path).name if apk_path else "APP-UNKNOWN")
    bundle = feature_bundle if isinstance(feature_bundle, dict) else {}

    code_v4 = bundle.get("code_v4")
    if code_v4 is None and apk_path is not None:
        code_v4 = extract_code_view_v4(Path(apk_path))

    resource = bundle.get("resource")
    resource_v2 = bundle.get("resource_v2")
    if decoded_dir is not None:
        if resource is None:
            resource = extract_resource_features(str(decoded_dir))
        if resource_v2 is None:
            resource_v2 = extract_resource_view_v2(str(decoded_dir))

    return build_semantic_views_from_features(
        apk_id=resolved_apk_id,
        code_v4=code_v4,
        resource=resource,
        resource_v2=resource_v2,
        dex_packaging_tokens=extract_dex_packaging_tokens(apk_path),
    )


def _resource_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_paths = left.get("resource_paths")
    right_paths = right.get("resource_paths")
    if not isinstance(left_paths, dict):
        left_paths = {}
    if not isinstance(right_paths, dict):
        right_paths = {}

    added = sorted(path for path in right_paths if path not in left_paths)
    removed = sorted(path for path in left_paths if path not in right_paths)
    modified = sorted(
        path for path in left_paths
        if path in right_paths and left_paths[path] != right_paths[path]
    )
    unchanged = sorted(
        path for path in left_paths
        if path in right_paths and left_paths[path] == right_paths[path]
    )
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": len(unchanged),
        "added_count": len(added),
        "removed_count": len(removed),
        "modified_count": len(modified),
    }


def _tokens(views: dict[str, Any], view_type: str) -> set[str]:
    view = views.get("views", {}).get(view_type, {})
    tokens = view.get("tokens") if isinstance(view, dict) else None
    return set(tokens) if isinstance(tokens, (set, list, tuple)) else set()


def _counter(views: dict[str, Any], view_type: str) -> Counter[str]:
    view = views.get("views", {}).get(view_type, {})
    counter = view.get("counter") if isinstance(view, dict) else None
    return Counter(counter) if isinstance(counter, dict) else Counter()


def _semantic_relation(scores: dict[str, float]) -> tuple[str, str]:
    code_identity = scores["R_code_identity"]
    code_stats = scores["R_code_stats"]
    code_packaging = scores["R_code_packaging"]
    resource_identity = scores["R_resource_identity"]
    resource_structure = scores["R_resource_structure"]

    has_code_identity = code_identity >= CODE_IDENTITY_HIGH
    has_resource_identity = resource_identity >= RESOURCE_IDENTITY_HIGH
    has_code_stats = code_stats >= CODE_STATS_SUPPORT
    has_resource_structure = resource_structure >= RESOURCE_STRUCTURE_SUPPORT
    has_packaging = code_packaging >= PACKAGING_SUPPORT

    if has_code_identity and has_resource_identity:
        return "high", "same_code_same_resources"
    if has_code_identity and has_resource_structure and not has_resource_identity:
        return "high", "same_code_resource_changed"
    if has_code_identity and (has_code_stats or has_packaging):
        return "high", "same_code_supported"
    if has_resource_identity and has_resource_structure and has_code_stats:
        return "high", "same_resources_code_stats_match"
    if has_resource_identity and has_resource_structure:
        return "review", "same_resources_without_code_identity"
    if has_code_stats or has_resource_structure or has_packaging:
        return "review", "supporting_signals_without_identity"
    return "low", "weak_or_unrelated"


def compare_semantic_views(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Сравнить два набора semantic views и вернуть JSON-safe результат."""
    scores = {
        "R_code_identity": _round_score(
            _jaccard(_tokens(left, "R_code_identity"), _tokens(right, "R_code_identity"))
        ),
        "R_code_stats": _round_score(
            _weighted_jaccard(_counter(left, "R_code_stats"), _counter(right, "R_code_stats"))
        ),
        "R_code_packaging": _round_score(
            _jaccard(_tokens(left, "R_code_packaging"), _tokens(right, "R_code_packaging"))
        ),
        "R_resource_identity": _round_score(
            _jaccard(
                _tokens(left, "R_resource_identity"),
                _tokens(right, "R_resource_identity"),
            )
        ),
        "R_resource_structure": _round_score(
            _jaccard(
                _tokens(left, "R_resource_structure"),
                _tokens(right, "R_resource_structure"),
            )
        ),
    }
    semantic_band, semantic_relation = _semantic_relation(scores)
    semantic_score = _round_score(
        0.35 * scores["R_code_identity"]
        + 0.25 * scores["R_code_stats"]
        + 0.05 * scores["R_code_packaging"]
        + 0.20 * scores["R_resource_identity"]
        + 0.15 * scores["R_resource_structure"]
    )

    left_artifacts = left.get("view_artifacts")
    right_artifacts = right.get("view_artifacts")
    return {
        "profile_id": PROFILE_ID,
        "status": STATUS_SUCCESS,
        "semantic_score": semantic_score,
        "semantic_band": semantic_band,
        "semantic_relation": semantic_relation,
        "scores": scores,
        "resource_delta": _resource_delta(left, right),
        "view_schema_versions": dict(VIEW_SCHEMA_VERSIONS),
        "view_artifacts": {
            "left": list(left_artifacts) if isinstance(left_artifacts, list) else [],
            "right": list(right_artifacts) if isinstance(right_artifacts, list) else [],
        },
        "decision_notes": [
            "R_code_stats и R_code_packaging являются поддерживающими сигналами и не дают high без identity-якоря.",
            "R_resource_structure игнорирует digest файлов и объясняет замену ресурсов или restyling.",
        ],
    }


def run_semantic_multiview_check(
    *,
    apk_a: str | Path | None,
    apk_b: str | Path | None,
    decoded_a: str | Path | None = None,
    decoded_b: str | Path | None = None,
    app_a: str | None = None,
    app_b: str | None = None,
    feature_bundle_a: dict[str, Any] | None = None,
    feature_bundle_b: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Извлечь и сравнить semantic views для одной пары.

    Ошибки возвращаются как структурные статусы, чтобы вызывающий код не
    превращал технические сбои в нулевое сходство.
    """
    start = perf_counter()
    try:
        if apk_a is None or apk_b is None:
            raise ValueError("missing_apk_path")
        left = extract_semantic_views(
            apk_path=apk_a,
            decoded_dir=decoded_a,
            apk_id=app_a,
            feature_bundle=feature_bundle_a,
        )
        right = extract_semantic_views(
            apk_path=apk_b,
            decoded_dir=decoded_b,
            apk_id=app_b,
            feature_bundle=feature_bundle_b,
        )
        result = compare_semantic_views(left, right)
        result["duration_ms"] = int(round((perf_counter() - start) * 1000))
        result["inputs"] = {
            "app_a": app_a,
            "app_b": app_b,
            "apk_a": str(apk_a),
            "apk_b": str(apk_b),
            "decoded_a": str(decoded_a) if decoded_a is not None else None,
            "decoded_b": str(decoded_b) if decoded_b is not None else None,
        }
        return result
    except ValueError as exc:
        return {
            "profile_id": PROFILE_ID,
            "status": STATUS_ANALYSIS_FAILED,
            "error": str(exc),
            "duration_ms": int(round((perf_counter() - start) * 1000)),
            "scores": {},
        }
    except Exception as exc:  # pragma: no cover - defensive boundary for runners
        return {
            "profile_id": PROFILE_ID,
            "status": STATUS_COMPARISON_FAILED,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "duration_ms": int(round((perf_counter() - start) * 1000)),
            "scores": {},
        }

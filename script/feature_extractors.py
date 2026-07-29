#!/usr/bin/env python3
"""Feature extractor wrappers for architecture v3.4.

This module is intentionally thin: it adapts existing extraction functions to
the v3.4 ExtractorRunRecord/ViewArtifactRecord contracts without changing
similarity scores, thresholds, or verdict logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any
import zipfile

try:
    from script.feature_cache import FeatureCache
    from script.screening_runner import extract_layers_from_apk
    from script.semantic_multiview import (
        VIEW_SCHEMA_VERSIONS as SEMANTIC_VIEW_SCHEMA_VERSIONS,
        extract_semantic_views,
    )
    from script.v3_4_contracts import (
        STATUS_ANALYSIS_FAILED,
        STATUS_PARTIAL_RESULT,
        STATUS_SUCCESS,
        STATUS_UNSUPPORTED_INPUT,
        build_extractor_capability,
        build_extractor_run_record,
        build_view_artifact_record,
    )
except ImportError:  # pragma: no cover - direct script import fallback
    from feature_cache import FeatureCache
    from screening_runner import extract_layers_from_apk
    from semantic_multiview import (
        VIEW_SCHEMA_VERSIONS as SEMANTIC_VIEW_SCHEMA_VERSIONS,
        extract_semantic_views,
    )
    from v3_4_contracts import (
        STATUS_ANALYSIS_FAILED,
        STATUS_PARTIAL_RESULT,
        STATUS_SUCCESS,
        STATUS_UNSUPPORTED_INPUT,
        build_extractor_capability,
        build_extractor_run_record,
        build_view_artifact_record,
    )


ZIP_LIGHT_EXTRACTOR_ID = "zip_light_extractor"
ZIP_LIGHT_EXTRACTOR_VERSION = "zip-light-v1"
ZIP_LIGHT_SUPPORTED_VIEWS = ("code", "component", "resource", "metadata", "library")
ZIP_LIGHT_SUPPORTED_MODES = ("light",)
_ZIP_LIGHT_SCHEMA_PREFIX, _ZIP_LIGHT_SCHEMA_VERSION = (
    ZIP_LIGHT_EXTRACTOR_VERSION.rsplit("-", 1)
)
ZIP_LIGHT_VIEW_SCHEMA_VERSIONS = MappingProxyType(
    {
        view: "{}-{}-{}".format(
            _ZIP_LIGHT_SCHEMA_PREFIX,
            view,
            _ZIP_LIGHT_SCHEMA_VERSION,
        )
        for view in ZIP_LIGHT_SUPPORTED_VIEWS
    }
)

SEMANTIC_MULTIVIEW_EXTRACTOR_ID = "semantic_multiview_extractor"
SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION = "semantic-multiview-v1"
SEMANTIC_MULTIVIEW_SUPPORTED_VIEWS = tuple(SEMANTIC_VIEW_SCHEMA_VERSIONS.keys())
SEMANTIC_MULTIVIEW_SUPPORTED_MODES = ("deep", "diagnostic")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:{}".format(digest.hexdigest())


def _extractor_cache_storage_key(cache_key: str) -> str:
    """Map arbitrary v3.4 cache_key text to a filesystem-safe JSON key."""
    return sha256(cache_key.encode("utf-8")).hexdigest()


def _build_extractor_cache(cache_dir: object) -> FeatureCache | None:
    if cache_dir is None:
        return None
    return FeatureCache(cache_dir)


def _read_extractor_cache(cache: FeatureCache | None, cache_key: str) -> dict[str, Any] | None:
    if cache is None or not cache.available:
        return None
    cached = cache.get(_extractor_cache_storage_key(cache_key))
    if not isinstance(cached, dict):
        return None
    if cached.get("cache_key") != cache_key:
        return None
    payload = cached.get("payload")
    return payload if isinstance(payload, dict) else None


def _write_extractor_cache(
    cache: FeatureCache | None,
    cache_key: str,
    payload: dict[str, Any],
) -> None:
    if cache is None or not cache.available:
        return
    cache.put(
        _extractor_cache_storage_key(cache_key),
        {
            "cache_key": cache_key,
            "payload": payload,
        },
    )


def _cache_status_for_miss(cache: FeatureCache | None) -> str:
    if cache is None or not cache.available:
        return "not_used"
    return "miss"


def _zip_light_payload_failure(path: Path) -> str | None:
    """Return a typed analysis failure when ZIP metadata is not enough."""
    with zipfile.ZipFile(path, "r") as archive:
        dex_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dex") and not name.endswith("/")
        ]
        if not dex_names:
            return "code_empty_apk"

        for dex_name in dex_names:
            with archive.open(dex_name, "r") as stream:
                header = stream.read(8)
            if (
                len(header) >= 8
                and header.startswith(b"dex\n")
                and header[4:7].isdigit()
                and header[7:8] == b"\x00"
            ):
                return None
        return "invalid_dex_payload"


def _zip_light_cache_key(
    *,
    apk_sha256: str,
    representation_spec_ref: object = None,
    config_hash: object = None,
) -> str:
    return "{}:{}:light:{}:{}:{}".format(
        ZIP_LIGHT_EXTRACTOR_ID,
        ZIP_LIGHT_EXTRACTOR_VERSION,
        apk_sha256,
        representation_spec_ref or "representation-spec:none",
        config_hash or "config:none",
    )


def _semantic_multiview_cache_key(
    *,
    apk_sha256: str,
    mode: str,
    representation_spec_ref: object = None,
    config_hash: object = None,
) -> str:
    return "{}:{}:{}:{}:{}:{}".format(
        SEMANTIC_MULTIVIEW_EXTRACTOR_ID,
        SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        mode,
        apk_sha256,
        representation_spec_ref or "representation-spec:none",
        config_hash or "config:none",
    )


def build_zip_light_extractor_capability() -> dict[str, Any]:
    """Describe the current ZIP-based light extractor."""
    return build_extractor_capability(
        extractor_id=ZIP_LIGHT_EXTRACTOR_ID,
        extractor_version=ZIP_LIGHT_EXTRACTOR_VERSION,
        supported_views=list(ZIP_LIGHT_SUPPORTED_VIEWS),
        supported_modes=list(ZIP_LIGHT_SUPPORTED_MODES),
        cost={"class": "low", "requires_decoded_dir": False},
        tool_name="python-zipfile",
        tool_version="stdlib",
        cache_key_fields=[
            "apk_sha256",
            "extractor_id",
            "extractor_version",
            "mode",
            "representation_spec_ref",
            "config_hash",
        ],
    )


def build_semantic_multiview_extractor_capability() -> dict[str, Any]:
    """Describe the semantic multiview extractor."""
    return build_extractor_capability(
        extractor_id=SEMANTIC_MULTIVIEW_EXTRACTOR_ID,
        extractor_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        supported_views=list(SEMANTIC_MULTIVIEW_SUPPORTED_VIEWS),
        supported_modes=list(SEMANTIC_MULTIVIEW_SUPPORTED_MODES),
        cost={
            "class": "medium",
            "requires_decoded_dir": False,
            "uses_decoded_dir_when_available": True,
        },
        tool_name="semantic_multiview.py",
        tool_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        requires=["code_view_v4", "resource_view", "resource_view_v2"],
        cache_key_fields=[
            "apk_sha256",
            "extractor_id",
            "extractor_version",
            "mode",
            "representation_spec_ref",
            "config_hash",
        ],
    )


def _normalize_requested_views(
    requested_views: object,
    supported_views: tuple[str, ...] = ZIP_LIGHT_SUPPORTED_VIEWS,
) -> list[str]:
    if requested_views is None:
        return list(supported_views)
    if not isinstance(requested_views, (list, tuple, set)):
        return []
    return [str(view) for view in requested_views if isinstance(view, str) and view.strip()]


def _failure_result(
    *,
    status: str,
    errors: list[str],
    apk_sha256: str,
    requested_views: list[str],
    started_at: str,
    duration_ms: int,
    cache_key: str,
    profile_ref: object,
    representation_spec_ref: object,
    config_hash: object,
    cache_status: str = "not_used",
) -> dict[str, Any]:
    run_record = build_extractor_run_record(
        extractor_id=ZIP_LIGHT_EXTRACTOR_ID,
        extractor_version=ZIP_LIGHT_EXTRACTOR_VERSION,
        tool_name="python-zipfile",
        tool_version="stdlib",
        mode="light",
        apk_sha256=apk_sha256,
        requested_views=requested_views,
        produced_views=[],
        status=status,
        started_at=started_at,
        finished_at=_utc_timestamp(),
        duration_ms=duration_ms,
        cache_key=cache_key,
        cache_status=cache_status,
        profile_ref=profile_ref,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
        errors=errors,
    )
    return {
        "status": status,
        "extractor_run_record": run_record,
        "view_artifacts": [],
        "layers": {},
        "warnings": [],
        "errors": list(errors),
    }


def _semantic_failure_result(
    *,
    status: str,
    errors: list[str],
    apk_sha256: str,
    requested_views: list[str],
    started_at: str,
    duration_ms: int,
    cache_key: str,
    mode: str,
    profile_ref: object,
    representation_spec_ref: object,
    config_hash: object,
    cache_status: str = "not_used",
) -> dict[str, Any]:
    run_record = build_extractor_run_record(
        extractor_id=SEMANTIC_MULTIVIEW_EXTRACTOR_ID,
        extractor_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        tool_name="semantic_multiview.py",
        tool_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        mode=mode,
        apk_sha256=apk_sha256,
        requested_views=requested_views,
        produced_views=[],
        status=status,
        started_at=started_at,
        finished_at=_utc_timestamp(),
        duration_ms=duration_ms,
        cache_key=cache_key,
        cache_status=cache_status,
        profile_ref=profile_ref,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
        errors=errors,
    )
    return {
        "status": status,
        "extractor_run_record": run_record,
        "view_artifacts": [],
        "semantic_views": {},
        "warnings": [],
        "errors": list(errors),
    }


def run_zip_light_extractor(
    apk_path: str | Path,
    *,
    requested_views: object = None,
    profile_ref: object = None,
    representation_spec_ref: object = None,
    config_hash: object = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the current light APK ZIP extractor under the v3.4 contract."""
    started_at = _utc_timestamp()
    started = perf_counter()
    requested = _normalize_requested_views(requested_views)
    path = Path(apk_path)
    apk_sha256 = "sha256:unknown"

    if not path.exists() or not path.is_file():
        cache_key = _zip_light_cache_key(
            apk_sha256=apk_sha256,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
        return _failure_result(
            status=STATUS_UNSUPPORTED_INPUT,
            errors=["missing_apk"],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )

    apk_sha256 = _sha256_file(path)
    cache_key = _zip_light_cache_key(
        apk_sha256=apk_sha256,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
    )

    try:
        payload_failure = _zip_light_payload_failure(path)
    except zipfile.BadZipFile:
        return _failure_result(
            status=STATUS_ANALYSIS_FAILED,
            errors=["bad_zipfile"],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
    except OSError as exc:
        return _failure_result(
            status=STATUS_ANALYSIS_FAILED,
            errors=["io_error:{}".format(type(exc).__name__)],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
    if payload_failure is not None:
        return _failure_result(
            status=STATUS_ANALYSIS_FAILED,
            errors=[payload_failure],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )

    cache = _build_extractor_cache(cache_dir)
    cached_payload = _read_extractor_cache(cache, cache_key)
    if cached_payload is not None:
        extracted_layers = cached_payload.get("layers")
        cache_status = "hit"
    else:
        cache_status = _cache_status_for_miss(cache)
        try:
            extracted_layers = extract_layers_from_apk(path)
        except zipfile.BadZipFile:
            return _failure_result(
                status=STATUS_ANALYSIS_FAILED,
                errors=["bad_zipfile"],
                apk_sha256=apk_sha256,
                requested_views=requested,
                started_at=started_at,
                duration_ms=int((perf_counter() - started) * 1000),
                cache_key=cache_key,
                profile_ref=profile_ref,
                representation_spec_ref=representation_spec_ref,
                config_hash=config_hash,
                cache_status=cache_status,
            )
        except OSError as exc:
            return _failure_result(
                status=STATUS_ANALYSIS_FAILED,
                errors=["io_error:{}".format(type(exc).__name__)],
                apk_sha256=apk_sha256,
                requested_views=requested,
                started_at=started_at,
                duration_ms=int((perf_counter() - started) * 1000),
                cache_key=cache_key,
                profile_ref=profile_ref,
                representation_spec_ref=representation_spec_ref,
                config_hash=config_hash,
                cache_status=cache_status,
            )
        _write_extractor_cache(cache, cache_key, {"layers": extracted_layers})
    if not isinstance(extracted_layers, dict):
        extracted_layers = {}

    supported_requested = [
        view for view in requested if view in ZIP_LIGHT_SUPPORTED_VIEWS
    ]
    unsupported_requested = [
        view for view in requested if view not in ZIP_LIGHT_SUPPORTED_VIEWS
    ]
    produced_views = [
        view for view in supported_requested if view in extracted_layers
    ]
    status = STATUS_SUCCESS if len(produced_views) == len(requested) else STATUS_PARTIAL_RESULT
    warnings = [
        "unsupported_view:{}".format(view) for view in unsupported_requested
    ]

    finished_at = _utc_timestamp()
    duration_ms = int((perf_counter() - started) * 1000)
    run_record = build_extractor_run_record(
        extractor_id=ZIP_LIGHT_EXTRACTOR_ID,
        extractor_version=ZIP_LIGHT_EXTRACTOR_VERSION,
        tool_name="python-zipfile",
        tool_version="stdlib",
        mode="light",
        apk_sha256=apk_sha256,
        requested_views=requested,
        produced_views=produced_views,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        cache_key=cache_key,
        cache_status=cache_status,
        profile_ref=profile_ref,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
        warnings=warnings,
    )

    view_artifacts = []
    layers = {}
    for view in produced_views:
        values = set(extracted_layers.get(view, set()))
        layers[view] = values
        view_artifacts.append(
            build_view_artifact_record(
                apk_id=apk_sha256,
                view_type=view,
                artifact_ref={
                    "extractor_id": ZIP_LIGHT_EXTRACTOR_ID,
                    "artifact_kind": "token_set",
                    "token_count": len(values),
                    "source": "apk_zip",
                },
                status=STATUS_SUCCESS,
                view_schema_version=ZIP_LIGHT_VIEW_SCHEMA_VERSIONS[view],
                extractor_run_ref=run_record["run_id"],
                extractor_run_record=run_record,
            )
        )

    return {
        "status": status,
        "extractor_run_record": run_record,
        "view_artifacts": view_artifacts,
        "layers": layers,
        "warnings": warnings,
        "errors": [],
    }


def run_semantic_multiview_extractor(
    apk_path: str | Path,
    *,
    decoded_dir: str | Path | None = None,
    requested_views: object = None,
    feature_bundle: dict[str, Any] | None = None,
    profile_ref: object = None,
    representation_spec_ref: object = None,
    config_hash: object = None,
    mode: str = "deep",
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run semantic multiview extraction under the v3.4 extractor contract."""
    started_at = _utc_timestamp()
    started = perf_counter()
    normalized_mode = mode if mode in SEMANTIC_MULTIVIEW_SUPPORTED_MODES else "deep"
    requested = _normalize_requested_views(
        requested_views,
        SEMANTIC_MULTIVIEW_SUPPORTED_VIEWS,
    )
    path = Path(apk_path)
    apk_sha256 = "sha256:unknown"

    if not path.exists() or not path.is_file():
        cache_key = _semantic_multiview_cache_key(
            apk_sha256=apk_sha256,
            mode=normalized_mode,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
        return _semantic_failure_result(
            status=STATUS_UNSUPPORTED_INPUT,
            errors=["missing_apk"],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            mode=normalized_mode,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )

    apk_sha256 = _sha256_file(path)
    cache_key = _semantic_multiview_cache_key(
        apk_sha256=apk_sha256,
        mode=normalized_mode,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
    )

    try:
        with zipfile.ZipFile(path, "r") as archive:
            archive.namelist()
    except zipfile.BadZipFile:
        return _semantic_failure_result(
            status=STATUS_ANALYSIS_FAILED,
            errors=["bad_zipfile"],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            mode=normalized_mode,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
    except OSError as exc:
        return _semantic_failure_result(
            status=STATUS_ANALYSIS_FAILED,
            errors=["io_error:{}".format(type(exc).__name__)],
            apk_sha256=apk_sha256,
            requested_views=requested,
            started_at=started_at,
            duration_ms=int((perf_counter() - started) * 1000),
            cache_key=cache_key,
            mode=normalized_mode,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )

    cache = _build_extractor_cache(cache_dir)
    cached_payload = _read_extractor_cache(cache, cache_key)
    if cached_payload is not None:
        semantic_views = cached_payload.get("semantic_views")
        cache_status = "hit"
    else:
        cache_status = _cache_status_for_miss(cache)
        semantic_views = extract_semantic_views(
            apk_path=path,
            decoded_dir=decoded_dir,
            apk_id=apk_sha256,
            feature_bundle=feature_bundle,
        )
        _write_extractor_cache(cache, cache_key, {"semantic_views": semantic_views})
    if not isinstance(semantic_views, dict):
        semantic_views = {}
    all_views = semantic_views.get("views")
    if not isinstance(all_views, dict):
        all_views = {}

    supported_requested = [
        view for view in requested if view in SEMANTIC_MULTIVIEW_SUPPORTED_VIEWS
    ]
    unsupported_requested = [
        view for view in requested if view not in SEMANTIC_MULTIVIEW_SUPPORTED_VIEWS
    ]
    produced_views = [
        view for view in supported_requested if view in all_views
    ]
    status = STATUS_SUCCESS if len(produced_views) == len(requested) else STATUS_PARTIAL_RESULT
    warnings = [
        "unsupported_view:{}".format(view) for view in unsupported_requested
    ]

    run_record = build_extractor_run_record(
        extractor_id=SEMANTIC_MULTIVIEW_EXTRACTOR_ID,
        extractor_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        tool_name="semantic_multiview.py",
        tool_version=SEMANTIC_MULTIVIEW_EXTRACTOR_VERSION,
        mode=normalized_mode,
        apk_sha256=apk_sha256,
        requested_views=requested,
        produced_views=produced_views,
        status=status,
        started_at=started_at,
        finished_at=_utc_timestamp(),
        duration_ms=int((perf_counter() - started) * 1000),
        cache_key=cache_key,
        cache_status=cache_status,
        profile_ref=profile_ref,
        representation_spec_ref=representation_spec_ref,
        config_hash=config_hash,
        warnings=warnings,
    )

    artifacts_by_view = {
        item.get("view_type"): item
        for item in semantic_views.get("view_artifacts", [])
        if isinstance(item, dict)
    }
    view_artifacts: list[dict[str, Any]] = []
    for view in produced_views:
        artifact = dict(artifacts_by_view.get(view, {}))
        if not artifact:
            artifact = build_view_artifact_record(
                apk_id=apk_sha256,
                view_type=view,
                artifact_ref={
                    "extractor_id": SEMANTIC_MULTIVIEW_EXTRACTOR_ID,
                    "artifact_kind": "semantic_view",
                    "source": "semantic_multiview",
                },
                status=STATUS_SUCCESS,
                view_schema_version=SEMANTIC_VIEW_SCHEMA_VERSIONS.get(view),
            )
        artifact["extractor_run_ref"] = run_record["run_id"]
        artifact["extractor_run_record"] = run_record
        view_artifacts.append(artifact)

    filtered_semantic_views = dict(semantic_views)
    filtered_semantic_views["views"] = {
        view: all_views[view]
        for view in produced_views
        if view in all_views
    }
    filtered_semantic_views["view_artifacts"] = view_artifacts

    return {
        "status": status,
        "extractor_run_record": run_record,
        "view_artifacts": view_artifacts,
        "semantic_views": filtered_semantic_views,
        "warnings": warnings,
        "errors": [],
    }

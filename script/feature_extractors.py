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
from typing import Any
import zipfile

try:
    from script.screening_runner import extract_layers_from_apk
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
    from screening_runner import extract_layers_from_apk
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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:{}".format(digest.hexdigest())


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


def _normalize_requested_views(requested_views: object) -> list[str]:
    if requested_views is None:
        return list(ZIP_LIGHT_SUPPORTED_VIEWS)
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
        cache_status="not_used",
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


def run_zip_light_extractor(
    apk_path: str | Path,
    *,
    requested_views: object = None,
    profile_ref: object = None,
    representation_spec_ref: object = None,
    config_hash: object = None,
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
        cache_status="not_used",
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
                view_schema_version="zip-light-{}-v1".format(view),
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

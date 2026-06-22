#!/usr/bin/env python3
"""Executable v3.4 conformance checks for registered APK feature extractors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

try:
    from script.feature_extractors import (
        build_semantic_multiview_extractor_capability,
        build_zip_light_extractor_capability,
        run_semantic_multiview_extractor,
        run_zip_light_extractor,
    )
    from script.v3_4_contracts import (
        EXTRACTOR_CAPABILITY,
        EXTRACTOR_REGISTRY,
        EXTRACTOR_RUN_RECORD,
        STATUS_ANALYSIS_FAILED,
        STATUS_PARTIAL_RESULT,
        STATUS_SUCCESS,
        STATUS_UNSUPPORTED_INPUT,
        VIEW_ARTIFACT_RECORD,
        build_extractor_registry,
    )
except ImportError:  # pragma: no cover - direct script import fallback
    from feature_extractors import (
        build_semantic_multiview_extractor_capability,
        build_zip_light_extractor_capability,
        run_semantic_multiview_extractor,
        run_zip_light_extractor,
    )
    from v3_4_contracts import (
        EXTRACTOR_CAPABILITY,
        EXTRACTOR_REGISTRY,
        EXTRACTOR_RUN_RECORD,
        STATUS_ANALYSIS_FAILED,
        STATUS_PARTIAL_RESULT,
        STATUS_SUCCESS,
        STATUS_UNSUPPORTED_INPUT,
        VIEW_ARTIFACT_RECORD,
        build_extractor_registry,
    )


_REQUIRED_STATUS_SET = {
    STATUS_SUCCESS,
    STATUS_PARTIAL_RESULT,
    STATUS_ANALYSIS_FAILED,
    STATUS_UNSUPPORTED_INPUT,
}
_REQUIRED_CACHE_KEY_FIELDS = {
    "apk_sha256",
    "extractor_id",
    "extractor_version",
    "mode",
}
_ALLOWED_CACHE_STATUS = {
    "hit",
    "miss",
    "not_used",
    "cache_incompatible",
}


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _extractor_specs() -> list[dict[str, Any]]:
    return [
        {
            "capability_builder": build_zip_light_extractor_capability,
            "runner": run_zip_light_extractor,
            "requested_views": ["code", "resource", "metadata"],
            "payload_key": "layers",
            "mode": "light",
        },
        {
            "capability_builder": build_semantic_multiview_extractor_capability,
            "runner": run_semantic_multiview_extractor,
            "requested_views": [
                "R_code_identity",
                "R_code_stats",
                "R_code_packaging",
            ],
            "payload_key": "semantic_views",
            "mode": "deep",
        },
    ]


def build_default_extractor_registry() -> dict[str, Any]:
    """Build the current v3.4 extractor registry from executable wrappers."""
    capabilities = [
        spec["capability_builder"]()
        for spec in _extractor_specs()
    ]
    return build_extractor_registry(capabilities)


def validate_extractor_capability(capability: dict[str, Any]) -> list[str]:
    """Validate one ExtractorCapability record."""
    errors: list[str] = []
    extractor_id = capability.get("extractor_id", "extractor-unknown")
    prefix = "capability:{}".format(extractor_id)

    if capability.get("record_type") != EXTRACTOR_CAPABILITY:
        errors.append("{}:invalid_record_type".format(prefix))
    if not _is_non_empty_string(capability.get("extractor_id")):
        errors.append("{}:missing_extractor_id".format(prefix))
    if not _is_non_empty_string(capability.get("extractor_version")):
        errors.append("{}:missing_extractor_version".format(prefix))
    if not _as_list(capability.get("supported_views")):
        errors.append("{}:missing_supported_views".format(prefix))
    if not _as_list(capability.get("supported_modes")):
        errors.append("{}:missing_supported_modes".format(prefix))

    status_set = set(_as_list(capability.get("status_set")))
    for status in sorted(_REQUIRED_STATUS_SET):
        if status not in status_set:
            errors.append("{}:missing_status:{}".format(prefix, status))

    cache_key_fields = set(_as_list(capability.get("cache_key_fields")))
    for field in sorted(_REQUIRED_CACHE_KEY_FIELDS):
        if field not in cache_key_fields:
            errors.append("{}:missing_cache_key_field:{}".format(prefix, field))

    if not isinstance(capability.get("cost"), dict):
        errors.append("{}:missing_cost".format(prefix))

    return errors


def validate_extractor_run_result(
    result: dict[str, Any],
    capability: dict[str, Any],
    *,
    payload_key: str | None = None,
) -> list[str]:
    """Validate one extractor run result against its capability."""
    errors: list[str] = []
    extractor_id = str(capability.get("extractor_id") or "extractor-unknown")
    prefix = "run:{}".format(extractor_id)
    run_record = result.get("extractor_run_record")

    if not isinstance(run_record, dict):
        return ["{}:missing_extractor_run_record".format(prefix)]

    status_set = set(_as_list(capability.get("status_set")))
    supported_modes = set(_as_list(capability.get("supported_modes")))
    supported_views = set(_as_list(capability.get("supported_views")))
    status = result.get("status")

    if run_record.get("record_type") != EXTRACTOR_RUN_RECORD:
        errors.append("{}:invalid_run_record_type".format(prefix))
    if run_record.get("extractor_id") != capability.get("extractor_id"):
        errors.append("{}:extractor_id_mismatch".format(prefix))
    if run_record.get("extractor_version") != capability.get("extractor_version"):
        errors.append("{}:extractor_version_mismatch".format(prefix))
    if status != run_record.get("status"):
        errors.append("{}:status_mismatch".format(prefix))
    if status not in status_set:
        errors.append("{}:unsupported_status:{}".format(prefix, status))
    if run_record.get("mode") not in supported_modes:
        errors.append("{}:unsupported_mode:{}".format(prefix, run_record.get("mode")))
    if not _is_non_empty_string(run_record.get("run_id")):
        errors.append("{}:missing_run_id".format(prefix))
    if not _is_non_empty_string(run_record.get("apk_sha256")):
        errors.append("{}:missing_apk_sha256".format(prefix))
    if not _is_non_empty_string(run_record.get("cache_key")):
        errors.append("{}:missing_cache_key".format(prefix))
    cache_status = run_record.get("cache_status")
    if cache_status not in _ALLOWED_CACHE_STATUS:
        errors.append("{}:unsupported_cache_status:{}".format(prefix, cache_status))

    requested_views = _as_list(run_record.get("requested_views"))
    produced_views = _as_list(run_record.get("produced_views"))
    for view in requested_views:
        if view not in supported_views:
            errors.append("{}:requested_unsupported_view:{}".format(prefix, view))
    for view in produced_views:
        if view not in requested_views:
            errors.append("{}:produced_unrequested_view:{}".format(prefix, view))
        if view not in supported_views:
            errors.append("{}:produced_unsupported_view:{}".format(prefix, view))

    artifacts = _as_list(result.get("view_artifacts"))
    artifact_views = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("{}:invalid_artifact_record".format(prefix))
            continue
        view_type = artifact.get("view_type")
        artifact_prefix = "{}:artifact:{}".format(prefix, view_type or "unknown_view")
        artifact_views.append(view_type)
        if artifact.get("record_type") != VIEW_ARTIFACT_RECORD:
            errors.append("{}:invalid_record_type".format(artifact_prefix))
        if view_type not in produced_views:
            errors.append("{}:view_not_in_produced_views".format(artifact_prefix))
        if artifact.get("extractor_run_ref") != run_record.get("run_id"):
            errors.append("{}:missing_extractor_run_ref".format(artifact_prefix))
        artifact_run = artifact.get("extractor_run_record")
        if not isinstance(artifact_run, dict):
            errors.append("{}:missing_extractor_run_record".format(artifact_prefix))
        elif artifact_run.get("run_id") != run_record.get("run_id"):
            errors.append("{}:extractor_run_record_mismatch".format(artifact_prefix))

    for view in produced_views:
        if view not in artifact_views:
            errors.append("{}:missing_artifact_for_view:{}".format(prefix, view))

    if payload_key is not None and not isinstance(result.get(payload_key), dict):
        errors.append("{}:missing_payload:{}".format(prefix, payload_key))
    if not isinstance(result.get("warnings"), list):
        errors.append("{}:warnings_not_list".format(prefix))
    if not isinstance(result.get("errors"), list):
        errors.append("{}:errors_not_list".format(prefix))

    return errors


def _run_spec(
    *,
    spec: dict[str, Any],
    apk_path: str | Path,
    profile_ref: object,
    representation_spec_ref: object,
    config_hash: object,
) -> dict[str, Any]:
    runner: Callable[..., dict[str, Any]] = spec["runner"]
    kwargs = {
        "requested_views": spec["requested_views"],
        "profile_ref": profile_ref,
        "representation_spec_ref": representation_spec_ref,
        "config_hash": config_hash,
    }
    if spec["mode"] != "light":
        kwargs["mode"] = spec["mode"]
    return runner(apk_path, **kwargs)


def run_extractor_conformance_check(
    apk_path: str | Path,
    *,
    profile_ref: object = None,
    representation_spec_ref: object = None,
    config_hash: object = None,
) -> dict[str, Any]:
    """Run all registered extractors and validate their v3.4 records."""
    checked_extractors = []
    all_errors: list[str] = []
    registry = build_default_extractor_registry()

    if registry.get("record_type") != EXTRACTOR_REGISTRY:
        all_errors.append("registry:invalid_record_type")

    for spec in _extractor_specs():
        capability = spec["capability_builder"]()
        capability_errors = validate_extractor_capability(capability)
        result = _run_spec(
            spec=spec,
            apk_path=apk_path,
            profile_ref=profile_ref,
            representation_spec_ref=representation_spec_ref,
            config_hash=config_hash,
        )
        run_errors = validate_extractor_run_result(
            result,
            capability,
            payload_key=spec["payload_key"],
        )
        errors = capability_errors + run_errors
        all_errors.extend(errors)
        run_record = result.get("extractor_run_record", {})
        checked_extractors.append(
            {
                "extractor_id": capability.get("extractor_id"),
                "extractor_version": capability.get("extractor_version"),
                "mode": run_record.get("mode"),
                "status": "success" if not errors else "failed",
                "run_status": result.get("status"),
                "requested_views": run_record.get("requested_views", []),
                "produced_views": run_record.get("produced_views", []),
                "errors": errors,
            }
        )

    return {
        "record_type": "ExtractorConformanceReport",
        "status": "success" if not all_errors else "failed",
        "extractor_registry": registry,
        "extractor_count": len(checked_extractors),
        "checked_extractors": checked_extractors,
        "errors": all_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v3.4 conformance checks for registered APK feature extractors.",
    )
    parser.add_argument("apk_path", help="APK used as a small conformance input")
    parser.add_argument("--profile-ref", default=None)
    parser.add_argument("--representation-spec-ref", default=None)
    parser.add_argument("--config-hash", default=None)
    args = parser.parse_args(argv)

    report = run_extractor_conformance_check(
        args.apk_path,
        profile_ref=args.profile_ref,
        representation_spec_ref=args.representation_spec_ref,
        config_hash=args.config_hash,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

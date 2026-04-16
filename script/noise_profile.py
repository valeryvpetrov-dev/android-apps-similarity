#!/usr/bin/env python3
"""NoiseProfileEnvelope — runtime carrier for noise cleanup output.

Schema version: nc-v1
Canonical reference: experiments/artifacts/E-NC-002/noise-profile-arbitration-schema.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


SCHEMA_VERSION = "nc-v1"

# Detector source constants (from arbitration ladder)
DETECTOR_LIBRARY_VIEW_V2 = "library_view_v2"
DETECTOR_PREFIX_CATALOG_V1 = "prefix_catalog_v1"
DETECTOR_OFFLINE_PROFILE_FINGERPRINT_V1 = "offline_profile_fingerprint_v1"

# Status constants
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"

# Confidence constants
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass
class NoiseProfileEnvelope:
    """Compact carrier for noise cleanup arbitration result.

    Survives all four pipeline stages:
      noise_cleanup -> representation -> screening -> deep_verification
    """

    schema_version: str = SCHEMA_VERSION
    detector_source: str = DETECTOR_LIBRARY_VIEW_V2
    confidence: str = CONFIDENCE_HIGH
    status: str = STATUS_SUCCESS
    noise_reason: str = ""
    downstream_warnings: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)


def build_noise_profile(apk_result: dict) -> NoiseProfileEnvelope:
    """Build NoiseProfileEnvelope from noise_normalizer build_payload() output.

    Args:
        apk_result: dict produced by noise_normalizer.build_payload().
                    Expected keys: apk_path, timestamp, elements, summary.

    Returns:
        NoiseProfileEnvelope populated from arbitration over apk_result.
    """
    summary: dict = apk_result.get("summary", {})
    elements: list = apk_result.get("elements", [])

    # Determine detector source based on available signals.
    # library_view_v2 is primary; fall back based on what summary contains.
    detector_source = _infer_detector_source(apk_result)

    # Derive status and confidence from summary counts.
    library_count = summary.get("library_like", 0)
    app_count = summary.get("app_specific", 0)
    unstable_count = summary.get("unstable_extraction_risk", 0)
    total_elements = sum(summary.values()) if summary else 0

    status, confidence, noise_reason, warnings = _arbitrate(
        library_count=library_count,
        app_count=app_count,
        unstable_count=unstable_count,
        total_elements=total_elements,
        detector_source=detector_source,
    )

    # Collect evidence refs from apk_result metadata.
    evidence_refs: List[str] = []
    apk_path = apk_result.get("apk_path", "")
    if apk_path:
        evidence_refs.append("apk_path:{}".format(apk_path))
    timestamp = apk_result.get("timestamp", "")
    if timestamp:
        evidence_refs.append("timestamp:{}".format(timestamp))

    return NoiseProfileEnvelope(
        schema_version=SCHEMA_VERSION,
        detector_source=detector_source,
        confidence=confidence,
        status=status,
        noise_reason=noise_reason,
        downstream_warnings=warnings,
        evidence_refs=evidence_refs,
    )


def _infer_detector_source(apk_result: dict) -> str:
    """Infer which detector produced the result based on metadata hints."""
    # Heuristic: if v2 detector fields are present in elements, use library_view_v2.
    # Otherwise fall back to prefix_catalog_v1 (the prefix-match detector).
    elements: list = apk_result.get("elements", [])
    for elem in elements:
        reason = elem.get("reason", "")
        # library_view_v2 produces reasons mentioning TPL or version-aware detection.
        if "tpl" in reason.lower() or "androguard" in reason.lower():
            return DETECTOR_LIBRARY_VIEW_V2
    # Default: prefix_catalog_v1 is the active prefix-based detector.
    return DETECTOR_PREFIX_CATALOG_V1


def _arbitrate(
    library_count: int,
    app_count: int,
    unstable_count: int,
    total_elements: int,
    detector_source: str,
) -> tuple:
    """Apply arbitration rules to produce status, confidence, reason, warnings.

    Returns:
        (status, confidence, noise_reason, downstream_warnings)
    """
    warnings: List[str] = []

    if total_elements == 0:
        return STATUS_BLOCKED, CONFIDENCE_LOW, "extraction_missing", ["fallback_used"]

    if unstable_count > 0:
        warnings.append("extraction_unstable")

    library_ratio = library_count / total_elements if total_elements > 0 else 0.0

    # Blocked: detector could not produce usable result.
    if library_count == 0 and unstable_count > 0:
        return STATUS_BLOCKED, CONFIDENCE_LOW, "extraction_missing", warnings + ["fallback_used"]

    # Success path: clear library detection.
    if library_ratio >= 0.5:
        if unstable_count == 0:
            noise_reason = "library_detected"
            confidence = CONFIDENCE_HIGH
        else:
            noise_reason = "library_ambiguous"
            confidence = CONFIDENCE_MEDIUM
            warnings.append("ambiguous_library_attribution")
        return STATUS_SUCCESS, confidence, noise_reason, warnings

    # Partial path: some library signal, not dominant.
    if library_ratio > 0.0:
        warnings.append("ambiguous_library_attribution")
        if detector_source != DETECTOR_LIBRARY_VIEW_V2:
            warnings.append("fallback_used")
            if detector_source == DETECTOR_PREFIX_CATALOG_V1:
                warnings.append("catalog_only")
        return STATUS_PARTIAL, CONFIDENCE_MEDIUM, "library_ambiguous", warnings

    # App-specific: no library detection.
    if detector_source != DETECTOR_LIBRARY_VIEW_V2:
        warnings.append("fallback_used")
    return STATUS_SUCCESS, CONFIDENCE_MEDIUM, "app_specific_dominant", warnings


def to_dict(envelope: NoiseProfileEnvelope) -> Dict[str, object]:
    """Serialize NoiseProfileEnvelope to a plain dict for JSON output."""
    return {
        "schema_version": envelope.schema_version,
        "detector_source": envelope.detector_source,
        "confidence": envelope.confidence,
        "status": envelope.status,
        "noise_reason": envelope.noise_reason,
        "downstream_warnings": list(envelope.downstream_warnings),
        "evidence_refs": list(envelope.evidence_refs),
    }

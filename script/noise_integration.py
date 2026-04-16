#!/usr/bin/env python3
"""Noise integration functions for pipeline stages (NC-003).

Provides three integration points that thread NoiseProfileEnvelope
through the similarity pipeline:

  Step 2 — inject_noise_into_screening_pair(pair, envelope)
  Step 3 — propagate_noise_to_pairwise(result, envelope)
  Step 4 — add_noise_context_to_explanation(explanation, envelope)

All functions are pure (non-destructive): they return a new dict
rather than mutating the input.

Canonical reference: NC-003-REPO
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from script.noise_profile_envelope import (
        NoiseProfileEnvelope,
        NOISE_STATUS_CLEAN,
        NOISE_STATUS_NOISY,
        NOISE_STATUS_UNKNOWN,
        STATUS_BLOCKED,
        STATUS_FAILED,
        STATUS_PARTIAL,
        to_dict,
    )
except ImportError:
    from noise_profile_envelope import (  # type: ignore[no-redef]
        NoiseProfileEnvelope,
        NOISE_STATUS_CLEAN,
        NOISE_STATUS_NOISY,
        NOISE_STATUS_UNKNOWN,
        STATUS_BLOCKED,
        STATUS_FAILED,
        STATUS_PARTIAL,
        to_dict,
    )


# ---------------------------------------------------------------------------
# Step 2: Inject noise into screening pair
# ---------------------------------------------------------------------------

def inject_noise_into_screening_pair(
    pair: Dict[str, Any],
    envelope: NoiseProfileEnvelope,
) -> Dict[str, Any]:
    """Inject NoiseProfileEnvelope data into a screening candidate pair dict.

    Merges downstream_warnings into 'screening_warnings' and sets
    'screening_explanation' with noise_profile_ref and noise_status.

    Args:
        pair:     Screening candidate pair dict (from build_candidate_list).
        envelope: NoiseProfileEnvelope from arbitrate_detector().

    Returns:
        A new dict with noise fields merged. Original pair is not mutated.
    """
    result = dict(pair)

    # Merge warnings (deduplicated, preserving order).
    existing_warnings: List[str] = list(result.get("screening_warnings") or [])
    new_warnings = [w for w in envelope.downstream_warnings if w not in existing_warnings]
    result["screening_warnings"] = existing_warnings + new_warnings

    # Build noise_profile_ref compact string.
    noise_profile_ref = _build_noise_profile_ref(envelope)

    # Merge or create screening_explanation.
    explanation: Dict[str, Any] = dict(result.get("screening_explanation") or {})
    explanation["noise_profile_ref"] = noise_profile_ref
    explanation["noise_status"] = envelope.noise_status
    result["screening_explanation"] = explanation

    # Attach full envelope for downstream consumers.
    result["noise_profile_envelope"] = to_dict(envelope)

    return result


# ---------------------------------------------------------------------------
# Step 3: Propagate noise to pairwise result
# ---------------------------------------------------------------------------

def propagate_noise_to_pairwise(
    result: Dict[str, Any],
    envelope: NoiseProfileEnvelope,
) -> Dict[str, Any]:
    """Propagate NoiseProfileEnvelope into a pairwise analysis result dict.

    Sets noise_profile_ref in 'artifacts' and appends noise_status
    to 'run_context'. Merges downstream_warnings into result-level
    'pairwise_warnings' list.

    Args:
        result:   Pairwise detailed result dict (from build_detailed_result).
        envelope: NoiseProfileEnvelope to propagate.

    Returns:
        A new dict with noise fields merged. Original result is not mutated.
    """
    updated = dict(result)

    noise_profile_ref = _build_noise_profile_ref(envelope)

    # Update artifacts section.
    artifacts: Dict[str, Any] = dict(updated.get("artifacts") or {})
    artifacts["noise_profile_ref"] = noise_profile_ref
    artifacts["noise_summary_ref"] = _build_noise_summary_ref(envelope)
    updated["artifacts"] = artifacts

    # Update run_context section.
    run_context: Dict[str, Any] = dict(updated.get("run_context") or {})
    run_context["noise_status"] = envelope.noise_status
    run_context["noise_detector_source"] = envelope.detector_source
    updated["run_context"] = run_context

    # Merge warnings into pairwise_warnings.
    existing_warnings: List[str] = list(updated.get("pairwise_warnings") or [])
    new_warnings = [w for w in envelope.downstream_warnings if w not in existing_warnings]
    updated["pairwise_warnings"] = existing_warnings + new_warnings

    return updated


# ---------------------------------------------------------------------------
# Step 4: Add noise context to explanation
# ---------------------------------------------------------------------------

def add_noise_context_to_explanation(
    explanation: Dict[str, Any],
    envelope: NoiseProfileEnvelope,
) -> Dict[str, Any]:
    """Add noise context block to a pairwise explanation dict.

    Adds a 'noise_context' sub-dict with detector metadata and
    optional warnings if noise status is non-clean or status is
    blocked/partial.

    Args:
        explanation: Explanation dict (from build_detailed_explanation).
        envelope:    NoiseProfileEnvelope to embed.

    Returns:
        A new dict with 'noise_context' added. Original is not mutated.
    """
    updated = dict(explanation)

    noise_context: Dict[str, Any] = {
        "noise_status": envelope.noise_status,
        "detector_source": envelope.detector_source,
        "confidence": envelope.confidence,
        "noise_reason": envelope.noise_reason,
        "schema_version": envelope.schema_version,
    }

    # Include warnings only when they carry signal.
    if envelope.downstream_warnings:
        noise_context["warnings"] = list(envelope.downstream_warnings)

    # Flag reliability issues.
    reliability_degraded = (
        envelope.status in (STATUS_BLOCKED, STATUS_FAILED, STATUS_PARTIAL)
        or envelope.noise_status == NOISE_STATUS_UNKNOWN
        or bool(envelope.downstream_warnings)
    )
    noise_context["reliability_degraded"] = reliability_degraded

    updated["noise_context"] = noise_context
    return updated


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_noise_profile_ref(envelope: NoiseProfileEnvelope) -> str:
    """Build compact noise_profile_ref string from envelope."""
    return "noise_profile:{}:{}:{}".format(
        envelope.schema_version,
        envelope.detector_source,
        envelope.status,
    )


def _build_noise_summary_ref(envelope: NoiseProfileEnvelope) -> str:
    """Build compact noise_summary_ref string including noise_status."""
    return "noise_summary:{}:{}:{}".format(
        envelope.schema_version,
        envelope.noise_status,
        envelope.confidence,
    )

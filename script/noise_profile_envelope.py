#!/usr/bin/env python3
"""NoiseProfileEnvelope — extended carrier for noise cleanup output (NC-003).

Schema version: nc-v1
Extends noise_profile.py with:
  - from_dict() deserializer
  - arbitrate_detector(apk_record) — ladder: library_view_v2 → prefix_catalog_v1
    → offline_profile_fingerprint_v1
  - Noise status vocabulary: "clean" / "noisy" / "unknown"
    (parallel to existing status: success/partial/blocked)

Canonical reference: NC-003-REPO
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Schema & constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "nc-v1"

# Detector sources (arbitration ladder order)
DETECTOR_LIBRARY_VIEW_V2 = "library_view_v2"
DETECTOR_PREFIX_CATALOG_V1 = "prefix_catalog_v1"
DETECTOR_OFFLINE_PROFILE_FINGERPRINT_V1 = "offline_profile_fingerprint_v1"

DETECTOR_LADDER = (
    DETECTOR_LIBRARY_VIEW_V2,
    DETECTOR_PREFIX_CATALOG_V1,
    DETECTOR_OFFLINE_PROFILE_FINGERPRINT_V1,
)

# Pipeline status (execution completeness)
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"

# Noise verdict (semantic result — "is this APK noisy?")
NOISE_STATUS_CLEAN = "clean"
NOISE_STATUS_NOISY = "noisy"
NOISE_STATUS_UNKNOWN = "unknown"

# Confidence
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class NoiseProfileEnvelope:
    """Compact carrier for noise cleanup arbitration result.

    Survives all four pipeline stages:
      noise_cleanup -> representation -> screening -> deep_verification

    Fields:
        schema_version:       Protocol version string (nc-v1).
        detector_source:      Which detector produced the result.
        confidence:           high / medium / low.
        status:               Execution completeness: success / partial / blocked.
        noise_reason:         Short token explaining the noise verdict.
        downstream_warnings:  List of warning tokens for downstream consumers.
        evidence_refs:        List of reference strings (apk_path, timestamp, etc.).
        noise_status:         Semantic verdict: clean / noisy / unknown.
    """

    schema_version: str = SCHEMA_VERSION
    detector_source: str = DETECTOR_LIBRARY_VIEW_V2
    confidence: str = CONFIDENCE_HIGH
    status: str = STATUS_SUCCESS
    noise_reason: str = ""
    downstream_warnings: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    noise_status: str = NOISE_STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

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
        "noise_status": envelope.noise_status,
    }


def from_dict(data: dict) -> NoiseProfileEnvelope:
    """Deserialize NoiseProfileEnvelope from a plain dict.

    Tolerant: unknown keys are ignored; missing keys use dataclass defaults.
    """
    if not isinstance(data, dict):
        raise TypeError("from_dict expects a dict, got {!r}".format(type(data).__name__))

    return NoiseProfileEnvelope(
        schema_version=data.get("schema_version", SCHEMA_VERSION),
        detector_source=data.get("detector_source", DETECTOR_LIBRARY_VIEW_V2),
        confidence=data.get("confidence", CONFIDENCE_HIGH),
        status=data.get("status", STATUS_SUCCESS),
        noise_reason=data.get("noise_reason", ""),
        downstream_warnings=list(data.get("downstream_warnings") or []),
        evidence_refs=list(data.get("evidence_refs") or []),
        noise_status=data.get("noise_status", NOISE_STATUS_UNKNOWN),
    )


# ---------------------------------------------------------------------------
# Arbitration ladder
# ---------------------------------------------------------------------------

def arbitrate_detector(apk_record: dict) -> NoiseProfileEnvelope:
    """Run the detector arbitration ladder on an apk_record.

    Ladder priority (highest to lowest):
      1. library_view_v2    — TPL package-set Jaccard fingerprint
      2. prefix_catalog_v1  — prefix-match catalog
      3. offline_profile_fingerprint_v1 — static offline fingerprint

    The first detector that yields a usable result wins.
    Falls back to an UNKNOWN envelope if none succeed.

    Args:
        apk_record: dict from noise_normalizer.build_payload() or compatible
                    structure with keys: apk_path, elements, summary.

    Returns:
        NoiseProfileEnvelope populated by the winning detector.
    """
    # --- Step 1: try library_view_v2 ---
    result = _try_library_view_v2(apk_record)
    if result is not None:
        return result

    # --- Step 2: try prefix_catalog_v1 ---
    result = _try_prefix_catalog_v1(apk_record)
    if result is not None:
        return result

    # --- Step 3: try offline_profile_fingerprint_v1 ---
    result = _try_offline_profile_fingerprint_v1(apk_record)
    if result is not None:
        return result

    # --- Fallback: no detector succeeded ---
    return NoiseProfileEnvelope(
        schema_version=SCHEMA_VERSION,
        detector_source=DETECTOR_LIBRARY_VIEW_V2,
        confidence=CONFIDENCE_LOW,
        status=STATUS_BLOCKED,
        noise_reason="no_detector_succeeded",
        downstream_warnings=["fallback_used", "all_detectors_failed"],
        evidence_refs=_collect_evidence_refs(apk_record),
        noise_status=NOISE_STATUS_UNKNOWN,
    )


# ---------------------------------------------------------------------------
# Private detector implementations
# ---------------------------------------------------------------------------

def _collect_evidence_refs(apk_record: dict) -> List[str]:
    refs: List[str] = []
    apk_path = apk_record.get("apk_path", "")
    if apk_path:
        refs.append("apk_path:{}".format(apk_path))
    timestamp = apk_record.get("timestamp", "")
    if timestamp:
        refs.append("timestamp:{}".format(timestamp))
    return refs


def _try_library_view_v2(apk_record: dict) -> Optional[NoiseProfileEnvelope]:
    """Try library_view_v2 detector via apk_record elements/summary.

    Succeeds when at least one element has a TPL-related reason
    (indicator that androguard-based detection ran).
    """
    elements: list = apk_record.get("elements", [])
    summary: dict = apk_record.get("summary", {})

    # Check for library_view_v2 signal: TPL or androguard mentions in reasons.
    has_v2_signal = any(
        "tpl" in str(e.get("reason", "")).lower()
        or "androguard" in str(e.get("reason", "")).lower()
        for e in elements
    )
    if not has_v2_signal:
        return None

    return _build_envelope_from_summary(
        summary=summary,
        elements=elements,
        detector_source=DETECTOR_LIBRARY_VIEW_V2,
        evidence_refs=_collect_evidence_refs(apk_record),
    )


def _try_prefix_catalog_v1(apk_record: dict) -> Optional[NoiseProfileEnvelope]:
    """Try prefix_catalog_v1 detector.

    Succeeds when elements are present and contain library_like entries,
    indicating prefix-match catalog was used (no TPL signal needed).
    """
    elements: list = apk_record.get("elements", [])
    summary: dict = apk_record.get("summary", {})

    if not elements:
        return None

    library_count = summary.get("library_like", 0)
    if library_count == 0:
        return None

    return _build_envelope_from_summary(
        summary=summary,
        elements=elements,
        detector_source=DETECTOR_PREFIX_CATALOG_V1,
        evidence_refs=_collect_evidence_refs(apk_record),
    )


def _try_offline_profile_fingerprint_v1(apk_record: dict) -> Optional[NoiseProfileEnvelope]:
    """Try offline_profile_fingerprint_v1 detector.

    Succeeds when apk_record contains an explicit 'offline_fingerprint' key
    or when any elements exist (last resort fallback within ladder).
    """
    elements: list = apk_record.get("elements", [])
    summary: dict = apk_record.get("summary", {})
    has_fingerprint = bool(apk_record.get("offline_fingerprint"))

    if not has_fingerprint and not elements:
        return None

    warnings = ["fallback_used", "offline_fingerprint_v1_used"]

    if has_fingerprint:
        noise_status = NOISE_STATUS_NOISY if apk_record.get("offline_fingerprint", {}).get("is_noisy") else NOISE_STATUS_CLEAN
        confidence = CONFIDENCE_MEDIUM
        status = STATUS_SUCCESS
        noise_reason = "offline_fingerprint_match"
    else:
        # Elements present but no clear library signal — build from summary as last resort.
        envelope = _build_envelope_from_summary(
            summary=summary,
            elements=elements,
            detector_source=DETECTOR_OFFLINE_PROFILE_FINGERPRINT_V1,
            evidence_refs=_collect_evidence_refs(apk_record),
        )
        envelope.downstream_warnings = list(set(envelope.downstream_warnings + warnings))
        return envelope

    return NoiseProfileEnvelope(
        schema_version=SCHEMA_VERSION,
        detector_source=DETECTOR_OFFLINE_PROFILE_FINGERPRINT_V1,
        confidence=confidence,
        status=status,
        noise_reason=noise_reason,
        downstream_warnings=warnings,
        evidence_refs=_collect_evidence_refs(apk_record),
        noise_status=noise_status,
    )


# ---------------------------------------------------------------------------
# APKiD gate integration (EXEC-083-APKID-ADAPTER, hard policy D-2026-04-19)
# ---------------------------------------------------------------------------

def apply_apkid_gate(apkid_result: dict, envelope: dict) -> dict:
    """Встраивает результат gate-решения в profile envelope.

    Если gate_status='blocked', добавляет в envelope пометку
    `detector_blocked=True` и `detector_block_reason='packer_detected'`.
    Остальные случаи записываются как informational (recommended_detector).

    Для gate_status='manipulator_detected' (APKID-ADAPTER-EXT) в envelope
    дополнительно записывается `manipulator_warning: list[str]` со списком
    конкретных manipulator-правил APKiD. detector_blocked при этом
    НЕ выставляется — detector остаётся работать по recommended=libloom.

    Args:
        apkid_result: dict из apkid_adapter.decide_gate(...).
        envelope: dict-представление NoiseProfileEnvelope (из to_dict)
                  или произвольный dict с metadata.

    Returns:
        Новый dict, расширенный полями:
          - apkid_gate_status
          - apkid_recommended_detector
          - apkid_reason
          - apkid_signals
          - detector_blocked        (только при blocked)
          - detector_block_reason   (только при blocked)
          - manipulator_warning     (только при manipulator_detected)
    """
    if not isinstance(envelope, dict):
        raise TypeError("apply_apkid_gate expects envelope as dict")

    merged = dict(envelope)
    gate_status = apkid_result.get("gate_status")
    recommended = apkid_result.get("recommended_detector")
    reason = apkid_result.get("reason", "")
    signals = dict(apkid_result.get("apkid_signals", {}) or {})

    merged["apkid_gate_status"] = gate_status
    merged["apkid_recommended_detector"] = recommended
    merged["apkid_reason"] = reason
    merged["apkid_signals"] = signals

    if gate_status == "blocked":
        merged["detector_blocked"] = True
        merged["detector_block_reason"] = "packer_detected"

    if gate_status == "manipulator_detected":
        manipulators = list(signals.get("manipulators", []) or [])
        merged["manipulator_warning"] = manipulators

    return merged


# ---------------------------------------------------------------------------
# LIBLOOM detector integration (EXEC-083-LIBLOOM-INTEGRATION)
# ---------------------------------------------------------------------------

def apply_libloom_detection(
    apk_path: str,
    apkid_result: dict,
    libloom_jar_path: Optional[str],
    libs_profile_dir: Optional[str],
    envelope: dict,
    timeout_sec: int = 600,
) -> dict:
    """Встраивает результат LIBLOOM в профиль шума, если политика APKiD позволяет.

    Правила:
      - Если apkid_result["gate_status"] == "blocked" → LIBLOOM НЕ вызывается,
        envelope возвращается как есть (detector_blocked уже выставлен
        функцией apply_apkid_gate).
      - Если среда LIBLOOM недоступна или сломана → LIBLOOM не вызывается,
        envelope получает libloom_status="libloom_unavailable" либо
        "libloom_misconfigured" с явной причиной.
      - Иначе вызывается libloom_adapter.detect_libraries, результат пишется
        в envelope:
          * envelope["libloom_status"]         = result["status"]
          * envelope["libloom_libraries"]      = result["libraries"]
          * envelope["libloom_elapsed_sec"]    = result["elapsed_sec"]
          * envelope["libloom_error_reason"]   = result.get("error_reason")

    Функция никогда не бросает исключения (как и сам adapter — он
    маппит любое исключение в статус внутри результата).

    Args:
        apk_path:          путь к APK-файлу для анализа.
        apkid_result:      dict из apkid_adapter.decide_gate(...).
        libloom_jar_path:  путь к LIBLOOM.jar либо None (не сконфигурировано).
        libs_profile_dir:  каталог с prebuilt TPL-профилями либо None.
        envelope:          dict-представление NoiseProfileEnvelope (или
                           произвольный dict с metadata).
        timeout_sec:       hard-timeout для каждой фазы LIBLOOM (default 600).

    Returns:
        Новый dict — копия envelope, расширенная libloom_*-полями
        (если LIBLOOM был вызван), либо без них (при blocked). Для
        unavailable/misconfigured добавляются явные libloom_* поля.
    """
    if not isinstance(envelope, dict):
        raise TypeError("apply_libloom_detection expects envelope as dict")

    merged = dict(envelope)

    # 1. Жёсткая политика blocked — LIBLOOM не вызывается.
    gate_status = apkid_result.get("gate_status") if isinstance(apkid_result, dict) else None
    if gate_status == "blocked":
        return merged

    # 2. Явная проверка политики зависимости.
    # TODO(NOISE-21-DEPENDENCY-POLICY): keep status mapping in sync with
    # README.md#libloom-dependency-policy.
    # 3. Вызов LIBLOOM. Импорт внутри — чтобы не создавать циклическую
    #    зависимость на уровне модуля и чтобы тесты, мокающие
    #    libloom_adapter.detect_libraries, работали через патч модуля.
    from script import libloom_adapter  # noqa: WPS433

    verification = libloom_adapter.verify_libloom_setup(
        jar_path=libloom_jar_path,
        libs_profile_dir=libs_profile_dir,
    )
    if verification["status"] != "available":
        merged["libloom_status"] = "libloom_{}".format(verification["status"])
        merged["libloom_libraries"] = []
        merged["libloom_elapsed_sec"] = 0.0
        merged["libloom_error_reason"] = verification["reason"]
        return merged

    result = libloom_adapter.detect_libraries(
        apk_path=apk_path,
        jar_path=str(verification["jar_path"]),
        libs_profile_dir=str(verification["libs_profile_dir"]),
        timeout_sec=timeout_sec,
    )

    merged["libloom_status"] = result.get("status")
    merged["libloom_libraries"] = list(result.get("libraries") or [])
    merged["libloom_elapsed_sec"] = result.get("elapsed_sec", 0.0)
    merged["libloom_error_reason"] = result.get("error_reason")
    return merged


def _build_envelope_from_summary(
    summary: dict,
    elements: list,
    detector_source: str,
    evidence_refs: List[str],
) -> NoiseProfileEnvelope:
    """Build a NoiseProfileEnvelope from a summary dict.

    Shared logic for multiple detectors.
    """
    library_count = summary.get("library_like", 0)
    app_count = summary.get("app_specific", 0)
    unstable_count = summary.get("unstable_extraction_risk", 0)
    total_elements = sum(summary.values()) if summary else len(elements)

    warnings: List[str] = []
    if unstable_count > 0:
        warnings.append("extraction_unstable")

    if total_elements == 0:
        return NoiseProfileEnvelope(
            schema_version=SCHEMA_VERSION,
            detector_source=detector_source,
            confidence=CONFIDENCE_LOW,
            status=STATUS_BLOCKED,
            noise_reason="extraction_missing",
            downstream_warnings=["fallback_used"],
            evidence_refs=evidence_refs,
            noise_status=NOISE_STATUS_UNKNOWN,
        )

    library_ratio = library_count / total_elements if total_elements > 0 else 0.0

    # Determine noise_status semantic verdict.
    if library_ratio >= 0.5:
        noise_status = NOISE_STATUS_NOISY
        if unstable_count == 0:
            confidence = CONFIDENCE_HIGH
            noise_reason = "library_detected"
            status = STATUS_SUCCESS
        else:
            confidence = CONFIDENCE_MEDIUM
            noise_reason = "library_ambiguous"
            status = STATUS_SUCCESS
            warnings.append("ambiguous_library_attribution")
    elif library_ratio > 0.0:
        noise_status = NOISE_STATUS_UNKNOWN
        confidence = CONFIDENCE_MEDIUM
        noise_reason = "library_ambiguous"
        status = STATUS_PARTIAL
        warnings.append("ambiguous_library_attribution")
        if detector_source != DETECTOR_LIBRARY_VIEW_V2:
            warnings.append("fallback_used")
            if detector_source == DETECTOR_PREFIX_CATALOG_V1:
                warnings.append("catalog_only")
    else:
        noise_status = NOISE_STATUS_CLEAN
        confidence = CONFIDENCE_MEDIUM
        noise_reason = "app_specific_dominant"
        status = STATUS_SUCCESS
        if detector_source != DETECTOR_LIBRARY_VIEW_V2:
            warnings.append("fallback_used")

    return NoiseProfileEnvelope(
        schema_version=SCHEMA_VERSION,
        detector_source=detector_source,
        confidence=confidence,
        status=status,
        noise_reason=noise_reason,
        downstream_warnings=warnings,
        evidence_refs=evidence_refs,
        noise_status=noise_status,
    )

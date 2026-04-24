#!/usr/bin/env python3
"""Noise integration functions for pipeline stages (NC-003).

Provides three integration points that thread NoiseProfileEnvelope
through the similarity pipeline:

  Step 2 — inject_noise_into_screening_pair(pair, envelope)
  Step 3 — propagate_noise_to_pairwise(result, envelope)
  Step 4 — add_noise_context_to_explanation(explanation, envelope)

And, for NOISE-GATE-WIRING (wave 16 / runtime-truth fix):

  Entry gate — should_reject_by_noise_gate(app_record, reject_triggers)
  Trigger collector — collect_noise_gate_triggers(app_record)

All functions are pure (non-destructive): they return a new dict
rather than mutating the input.

Canonical reference: NC-003-REPO
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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
# Entry gate (NOISE-GATE-WIRING, wave 16): reject by envelope_triggers
# ---------------------------------------------------------------------------

_ADWARE_LIBRARY_PREFIXES = (
    "admob",
    "facebook_ads",
    "com.google.android.gms.ads",
    "com.google.ads",
    "com.facebook.ads",
    "com.unity3d.ads",
    "com.applovin",
    "com.mopub",
    "com.ironsource",
    "com.vungle",
    "com.chartboost",
    "com.inmobi",
    "com.adcolony",
    "com.tapjoy",
    "com.startapp",
    "com.bytedance.sdk.openadsdk",
    "com.smaato",
    "net.pubnative",
)


def _looks_like_adware_library(raw_name: Any) -> bool:
    """Return True when a LIBLOOM library name matches known ad SDKs."""
    if not raw_name:
        return False

    name = str(raw_name).strip().lower()
    if not name:
        return False

    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in _ADWARE_LIBRARY_PREFIXES
    )


def _collect_runtime_noise_gate_triggers(app_record: Dict[str, Any]) -> List[str]:
    """Derive noise-gate triggers from runtime APKiD/LIBLOOM fields."""
    envelope = app_record.get("noise_profile_envelope")
    if not isinstance(envelope, dict):
        return []

    triggers: List[str] = []

    gate_status = str(envelope.get("apkid_gate_status") or "").strip().lower()
    apkid_signals = envelope.get("apkid_signals")
    detector_blocked = bool(envelope.get("detector_blocked"))

    packers: List[str] = []
    if isinstance(apkid_signals, dict):
        raw_packers = apkid_signals.get("packers")
        if isinstance(raw_packers, (list, tuple)):
            packers = [str(p) for p in raw_packers if p]

    if gate_status == "blocked" or detector_blocked or packers:
        triggers.append("fake")

    libraries = envelope.get("libloom_libraries")
    if isinstance(libraries, (list, tuple)):
        for library in libraries:
            raw_name = library
            if isinstance(library, dict):
                raw_name = (
                    library.get("name")
                    or library.get("library")
                    or library.get("package")
                )
            if _looks_like_adware_library(raw_name):
                triggers.append("adware")
                break

    return triggers

def _collect_envelope_triggers(app_record: Dict[str, Any]) -> List[str]:
    """Собрать список envelope-triggers из app_record.

    Контракт чтения (порядок поиска):
      1. app_record["envelope_triggers"] — плоский список строк
         (канонический путь, заполняется слоем очистки шума).
      2. app_record["noise_profile_envelope"]["envelope_triggers"] —
         вложенный список (если envelope уже прикреплён к записи).
      3. app_record["noise_profile_envelope"]["downstream_warnings"] —
         fallback: downstream_warnings из envelope, когда отдельного
         поля envelope_triggers ещё не выставили (пересечение с
         reject_triggers даёт тот же семантический эффект).
      4. Runtime-derived triggers из ``noise_profile_envelope``:
         - ``fake``  — когда APKiD gate пометил APK как blocked/packer;
         - ``adware`` — когда LIBLOOM нашёл известный advertising SDK.

    Возвращает нормализованный список строк (пустой, если источников
    нет или они невалидны). Дубликаты сохраняются — порядок триггеров
    важен для диагностики (первый совпавший попадает в причину отказа).
    """
    triggers: List[str] = []

    raw = app_record.get("envelope_triggers")
    if isinstance(raw, (list, tuple)):
        triggers.extend(str(t) for t in raw if t)

    envelope = app_record.get("noise_profile_envelope")
    if isinstance(envelope, dict):
        nested = envelope.get("envelope_triggers")
        if isinstance(nested, (list, tuple)):
            triggers.extend(str(t) for t in nested if t)
        warnings = envelope.get("downstream_warnings")
        if isinstance(warnings, (list, tuple)):
            triggers.extend(str(t) for t in warnings if t)

    triggers.extend(_collect_runtime_noise_gate_triggers(app_record))
    return triggers


def collect_noise_gate_triggers(app_record: Dict[str, Any]) -> List[str]:
    """Public wrapper for the trigger sources used by noise-gate."""
    return _collect_envelope_triggers(app_record)


def should_reject_by_noise_gate(
    app_record: Dict[str, Any],
    reject_triggers: List[str],
) -> Tuple[bool, str]:
    """Решить, отклонять ли запись приложения на входе каскада.

    Первый фильтр каскада (NOISE-GATE-WIRING, P0 от критика
    noise-cleanup волны 14): если хотя бы один envelope-trigger
    записи входит в ``reject_triggers``, запись отклоняется.

    Args:
        app_record:       запись приложения (dict). Источники триггеров
                          описаны в _collect_envelope_triggers.
        reject_triggers:  список триггеров, по которым носим ярлык
                          «шумный APK — не пускать в каскад».

    Returns:
        (True, "noise_gate:<trigger>") — если пересечение найдено;
            <trigger> — первый из ``reject_triggers``, который
            встретился среди envelope-triggers записи
            (детерминированный порядок относительно конфигурации).
        (False, "") — если пересечение пусто (или reject_triggers пуст,
            или envelope-triggers отсутствуют).

    Функция не мутирует app_record.
    """
    if not reject_triggers:
        return (False, "")

    triggers = collect_noise_gate_triggers(app_record)
    if not triggers:
        return (False, "")

    triggers_set = set(triggers)
    # Порядок обхода — по reject_triggers (конфигурация задаёт
    # приоритет причин: первый из списка побеждает).
    for trigger in reject_triggers:
        if trigger in triggers_set:
            return (True, "noise_gate:{}".format(trigger))

    return (False, "")


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

#!/usr/bin/env python3
"""EXEC-088: единый формат записей доказательств Evidence.

Формат Evidence — канонический способ записать один сигнал одной пары
(слой с его per-layer score, signature_match и так далее), который
используется writer'ами первичного отбора (screening) и углублённого
сравнения (pairwise/signing).

Каждая запись — это dict со следующими ключами:

- source_stage: str — источник сигнала ("screening" | "pairwise" | "signing");
- signal_type:  str — тип сигнала ("layer_score" | "signature_match" |
  "library_match");
- magnitude:    float в [0, 1] — величина сигнала;
- ref:          str — стабильный указатель (имя слоя, "apk_signature" и т.п.).

Все helper-функции возвращают list[dict] или dict; dataclass Evidence
используется только для валидации на этапе конструирования записи.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Evidence:
    source_stage: str
    signal_type: str
    magnitude: float
    ref: str


def _validate_non_empty_string(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Evidence field {!r} must be a non-empty string".format(field_name)
        )
    return value


def _validate_magnitude(value: object) -> float:
    try:
        magnitude = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Evidence field 'magnitude' must be numeric in [0, 1]"
        ) from error
    if magnitude < 0.0 or magnitude > 1.0:
        raise ValueError(
            "Evidence field 'magnitude' must be in [0, 1], got {!r}".format(magnitude)
        )
    return magnitude


def make_evidence(
    source_stage: str, signal_type: str, magnitude: float, ref: str
) -> dict:
    """Построить Evidence как dict с валидацией.

    Валидирует: строки `source_stage`, `signal_type`, `ref` не пустые;
    `magnitude` численно в [0, 1]. Иначе поднимает ValueError.
    """
    source_stage = _validate_non_empty_string("source_stage", source_stage)
    signal_type = _validate_non_empty_string("signal_type", signal_type)
    ref = _validate_non_empty_string("ref", ref)
    magnitude_value = _validate_magnitude(magnitude)
    return asdict(
        Evidence(
            source_stage=source_stage,
            signal_type=signal_type,
            magnitude=magnitude_value,
            ref=ref,
        )
    )


def _clamp_unit(value: object) -> float:
    try:
        magnitude = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if magnitude < 0.0:
        return 0.0
    if magnitude > 1.0:
        return 1.0
    return magnitude


def collect_evidence_from_pairwise(pair_row: dict) -> list[dict]:
    """Построить список Evidence из pair_row.

    Правила:
    - если status=='analysis_failed' -> [];
    - для каждого слоя в views_used -> Evidence(source_stage='pairwise',
      signal_type='layer_score', magnitude=library_reduced_score (или
      full_similarity_score, если library_reduced_score is None), ref=layer);
    - если signature_match присутствует и status != 'analysis_failed' ->
      Evidence(source_stage='signing', signal_type='signature_match',
      magnitude=signature_match['score'], ref='apk_signature').

    magnitude в любом случае clamp'ается в [0, 1]; при отсутствии обоих
    scores слоевые записи пропускаются.
    """
    if not isinstance(pair_row, dict):
        return []
    if pair_row.get("status") == "analysis_failed":
        return []

    evidence: list[dict] = []

    views_used = pair_row.get("views_used")
    if isinstance(views_used, list):
        library_reduced = pair_row.get("library_reduced_score")
        full_similarity = pair_row.get("full_similarity_score")
        layer_score_source: object | None
        if library_reduced is not None:
            layer_score_source = library_reduced
        else:
            layer_score_source = full_similarity

        if layer_score_source is not None:
            magnitude = _clamp_unit(layer_score_source)
            for layer in views_used:
                if not isinstance(layer, str) or not layer.strip():
                    continue
                evidence.append(
                    make_evidence(
                        source_stage="pairwise",
                        signal_type="layer_score",
                        magnitude=magnitude,
                        ref=layer.strip(),
                    )
                )

    signature_match = pair_row.get("signature_match")
    if isinstance(signature_match, dict) and "score" in signature_match:
        magnitude = _clamp_unit(signature_match.get("score"))
        evidence.append(
            make_evidence(
                source_stage="signing",
                signal_type="signature_match",
                magnitude=magnitude,
                ref="apk_signature",
            )
        )

    return evidence


def collect_evidence_from_screening_layers(
    layers: dict[str, float], stage_name: str = "screening"
) -> list[dict]:
    """Построить Evidence из mapping 'имя слоя' -> per-layer score.

    Все score clamp'аются в [0, 1]. Пустые или не-строковые ключи
    игнорируются. `stage_name` по умолчанию 'screening'.
    """
    if not isinstance(layers, dict):
        return []

    stage = _validate_non_empty_string("stage_name", stage_name)

    evidence: list[dict] = []
    for layer, score in layers.items():
        if not isinstance(layer, str) or not layer.strip():
            continue
        magnitude = _clamp_unit(score)
        evidence.append(
            make_evidence(
                source_stage=stage,
                signal_type="layer_score",
                magnitude=magnitude,
                ref=layer.strip(),
            )
        )
    return evidence


def collect_all_evidence(
    screening_result: dict | None, pair_row: dict | None
) -> list[dict]:
    """Собрать объединённый Evidence с обоих этапов.

    EXEC-088-WRITERS: reader для интерпретации результата. Берёт уже
    записанное поле `evidence` из `screening_result` (писатель
    `screening_runner`) и из `pair_row` (писатель `pairwise_runner`),
    сохраняет `source_stage` ('screening'/'pairwise'/'signing') каждой
    записи и дедуплицирует по ключу (source_stage, signal_type, ref).
    При дубликатах сохраняется первая встреченная запись.

    Оба аргумента могут быть None. Нестандартные входы (не-dict)
    игнорируются. При обоих None возвращает [].
    """
    combined: list[dict] = []
    for stage_row in (screening_result, pair_row):
        if not isinstance(stage_row, dict):
            continue
        stage_evidence = stage_row.get("evidence")
        if not isinstance(stage_evidence, list):
            continue
        for item in stage_evidence:
            if not isinstance(item, dict):
                continue
            combined.append(item)

    deduplicated: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in combined:
        source_stage = item.get("source_stage")
        signal_type = item.get("signal_type")
        ref = item.get("ref")
        if (
            not isinstance(source_stage, str)
            or not isinstance(signal_type, str)
            or not isinstance(ref, str)
        ):
            continue
        key = (source_stage, signal_type, ref)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)
    return deduplicated

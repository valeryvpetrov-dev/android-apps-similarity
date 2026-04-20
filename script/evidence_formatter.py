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

Reader path (EXEC-088-READER): `format_evidence_as_text`,
`format_evidence_summary` и `describe_pair_evidence` превращают
записи Evidence в связное человеко-читаемое представление для
отчётов интерпретации.
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


# ---------------------------------------------------------------------------
# Reader path: человеко-читаемое представление доказательств.
# ---------------------------------------------------------------------------

_STAGE_ORDER = {"screening": 0, "pairwise": 1, "signing": 2}

_STAGE_LABELS = {
    "screening": "Первичный отбор",
    "pairwise": "Углублённое сравнение",
    "signing": "Подпись APK",
}

_SIGNAL_LABELS = {
    "signature_match": "совпадение подписи APK",
    "library_match": "совпадение набора библиотек",
    "icc_overlap": "пересечение ICC-кортежей",
}


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _signal_description(signal_type: str, ref: str) -> str:
    if signal_type == "layer_score":
        return "сходство по слою {}".format(ref)
    if signal_type in _SIGNAL_LABELS:
        return _SIGNAL_LABELS[signal_type]
    return "сигнал {}".format(signal_type)


def _evidence_sort_key(item: dict) -> tuple[int, float]:
    stage = item.get("source_stage", "")
    stage_order = _STAGE_ORDER.get(stage, 99)
    try:
        magnitude = float(item.get("magnitude", 0.0))
    except (TypeError, ValueError):
        magnitude = 0.0
    # magnitude по убыванию -> отрицательный ключ
    return (stage_order, -magnitude)


def format_evidence_as_text(
    evidence_list: list[dict], max_items: int = 20
) -> list[str]:
    """Форматировать список Evidence в человеко-читаемые строки.

    Каждая запись превращается в строку формата
    `"<Этап>: <описание сигнала> (сила ≈ <m>, источник: <ref>)"`.
    Пустой список возвращает одну строку-заглушку.

    Сортировка: сначала по `source_stage`
    (screening -> pairwise -> signing), затем по magnitude убыванию.
    Максимум `max_items` строк.
    """
    if not isinstance(evidence_list, list) or len(evidence_list) == 0:
        return ["Нет доказательств для этой пары."]

    valid_items: list[dict] = [
        item for item in evidence_list if isinstance(item, dict)
    ]
    valid_items.sort(key=_evidence_sort_key)

    try:
        limit = int(max_items)
    except (TypeError, ValueError):
        limit = 20
    if limit < 0:
        limit = 0

    lines: list[str] = []
    for item in valid_items[:limit]:
        stage = str(item.get("source_stage", ""))
        signal_type = str(item.get("signal_type", ""))
        ref = str(item.get("ref", ""))
        try:
            magnitude = float(item.get("magnitude", 0.0))
        except (TypeError, ValueError):
            magnitude = 0.0
        lines.append(
            "{stage}: {desc} (сила ≈ {magnitude:.2f}, источник: {ref})".format(
                stage=_stage_label(stage),
                desc=_signal_description(signal_type, ref),
                magnitude=magnitude,
                ref=ref,
            )
        )
    return lines


def format_evidence_summary(evidence_list: list[dict]) -> dict:
    """Агрегировать статистику по списку Evidence.

    Возвращает dict с ключами `total`, `by_stage`, `top_signals`
    (до 5 записей), `average_magnitude` и `max_magnitude_signal`.
    На пустом списке `total=0`, `average_magnitude=None`,
    `max_magnitude_signal=None`.
    """
    summary: dict = {
        "total": 0,
        "by_stage": {"screening": 0, "pairwise": 0, "signing": 0},
        "top_signals": [],
        "average_magnitude": None,
        "max_magnitude_signal": None,
    }
    if not isinstance(evidence_list, list):
        return summary

    valid_items: list[dict] = [
        item for item in evidence_list if isinstance(item, dict)
    ]
    if len(valid_items) == 0:
        return summary

    magnitudes: list[float] = []
    for item in valid_items:
        stage = str(item.get("source_stage", ""))
        if stage in summary["by_stage"]:
            summary["by_stage"][stage] += 1
        else:
            summary["by_stage"][stage] = summary["by_stage"].get(stage, 0) + 1
        try:
            magnitudes.append(float(item.get("magnitude", 0.0)))
        except (TypeError, ValueError):
            magnitudes.append(0.0)

    summary["total"] = len(valid_items)
    summary["average_magnitude"] = sum(magnitudes) / len(magnitudes)

    def _record_brief(record: dict) -> dict:
        try:
            magnitude_value = float(record.get("magnitude", 0.0))
        except (TypeError, ValueError):
            magnitude_value = 0.0
        return {
            "stage": str(record.get("source_stage", "")),
            "type": str(record.get("signal_type", "")),
            "ref": str(record.get("ref", "")),
            "magnitude": magnitude_value,
        }

    sorted_items = sorted(
        valid_items,
        key=lambda item: -(
            float(item.get("magnitude", 0.0))
            if isinstance(item.get("magnitude"), (int, float))
            else 0.0
        ),
    )
    summary["top_signals"] = [
        _record_brief(item) for item in sorted_items[:5]
    ]
    summary["max_magnitude_signal"] = _record_brief(sorted_items[0])
    return summary


def _collect_pair_notes(pair_row: dict) -> list[str]:
    notes: list[str] = []
    reason = pair_row.get("analysis_failed_reason")
    if isinstance(reason, str) and reason == "budget_exceeded":
        notes.append(
            "Пара прервана по жёсткому лимиту времени (инцидент)."
        )
    timeout_info = pair_row.get("timeout_info")
    if isinstance(timeout_info, dict):
        timeout_sec = timeout_info.get("pair_timeout_sec")
        stage = timeout_info.get("stage")
        notes.append(
            "Таймаут: {timeout} сек на этапе {stage}.".format(
                timeout=timeout_sec if timeout_sec is not None else "?",
                stage=stage if stage is not None else "?",
            )
        )
    if pair_row.get("shortcut_applied") is True:
        notes.append(
            "Применён сокращённый путь: высокое доверие + совпадение подписи."
        )
    signature_match = pair_row.get("signature_match")
    if (
        isinstance(signature_match, dict)
        and signature_match.get("status") == "mismatch"
    ):
        notes.append("Внимание: подписи APK не совпадают.")
    return notes


def describe_pair_evidence(
    pair_row: dict, screening_result: dict | None = None
) -> dict:
    """Главный reader: собрать связное представление доказательств пары.

    Возвращает dict со следующими ключами:
    `pair_id`, `verdict`, `similarity_score`, `evidence_lines`
    (человеко-читаемые строки), `summary` (агрегат) и `notes`
    (человеко-читаемые замечания о таймаутах, сокращённом пути
    и несовпадении подписей).

    Использует `collect_all_evidence` для объединения Evidence
    со всех этапов с дедупликацией.
    """
    if not isinstance(pair_row, dict):
        pair_row_safe: dict = {}
    else:
        pair_row_safe = pair_row

    evidence = collect_all_evidence(screening_result, pair_row_safe)
    evidence_lines = format_evidence_as_text(evidence)
    summary = format_evidence_summary(evidence)

    library_reduced = pair_row_safe.get("library_reduced_score")
    full_similarity = pair_row_safe.get("full_similarity_score")
    similarity_score: float | int | None
    if library_reduced is not None:
        similarity_score = library_reduced
    else:
        similarity_score = full_similarity

    pair_id_value = pair_row_safe.get("pair_id", "")
    pair_id = pair_id_value if isinstance(pair_id_value, str) else ""
    verdict_value = pair_row_safe.get("status", "unknown")
    verdict = verdict_value if isinstance(verdict_value, str) else "unknown"

    return {
        "pair_id": pair_id,
        "verdict": verdict,
        "similarity_score": similarity_score,
        "evidence_lines": evidence_lines,
        "summary": summary,
        "notes": _collect_pair_notes(pair_row_safe),
    }

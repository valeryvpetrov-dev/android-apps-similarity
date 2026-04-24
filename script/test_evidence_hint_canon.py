"""Тесты EXEC-HINT-20-EVIDENCE-CANON.

Фиксируем канонический контракт между Evidence и hint:

1. `Evidence` — канонический структурированный объект с per-layer фактами
   (см. `evidence_formatter.make_evidence`: поля `source_stage`,
   `signal_type`, `magnitude`, `ref`).
2. `hint` — производный человеко-читаемый (или машинный) объект, который
   формируется ИЗ `Evidence` функцией `format_hint_from_evidence(evidence)`.
3. Инварианты:
   - факты в hint ⊆ факты в Evidence (hint не может упоминать сигнал,
     которого нет в Evidence);
   - `Evidence` может существовать без hint (raw mode);
   - пустой `Evidence` → пустая/дефолтная строка hint, без падений.

Канонический документ: `system/result-interpretation-contract-v1.md`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _evidence_layer_score(ref: str, magnitude: float) -> dict:
    return {
        "source_stage": "pairwise",
        "signal_type": "layer_score",
        "magnitude": magnitude,
        "ref": ref,
    }


def _evidence_signature_match(magnitude: float) -> dict:
    return {
        "source_stage": "signing",
        "signal_type": "signature_match",
        "magnitude": magnitude,
        "ref": "apk_signature",
    }


def test_hint_is_derived_from_evidence_and_facts_subset():
    """(a) hint производится функцией `format_hint_from_evidence` из Evidence,
    и все факты, упомянутые в hint, присутствуют в исходном Evidence.

    Инвариант: факты в hint ⊆ фактов в Evidence. Проверяется по `ref`
    каждой evidence-записи: если ref упомянут в hint, он обязан быть в
    исходном списке Evidence.
    """
    from evidence_formatter import format_hint_from_evidence  # noqa: WPS433

    evidence = [
        _evidence_layer_score("code", 0.81),
        _evidence_layer_score("component", 0.42),
        _evidence_signature_match(1.0),
    ]

    hint = format_hint_from_evidence(evidence)

    assert isinstance(hint, str), "hint обязан быть строкой (canonical derived)"
    assert hint.strip() != "", "при непустом Evidence hint не может быть пустым"

    evidence_refs = {item["ref"] for item in evidence}
    # Все ref-ы evidence должны быть упомянуты в hint-строке как факты,
    # и наоборот — hint не должен упоминать ref, которого нет в Evidence.
    mentioned_refs = {ref for ref in evidence_refs if ref in hint}
    assert mentioned_refs == evidence_refs, (
        "Каждый ref из Evidence должен быть упомянут в hint "
        f"(ожидалось: {evidence_refs}, упомянуто: {mentioned_refs})"
    )

    # Негативная проверка: hint не упоминает «чужие» refs.
    alien_refs = {"resource", "permission", "native_lib", "apk_manifest"}
    alien_refs -= evidence_refs
    for alien in alien_refs:
        assert alien not in hint, (
            "hint не может содержать факт, которого нет в Evidence: "
            f"{alien!r}"
        )


def test_pairwise_explainer_uses_same_format_hint_from_evidence():
    """(b) pairwise_explainer больше не формирует hints независимо от Evidence:
    при наличии evidence в pair_row hints строятся из Evidence через тот же
    канонический путь (`format_hint_from_evidence` / `_hints_from_evidence`).

    Проверяем на одном наборе Evidence, что hints, полученные из
    `pairwise_explainer.build_output_rows`, эквивалентны по набору фактов
    производным из `evidence_formatter.format_hint_from_evidence`.
    """
    import evidence_formatter
    import pairwise_explainer

    evidence = [
        _evidence_layer_score("code", 0.88),
        _evidence_signature_match(1.0),
    ]

    # Канонический путь: Evidence -> hint-строка (через format_hint_from_evidence)
    canon_hint_str = evidence_formatter.format_hint_from_evidence(evidence)

    # Путь pairwise_explainer: Evidence -> list[dict] hints
    pair = {
        "app_a": "app-a",
        "app_b": "app-b",
        "pair_id": "pair_canon",
        "similarity_score": 0.9,
        "evidence": evidence,
    }
    rows = pairwise_explainer.build_output_rows([pair])
    assert len(rows) == 1
    row = rows[0]

    # pairwise hints не должны содержать ref, которого нет в Evidence.
    evidence_refs = {item["ref"] for item in evidence}
    hint_entities = {hint["entity"] for hint in row["explanation_hints"]}
    assert hint_entities <= evidence_refs, (
        "pairwise hints ссылаются на сущность, которой нет в Evidence: "
        f"hint_entities={hint_entities}, evidence_refs={evidence_refs}"
    )

    # Канонический hint-строка упоминает каждый ref Evidence.
    for ref in evidence_refs:
        assert ref in canon_hint_str, (
            f"Канонический hint не упомянул ref={ref!r} из Evidence"
        )

    # Инвариант: hint не может содержать factов, которых нет в Evidence.
    # В канонической hint-строке для данной пары не должен фигурировать
    # слой resource (он отсутствует в Evidence).
    assert "resource" not in canon_hint_str


def test_empty_evidence_yields_safe_default_hint():
    """(c) Если Evidence пуст → hint = безопасная дефолтная/пустая строка.

    Функция не должна падать на пустом списке, None, не-списке и дублирующих
    некорректных записях. Возвращает str (пустая или дефолтная).
    """
    from evidence_formatter import format_hint_from_evidence  # noqa: WPS433

    # Пустой список
    hint_empty = format_hint_from_evidence([])
    assert isinstance(hint_empty, str)
    assert hint_empty.strip() == "" or "нет" in hint_empty.lower() or "no " in hint_empty.lower()

    # None
    hint_none = format_hint_from_evidence(None)  # type: ignore[arg-type]
    assert isinstance(hint_none, str)
    assert hint_none == hint_empty

    # Не-список
    hint_bad = format_hint_from_evidence("not a list")  # type: ignore[arg-type]
    assert isinstance(hint_bad, str)
    assert hint_bad == hint_empty

    # Список с не-dict и некорректными записями
    hint_noise = format_hint_from_evidence([None, 42, {"foo": "bar"}])  # type: ignore[list-item]
    assert isinstance(hint_noise, str)
    # Невалидные записи не должны порождать фактов в hint
    assert hint_noise == hint_empty


def test_legacy_generate_hint_delegates_to_format_hint_from_evidence():
    """Рефактор: `pairwise_explainer.generate_hint` сохраняет старую сигнатуру
    (deprecated), но внутри должен звать `evidence_formatter.build_evidence` +
    `format_hint_from_evidence`. То есть единый путь формирования hint.
    """
    import evidence_formatter
    import pairwise_explainer

    pair = {
        "app_a": "app-a",
        "app_b": "app-b",
        "pair_id": "pair_legacy_delegate",
        "similarity_score": 0.7,
        "views_used": ["code", "component"],
        "library_reduced_score": 0.65,
        "signature_match": {"score": 1.0, "status": "match"},
    }

    # Канонический путь: собрать Evidence писателем и затем получить hint.
    evidence_canonical = evidence_formatter.collect_evidence_from_pairwise(pair)
    hint_canonical = evidence_formatter.format_hint_from_evidence(evidence_canonical)

    # Legacy API обязан возвращать тот же hint.
    hint_legacy = pairwise_explainer.generate_hint(pair)

    assert hint_legacy == hint_canonical, (
        "generate_hint должен делегировать в format_hint_from_evidence; "
        f"canonical={hint_canonical!r}, legacy={hint_legacy!r}"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

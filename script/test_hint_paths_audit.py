"""Тесты EXEC-HINT-24-EVIDENCE-CONTRACT-AUDIT.

Закрепляют контракт «два режима построения hint, без shadow-paths»
(рекомендация критика HINT волны 23, коммит 8700145).

Канон:
- `evidence_formatter.format_hint_from_evidence(evidence)` — единственный
  публичный путь Evidence → hint-строка;
- `pairwise_explainer.generate_hint(pair_row)` — deprecated legacy API,
  обязан делегировать в canonical через `collect_evidence_from_pairwise`,
  не строит hint независимо;
- legacy fallback в `build_output_rows` (для старых pair_row без Evidence)
  — режим помечается явным warning `legacy_hint_path` и в выходной строке
  ставится `hint_metadata = {"source": "legacy", "reason": "evidence_empty"}`;
- canonical-режим в `build_output_rows` — `hint_metadata = {"source": "canonical"}`,
  чтобы режим был явно различим в выводе и в логах больших прогонов.

Канонический документ: `system/result-interpretation-contract-v1.md` раздел 6.
"""
from __future__ import annotations

import inspect
import logging
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


def test_only_canonical_public_hint_function_is_format_hint_from_evidence():
    """(a) Единственная публичная функция формирования hint — это
    `evidence_formatter.format_hint_from_evidence(evidence)`.

    Проверяем, что:
    - функция существует и принимает один позиционный аргумент `evidence_list`;
    - других публичных функций с именем `*_hint_from_*`/`*format_hint*` в
      `evidence_formatter` нет (никто не добавил параллельный канон);
    - `pairwise_explainer.generate_hint` существует, deprecated и
      делегирует в `evidence_formatter.format_hint_from_evidence` (а не
      строит hint собственными правилами).
    """
    import evidence_formatter
    import pairwise_explainer

    # Каноническая функция существует и имеет один позиционный аргумент.
    canonical = getattr(evidence_formatter, "format_hint_from_evidence", None)
    assert canonical is not None, (
        "evidence_formatter.format_hint_from_evidence — canonical путь, должна существовать"
    )
    sig = inspect.signature(canonical)
    params = list(sig.parameters.values())
    assert len(params) == 1, (
        "format_hint_from_evidence обязана принимать ровно один аргумент "
        "(evidence_list); нашлось: " + repr(params)
    )

    # В evidence_formatter нет других публичных функций *format_hint*/*hint_from*.
    public_hint_funcs = {
        name
        for name, obj in vars(evidence_formatter).items()
        if not name.startswith("_")
        and callable(obj)
        and ("format_hint" in name or "hint_from" in name)
    }
    assert public_hint_funcs == {"format_hint_from_evidence"}, (
        "В evidence_formatter не должно быть другого публичного hint-канона; "
        f"найдено: {public_hint_funcs}"
    )

    # `pairwise_explainer.generate_hint` обязан делегировать через canonical
    # (волна 17 → волна 20 → волна 24): результат должен совпадать с прямым
    # вызовом canonical-цепочки.
    assert hasattr(pairwise_explainer, "generate_hint"), (
        "pairwise_explainer.generate_hint должен оставаться (deprecated API)"
    )
    pair = {
        "app_a": "a",
        "app_b": "b",
        "pair_id": "pair_canon_only",
        "views_used": ["code"],
        "library_reduced_score": 0.7,
    }
    legacy_hint = pairwise_explainer.generate_hint(pair)
    canonical_hint = canonical(
        evidence_formatter.collect_evidence_from_pairwise(pair)
    )
    assert legacy_hint == canonical_hint, (
        "generate_hint обязан делегировать в canonical путь "
        f"(canonical={canonical_hint!r}, legacy={legacy_hint!r})"
    )


def test_canonical_returns_safe_default_on_invalid_evidence():
    """(b) При пустом или невалидном Evidence canonical возвращает безопасную
    дефолтную строку без exceptions.

    Это страхует UI/логи от падений на старых артефактах: если Evidence
    отсутствует/битый — hint = "" (raw-режим, hint просто не показывается).
    """
    from evidence_formatter import format_hint_from_evidence  # noqa: WPS433

    invalid_inputs = [
        None,
        [],
        "not a list",
        42,
        {"foo": "bar"},
        [None, 42, "string"],
        [{"foo": "bar"}],
        [{"signal_type": "", "ref": "code", "magnitude": 0.5}],
        [{"signal_type": "layer_score", "ref": "", "magnitude": 0.5}],
        [{"signal_type": "layer_score", "ref": "code", "magnitude": "bad"}],
    ]
    for value in invalid_inputs:
        # Не должно бросать.
        try:
            hint = format_hint_from_evidence(value)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"format_hint_from_evidence бросил {type(exc).__name__} "
                f"на входе {value!r}: {exc}"
            )
        # Должна быть строка-дефолт (пустая в текущей реализации).
        assert isinstance(hint, str), (
            f"format_hint_from_evidence должен возвращать str, "
            f"вход={value!r}, тип={type(hint).__name__}"
        )
        assert hint == "" or hint.strip() == "", (
            "Безопасный дефолт на невалидном Evidence — пустая строка; "
            f"вход={value!r}, hint={hint!r}"
        )


def test_pairwise_generate_hint_delegates_via_collect_evidence_from_pairwise():
    """(c) `pairwise_explainer.generate_hint(pair_row)` обязан делегировать
    в `evidence_formatter.format_hint_from_evidence(
        collect_evidence_from_pairwise(pair_row))`,
    а не строить hint независимо.

    Проверка: на нескольких pair_row с разной структурой результат
    `generate_hint` идентичен явной канонической цепочке.
    """
    import evidence_formatter
    import pairwise_explainer

    test_pairs = [
        # Старый формат — только library_reduced_score, views_used.
        {
            "app_a": "a",
            "app_b": "b",
            "pair_id": "p1",
            "views_used": ["code", "component"],
            "library_reduced_score": 0.65,
        },
        # С signature_match.
        {
            "app_a": "a",
            "app_b": "b",
            "pair_id": "p2",
            "views_used": ["code"],
            "library_reduced_score": 0.5,
            "signature_match": {"score": 1.0, "status": "match"},
        },
        # С per_view_jaccard (per-layer magnitude).
        {
            "app_a": "a",
            "app_b": "b",
            "pair_id": "p3",
            "views_used": ["code", "component"],
            "per_view_jaccard": {"code": 0.9, "component": 0.3},
        },
        # analysis_failed -> Evidence пустой -> hint пустой.
        {
            "app_a": "a",
            "app_b": "b",
            "pair_id": "p4",
            "status": "analysis_failed",
            "views_used": ["code"],
            "library_reduced_score": 0.5,
        },
        # Не-dict / странный вход — не должен падать.
        {},
    ]
    for pair in test_pairs:
        canonical_evidence = evidence_formatter.collect_evidence_from_pairwise(pair)
        canonical_hint = evidence_formatter.format_hint_from_evidence(
            canonical_evidence
        )
        legacy_hint = pairwise_explainer.generate_hint(pair)
        assert legacy_hint == canonical_hint, (
            "generate_hint обязан строиться через "
            "collect_evidence_from_pairwise -> format_hint_from_evidence; "
            f"pair={pair!r}, canonical={canonical_hint!r}, legacy={legacy_hint!r}"
        )

    # Дополнительно: вход не-dict
    assert pairwise_explainer.generate_hint(None) == ""  # type: ignore[arg-type]
    assert pairwise_explainer.generate_hint("oops") == ""  # type: ignore[arg-type]


def test_legacy_fallback_emits_legacy_hint_path_warning_and_metadata(caplog):
    """(d) При чтении legacy-pair_row (старый формат без Evidence-структуры):
    - в логах есть стабильный warning-маркер `legacy_hint_path` (по нему
      легко искать в логах больших прогонов и метить процент legacy);
    - в выходной строке `build_output_rows` записан
      `hint_metadata = {"source": "legacy", "reason": "evidence_empty"}`.

    При canonical пути `hint_metadata = {"source": "canonical"}` — режим
    явно различим в выводе, без необходимости заглядывать в evidence.
    """
    import pairwise_explainer

    pair_legacy = {
        "app_a": "a",
        "app_b": "b",
        "pair_id": "pair_legacy_marker",
        "similarity_score": 0.7,
        # Никакого evidence, никакого views_used / per_view_*.
        "component_features_a": ["permission:android.permission.CAMERA"],
        "component_features_b": ["permission:android.permission.CAMERA"],
        "resource_features_a": [],
        "resource_features_b": [],
        "dots_1": [],
        "dots_2": [],
    }

    with caplog.at_level(logging.WARNING, logger="pairwise_explainer"):
        rows_legacy = pairwise_explainer.build_output_rows([pair_legacy])

    assert len(rows_legacy) == 1
    row_legacy = rows_legacy[0]

    # (d.1) hint_metadata явно помечает legacy-режим.
    assert "hint_metadata" in row_legacy, (
        "build_output_rows обязан записывать hint_metadata, чтобы режим "
        "был различим в выводе; ключ отсутствует"
    )
    assert row_legacy["hint_metadata"].get("source") == "legacy", (
        "В legacy-пути hint_metadata.source должен быть 'legacy'; "
        f"получено: {row_legacy['hint_metadata']!r}"
    )
    assert row_legacy["hint_metadata"].get("reason") == "evidence_empty", (
        "В legacy-пути hint_metadata.reason должен пояснять причину; "
        f"получено: {row_legacy['hint_metadata']!r}"
    )

    # (d.2) В логах есть стабильный маркер `legacy_hint_path`.
    legacy_records = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "legacy_hint_path" in record.getMessage()
    ]
    assert len(legacy_records) >= 1, (
        "Ожидалось WARNING с маркером 'legacy_hint_path', чтобы legacy-режим "
        "было видно в логах больших прогонов; "
        f"получено: {[r.getMessage() for r in caplog.records]}"
    )
    assert "pair_legacy_marker" in legacy_records[0].getMessage(), (
        "В legacy_hint_path-warning должен быть pair_id для трассируемости; "
        f"получено: {legacy_records[0].getMessage()!r}"
    )

    # Дополнительно: при canonical-пути hint_metadata.source = 'canonical'.
    pair_canonical = {
        "app_a": "a",
        "app_b": "b",
        "pair_id": "pair_canonical_marker",
        "similarity_score": 0.9,
        "evidence": [
            _evidence_layer_score("code", 0.8),
            _evidence_layer_score("component", 0.4),
        ],
    }
    rows_canonical = pairwise_explainer.build_output_rows([pair_canonical])
    assert len(rows_canonical) == 1
    row_canonical = rows_canonical[0]
    assert "hint_metadata" in row_canonical, (
        "build_output_rows обязан записывать hint_metadata и в canonical-пути"
    )
    assert row_canonical["hint_metadata"].get("source") == "canonical", (
        "В canonical-пути hint_metadata.source должен быть 'canonical'; "
        f"получено: {row_canonical['hint_metadata']!r}"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

"""Tests for EXEC-HINT-30-OBFUSCATION-WRITER.

Закрывает находку HINT-29: ``OBFUSCATION_SHIFT`` объявлен в
``hint_taxonomy.HINT_TAXONOMY_CLASSES``, но ни один writer его не
производит. Волна 30 добавляет:

1. Heuristic-сигнал в writer'е: при детекции ``library_view_v2.detected_via='jaccard_v2'``
   или коротких имён методов (a/b/c-pattern) на ≥50% method_signatures
   в pair_row ставится evidence-запись
   ``{source_stage:'pairwise', signal_type:'obfuscation_shift', magnitude:float, ref:'jaccard_v2_libmask' | 'short_method_names'}``.
2. Шестой канал ``obfuscation`` в ``EVIDENCE_CHANNELS`` для per-channel
   faithfulness-диагностики; ``compute_channel_faithfulness`` обрабатывает
   6 каналов.
3. ``classify_evidence_to_taxonomy`` уже умеет переводить
   ``signal_type='obfuscation_shift'`` -> ``OBFUSCATION_SHIFT``; здесь
   фиксируем это контрактным тестом, чтобы пайплайн writer -> taxonomy
   не разъехался при будущих рефакторингах.

Все тесты строго на synthetic Evidence/pair_row — без реальных APK,
чтобы не зависеть от F-Droid-данных и apktool на CI/локально.
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# (a) Heuristic-сигнал в writer'е: jaccard_v2_libmask и short_method_names.
# ---------------------------------------------------------------------------


def test_writer_emits_obfuscation_shift_when_library_view_v2_detected_via_jaccard_v2() -> None:
    """Когда pair_row помечен ``library_view_v2.detected_via='jaccard_v2'``,
    writer добавляет в evidence запись
    ``{signal_type:'obfuscation_shift', ref:'jaccard_v2_libmask', magnitude:0.5}``.
    """
    from pairwise_explainer import build_output_rows  # noqa: WPS433

    pair_row = {
        "app_a": "alpha.apk",
        "app_b": "beta.apk",
        "pair_id": "PAIR-OBF-LIB",
        "full_similarity_score": 0.5,
        "library_view_v2": {"detected_via": "jaccard_v2"},
        # Минимальный валидный evidence-набор, чтобы canonical-ветка
        # сработала, а not legacy fallback.
        "evidence": [
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.5,
                "ref": "library",
            },
        ],
    }

    rows = build_output_rows([pair_row])
    assert len(rows) == 1
    evidence = rows[0].get("evidence")
    assert isinstance(evidence, list)
    obfuscation_records = [
        item for item in evidence
        if isinstance(item, dict) and item.get("signal_type") == "obfuscation_shift"
    ]
    assert len(obfuscation_records) == 1, (
        "writer must emit exactly one obfuscation_shift evidence "
        "when library_view_v2.detected_via='jaccard_v2'"
    )
    record = obfuscation_records[0]
    assert record["ref"] == "jaccard_v2_libmask"
    assert abs(float(record["magnitude"]) - 0.5) < 1e-6
    assert record["source_stage"] == "pairwise"


def test_writer_emits_obfuscation_shift_when_short_method_names_dominate() -> None:
    """Когда в ``code_view_v4.method_signatures`` ≥50% записей имеют форму
    короткого имени (``a()``, ``b$a()`` и т.п.), writer добавляет
    ``{signal_type:'obfuscation_shift', ref:'short_method_names', magnitude:0.6}``.
    """
    from pairwise_explainer import build_output_rows  # noqa: WPS433

    pair_row = {
        "app_a": "alpha.apk",
        "app_b": "beta.apk",
        "pair_id": "PAIR-OBF-CODE",
        "full_similarity_score": 0.4,
        "code_view_v4": {
            # 6 из 8 (75%) — короткие имена -> heuristic срабатывает.
            "method_signatures": [
                "a()",
                "b()",
                "c()",
                "d$e()",
                "f$g()",
                "h()",
                "computeHash()",
                "encodeBitmap()",
            ],
        },
        "evidence": [
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.4,
                "ref": "code",
            },
        ],
    }

    rows = build_output_rows([pair_row])
    assert len(rows) == 1
    evidence = rows[0].get("evidence")
    assert isinstance(evidence, list)
    obfuscation_records = [
        item for item in evidence
        if isinstance(item, dict) and item.get("signal_type") == "obfuscation_shift"
    ]
    assert len(obfuscation_records) == 1, (
        "writer must emit obfuscation_shift evidence when ≥50% method_signatures "
        "match short-name pattern"
    )
    record = obfuscation_records[0]
    assert record["ref"] == "short_method_names"
    assert abs(float(record["magnitude"]) - 0.6) < 1e-6


def test_writer_does_not_emit_obfuscation_shift_for_clean_pair() -> None:
    """Без jaccard_v2-сигнала и без коротких имён writer НЕ выдаёт obfuscation_shift —
    отрицательный контроль защищает от ложных срабатываний на любой паре.
    """
    from pairwise_explainer import build_output_rows  # noqa: WPS433

    pair_row = {
        "app_a": "alpha.apk",
        "app_b": "beta.apk",
        "pair_id": "PAIR-OBF-CLEAN",
        "full_similarity_score": 0.7,
        "code_view_v4": {
            "method_signatures": [
                "computeHash()",
                "encodeBitmap()",
                "renderActivity()",
                "loadResources()",
            ],
        },
        "evidence": [
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.7,
                "ref": "code",
            },
        ],
    }

    rows = build_output_rows([pair_row])
    assert len(rows) == 1
    evidence = rows[0].get("evidence", [])
    obfuscation_records = [
        item for item in evidence
        if isinstance(item, dict) and item.get("signal_type") == "obfuscation_shift"
    ]
    assert len(obfuscation_records) == 0, (
        "writer must NOT emit obfuscation_shift on clean pairs (no jaccard_v2, "
        "long method names)"
    )


# ---------------------------------------------------------------------------
# (b) Шестой канал ``obfuscation`` в EVIDENCE_CHANNELS.
# ---------------------------------------------------------------------------


def test_evidence_channels_canon_set_has_six_channels_including_obfuscation() -> None:
    """Канон каналов расширен до шести: code/component/library/resource/signing/obfuscation."""
    from hint_faithfulness import EVIDENCE_CHANNELS  # noqa: WPS433

    assert set(EVIDENCE_CHANNELS) == {
        "code",
        "component",
        "library",
        "resource",
        "signing",
        "obfuscation",
    }
    assert len(EVIDENCE_CHANNELS) == 6


def test_classify_evidence_channel_maps_obfuscation_shift_to_obfuscation_channel() -> None:
    """Запись с ``signal_type='obfuscation_shift'`` маппится в канал ``obfuscation``."""
    from hint_faithfulness import classify_evidence_channel  # noqa: WPS433

    record = {
        "source_stage": "pairwise",
        "signal_type": "obfuscation_shift",
        "magnitude": 0.5,
        "ref": "jaccard_v2_libmask",
    }
    assert classify_evidence_channel(record) == "obfuscation"


def test_compute_channel_faithfulness_returns_non_none_for_obfuscation_on_synthetic_r8_pair() -> None:
    """(c) на synthetic R8-паре (evidence содержит obfuscation_shift)
    канал ``obfuscation`` получает не-None метрики, остальные пять — могут быть None,
    но obfuscation обязан быть заполнен."""
    from hint_faithfulness import compute_channel_faithfulness  # noqa: WPS433

    pair = {"app_a": "x.apk", "app_b": "x_r8.apk", "pair_id": "PAIR-R8-001"}
    evidence = [
        {
            "source_stage": "pairwise",
            "signal_type": "obfuscation_shift",
            "magnitude": 0.5,
            "ref": "jaccard_v2_libmask",
        },
        {
            "source_stage": "pairwise",
            "signal_type": "obfuscation_shift",
            "magnitude": 0.6,
            "ref": "short_method_names",
        },
    ]

    result = compute_channel_faithfulness(pair, evidence)

    assert "obfuscation" in result
    metrics = result["obfuscation"]
    assert set(metrics.keys()) == {"faithfulness", "sufficiency", "comprehensiveness"}
    assert metrics["faithfulness"] is not None, (
        "obfuscation channel must be populated on synthetic R8 evidence"
    )
    assert metrics["sufficiency"] is not None
    assert metrics["comprehensiveness"] is not None


# ---------------------------------------------------------------------------
# (d) Маппинг ``signal_type='obfuscation_shift'`` -> OBFUSCATION_SHIFT в taxonomy.
# ---------------------------------------------------------------------------


def test_classify_evidence_to_taxonomy_returns_obfuscation_shift_for_writer_signal() -> None:
    """(d) classify_evidence_to_taxonomy({signal_type:'obfuscation_shift', ...})
    возвращает OBFUSCATION_SHIFT — контракт между writer и taxonomy.
    """
    from hint_taxonomy import (  # noqa: WPS433
        OBFUSCATION_SHIFT,
        classify_evidence_to_taxonomy,
    )

    record = {
        "source_stage": "pairwise",
        "signal_type": "obfuscation_shift",
        "magnitude": 0.5,
        "ref": "jaccard_v2_libmask",
    }
    assert classify_evidence_to_taxonomy(record) == OBFUSCATION_SHIFT

    record_short_names = {
        "source_stage": "pairwise",
        "signal_type": "obfuscation_shift",
        "magnitude": 0.6,
        "ref": "short_method_names",
    }
    assert classify_evidence_to_taxonomy(record_short_names) == OBFUSCATION_SHIFT

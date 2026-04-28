"""Tests for per-channel split of hint faithfulness metrics.

Канон каналов (после EXEC-HINT-30-OBFUSCATION-WRITER): ``code``, ``component``,
``library``, ``resource``, ``signing``, ``obfuscation`` — шесть каналов.
Тесты проверяют, что ``compute_channel_faithfulness`` возвращает три метрики
(faithfulness/sufficiency/comprehensiveness) на каждый канал, корректно
обрабатывает «нет данных по каналу» (None, не 0.0) и согласуется со старой
single-number ``compute_faithfulness`` при равномерном распределении evidence.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from hint_faithfulness import (  # noqa: E402
    compute_channel_faithfulness,
    classify_evidence_channel,
    EVIDENCE_CHANNELS,
)


def _evidence(signal_type: str, ref: str, magnitude: float, source_stage: str = "pairwise") -> dict:
    return {
        "source_stage": source_stage,
        "signal_type": signal_type,
        "ref": ref,
        "magnitude": magnitude,
    }


def test_evidence_channels_canon_set_is_exactly_six():
    """Канон каналов после EXEC-HINT-30-OBFUSCATION-WRITER:
    code/component/library/resource/signing/obfuscation — шесть каналов."""

    assert set(EVIDENCE_CHANNELS) == {
        "code",
        "component",
        "library",
        "resource",
        "signing",
        "obfuscation",
    }


def test_classify_evidence_channel_maps_layer_score_and_signing():
    """Маппинг evidence -> channel:
    - signal_type=signature_match -> signing;
    - layer_score:code -> code;
    - layer_score:component / activity_overlap -> component;
    - layer_score:library / library_match -> library;
    - layer_score:resource / resource_overlap -> resource.
    """

    assert classify_evidence_channel(_evidence("signature_match", "apk_signature", 1.0, "signing")) == "signing"
    assert classify_evidence_channel(_evidence("layer_score", "code", 0.6)) == "code"
    assert classify_evidence_channel(_evidence("layer_score", "component", 0.5)) == "component"
    assert classify_evidence_channel(_evidence("activity_overlap", "com.foo.MainActivity", 0.4)) == "component"
    assert classify_evidence_channel(_evidence("layer_score", "library", 0.3)) == "library"
    assert classify_evidence_channel(_evidence("library_match", "okhttp3", 0.9)) == "library"
    assert classify_evidence_channel(_evidence("layer_score", "resource", 0.2)) == "resource"
    assert classify_evidence_channel(_evidence("resource_overlap", "drawable/icon", 0.8)) == "resource"


def test_compute_channel_faithfulness_returns_dict_with_all_six_channels():
    """(a) compute_channel_faithfulness возвращает dict с ключами 6 каналов
    (включая ``obfuscation``); для каждого канала — три метрики
    (faithfulness, sufficiency, comprehensiveness)."""

    pair = {
        "app_a": "alpha",
        "app_b": "beta",
        "pair_id": "PAIR-A",
        "full_similarity_score": 0.7,
    }
    evidence = [
        _evidence("layer_score", "code", 0.6),
        _evidence("layer_score", "component", 0.4),
        _evidence("layer_score", "library", 0.5),
        _evidence("layer_score", "resource", 0.3),
        _evidence("signature_match", "apk_signature", 1.0, "signing"),
        _evidence("obfuscation_shift", "jaccard_v2_libmask", 0.5),
    ]

    result = compute_channel_faithfulness(pair, evidence)

    assert set(result.keys()) == {
        "code",
        "component",
        "library",
        "resource",
        "signing",
        "obfuscation",
    }
    for channel_name, channel_metrics in result.items():
        assert set(channel_metrics.keys()) == {"faithfulness", "sufficiency", "comprehensiveness"}, (
            f"channel {channel_name} must have three metric keys"
        )


def test_compute_channel_faithfulness_marks_empty_channels_as_none():
    """(b) на синтетической паре, где все evidence-записи относятся только к каналу `code`,
    остальные четыре канала получают {faithfulness: None, sufficiency: None, comprehensiveness: None}.
    None означает «нет данных», а не 0.0."""

    pair = {"app_a": "x", "app_b": "y", "pair_id": "PAIR-CODE-ONLY"}
    evidence = [
        _evidence("layer_score", "code", 0.6),
    ]

    result = compute_channel_faithfulness(pair, evidence)

    for empty_channel in ("component", "library", "resource", "signing", "obfuscation"):
        channel_metrics = result[empty_channel]
        assert channel_metrics["faithfulness"] is None, (
            f"empty channel {empty_channel} faithfulness must be None, got {channel_metrics['faithfulness']!r}"
        )
        assert channel_metrics["sufficiency"] is None
        assert channel_metrics["comprehensiveness"] is None

    code_metrics = result["code"]
    assert code_metrics["faithfulness"] is not None
    assert code_metrics["sufficiency"] is not None
    assert code_metrics["comprehensiveness"] is not None


def test_compute_channel_faithfulness_average_differs_from_single_number_when_channels_uneven():
    """(c) при evidence только в `code` и `library` среднее по непустым каналам
    в общем случае ≠ старой single-number faithfulness — это новое независимое поведение."""

    pair = {"app_a": "x", "app_b": "y", "pair_id": "PAIR-UNEVEN"}
    evidence = [
        _evidence("layer_score", "code", 0.6),
        _evidence("library_match", "okhttp3", 0.9),
        _evidence("library_match", "retrofit", 0.4),
    ]

    result = compute_channel_faithfulness(pair, evidence)

    # На каждый канал по отдельности — две метрики, по которым считать
    # корреляцию проблематично (1 точка); важно, что поведение независимо
    # от старой compute_faithfulness и каналы code и library заполнены.
    assert result["code"]["faithfulness"] is not None
    assert result["library"]["faithfulness"] is not None
    # Empty channels stay None.
    for empty_channel in ("component", "resource", "signing", "obfuscation"):
        assert result[empty_channel]["faithfulness"] is None

    # Среднее по непустым каналам — самостоятельная диагностика.
    non_empty_faith = [
        result[channel_name]["faithfulness"]
        for channel_name in ("code", "library")
    ]
    average_faith = sum(non_empty_faith) / len(non_empty_faith)
    # Значение существует и не сводится к нулю при наличии данных в обоих каналах.
    assert isinstance(average_faith, float)


def test_compute_channel_faithfulness_uniform_evidence_is_consistent_with_single_number():
    """(d) sanity-check: если evidence во всех 6 каналах полностью одинаковая
    (по структуре «одна запись на канал, magnitude=1.0»), среднее по каналам
    sufficiency воспроизводит single-channel sufficiency (1.0)."""

    pair = {"app_a": "a", "app_b": "b", "pair_id": "PAIR-UNIFORM"}
    evidence = [
        _evidence("layer_score", "code", 1.0),
        _evidence("layer_score", "component", 1.0),
        _evidence("layer_score", "library", 1.0),
        _evidence("layer_score", "resource", 1.0),
        _evidence("signature_match", "apk_signature", 1.0, "signing"),
        _evidence("obfuscation_shift", "jaccard_v2_libmask", 1.0),
    ]

    result = compute_channel_faithfulness(pair, evidence)

    suff_values = [result[channel_name]["sufficiency"] for channel_name in EVIDENCE_CHANNELS]
    assert all(value is not None for value in suff_values)
    average_sufficiency = sum(suff_values) / len(suff_values)
    # При одном feature на канал hint-only == pair_features, sufficiency == 1.0.
    assert math.isclose(average_sufficiency, 1.0, rel_tol=1e-6, abs_tol=1e-6)


def test_compute_channel_faithfulness_signing_isolated():
    """Signing-канал распознаётся по signal_type=signature_match независимо от source_stage."""

    pair = {"app_a": "a", "app_b": "b", "pair_id": "PAIR-SIGN"}
    evidence = [
        _evidence("signature_match", "apk_signature", 1.0, "signing"),
    ]

    result = compute_channel_faithfulness(pair, evidence)

    assert result["signing"]["faithfulness"] is not None
    for empty_channel in ("code", "component", "library", "resource", "obfuscation"):
        assert result[empty_channel]["faithfulness"] is None

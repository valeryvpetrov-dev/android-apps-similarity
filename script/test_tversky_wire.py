#!/usr/bin/env python3
"""Tests for REPR-19-TVERSKY-WIRE: пробрасывание Tversky/overlap в main flow.

Волна 17 добавила `score_tversky_asym_ab`, `score_tversky_asym_ba`,
`score_overlap` в `library_view_v2.compare_libraries_v2`, но потребитель их
не читает: `m_static_views._compare_library_enhanced` и
`compare_m_static_layer` выбрасывают всё, кроме `jaccard`.

Эти тесты фиксируют контракт главного пути: результат сравнения библиотек
через `m_static_views` обязан содержать явные поля `tversky_a`, `tversky_b`,
`overlap_min` с корректными значениями, не совпадающими с Жаккаром в
асимметричном случае.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from m_static_views import (  # noqa: E402
    _compare_library_enhanced,
    compare_m_static_layer,
)


# ---------------------------------------------------------------------------
# (a) известная пара, где |A∩B|/|A| != |A∩B|/|B|
# ---------------------------------------------------------------------------

def test_library_enhanced_returns_asymmetric_tversky_and_overlap_min() -> None:
    """Case: A = {lib_a, lib_b}, B = {lib_a, lib_b, lib_c, lib_d}.

    Shared = 2, |A| = 2, |B| = 4.
    |A ∩ B| / |A| = 1.0  (tversky_a при alpha=1, beta=0)
    |A ∩ B| / |B| = 0.5  (tversky_b при alpha=0, beta=1)
    overlap_min   = 2/min(2,4) = 1.0
    Жаккар         = 2/4 = 0.5
    """
    feat_a = {"libraries": {"lib_a": {}, "lib_b": {}}}
    feat_b = {"libraries": {"lib_a": {}, "lib_b": {}, "lib_c": {}, "lib_d": {}}}

    result = _compare_library_enhanced(feat_a, feat_b)

    # Явные новые поля должны присутствовать и быть неравны друг другу.
    assert "jaccard" in result, "ожидается прямое поле `jaccard` в результате"
    assert "tversky_a" in result, "ожидается поле `tversky_a` (|A∩B|/|A|)"
    assert "tversky_b" in result, "ожидается поле `tversky_b` (|A∩B|/|B|)"
    assert "overlap_min" in result, "ожидается поле `overlap_min`"

    assert result["jaccard"] == pytest.approx(0.5)
    assert result["tversky_a"] == pytest.approx(1.0)
    assert result["tversky_b"] == pytest.approx(0.5)
    assert result["overlap_min"] == pytest.approx(1.0)

    # Асимметрия: tversky_a != tversky_b и != jaccard.
    assert result["tversky_a"] != pytest.approx(result["tversky_b"])
    assert result["tversky_a"] != pytest.approx(result["jaccard"])


# ---------------------------------------------------------------------------
# (b) обратный случай: A ⊂ B, overlap_min = 1.0, tversky_a = 1.0, Jaccard < 1
# ---------------------------------------------------------------------------

def test_subset_case_overlap_min_and_tversky_a_are_one_but_jaccard_below_one() -> None:
    """A ⊂ B: |A ∩ B| = |A|, значит |A∩B|/|A| = 1.0, overlap_min = 1.0.

    Жаккар = |A|/|B| < 1.0 при |A| < |B|.
    """
    feat_a = {"libraries": {"ok_http": {}, "gson": {}}}
    feat_b = {
        "libraries": {
            "ok_http": {},
            "gson": {},
            "retrofit": {},
            "glide": {},
            "coroutines": {},
        }
    }

    result = _compare_library_enhanced(feat_a, feat_b)

    assert result["tversky_a"] == pytest.approx(1.0), "A ⊂ B => tversky_a = 1.0"
    assert result["overlap_min"] == pytest.approx(1.0), "A ⊂ B => overlap_min = 1.0"
    assert result["jaccard"] < 1.0, "при |A| < |B| Жаккар строго < 1"
    assert result["jaccard"] == pytest.approx(2.0 / 5.0)


# ---------------------------------------------------------------------------
# (c) schema-тест: в результате compare_m_static_layer('library', ...) из main
# flow есть новые поля, не только jaccard, и обратная совместимость с `score`
# сохранена.
# ---------------------------------------------------------------------------

def test_compare_m_static_layer_library_schema_includes_new_channels() -> None:
    """Главный диспетчер `compare_m_static_layer` для слоя `library` обязан
    отдавать все четыре канала (jaccard + три асимметричные) рядом с
    обратно совместимым `score`.
    """
    feat_a = {"libraries": {"lib_a": {}, "lib_b": {}, "lib_c": {}}}
    feat_b = {"libraries": {"lib_b": {}, "lib_c": {}, "lib_d": {}, "lib_e": {}}}

    result = compare_m_static_layer("library", feat_a, feat_b)

    # Обратная совместимость — не ломаем старых потребителей.
    assert "score" in result
    assert "status" in result

    # Новые каналы — всё поверх старого контракта.
    for key in ("jaccard", "tversky_a", "tversky_b", "overlap_min"):
        assert key in result, "schema: {!r} отсутствует в ответе compare_m_static_layer('library', ...)".format(key)
        assert isinstance(result[key], float), "{!r} должен быть float".format(key)

    # Значения должны совпадать с прямыми формулами:
    # |A ∩ B| = {lib_b, lib_c} = 2; |A| = 3; |B| = 4; |A ∪ B| = 5.
    assert result["jaccard"] == pytest.approx(2.0 / 5.0)
    assert result["tversky_a"] == pytest.approx(2.0 / 3.0)
    assert result["tversky_b"] == pytest.approx(2.0 / 4.0)
    assert result["overlap_min"] == pytest.approx(2.0 / min(3, 4))

    # Для этой пары |A| != |B| => tversky_a != tversky_b.
    assert result["tversky_a"] != pytest.approx(result["tversky_b"])


def test_compare_m_static_layer_library_score_stays_jaccard_for_back_compat() -> None:
    """`score` остаётся равен Жаккару (обратная совместимость с агрегацией
    `full_similarity_score` в `compare_all`).

    Новые каналы дополняют результат, но не меняют текущую интерпретацию
    `score` до явной калибровки (EXEC-086).
    """
    feat_a = {"libraries": {"lib_a": {}, "lib_b": {}}}
    feat_b = {"libraries": {"lib_a": {}, "lib_b": {}, "lib_c": {}, "lib_d": {}}}

    result = compare_m_static_layer("library", feat_a, feat_b)

    assert result["score"] == pytest.approx(result["jaccard"]), (
        "score должен остаться = jaccard, иначе ломаем full_similarity_score"
    )

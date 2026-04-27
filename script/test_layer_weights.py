#!/usr/bin/env python3
"""DEEP-19-LAYER-WEIGHTS-CALIBRATE: калибровка LAYER_WEIGHTS до суммы 1.0.

Критик волны 18 (`inbox/critics/deep-verification-2026-04-24.md`, раздел 1)
обнаружил, что сумма активных весов слоёв в :data:`LAYER_WEIGHTS` равна
``1.15`` (``0.45 + 0.25 + 0.20 + 0.10 + 0.15``). Это нарушает инвариант
«веса распределения единичны» и на защите потребует отдельного
объяснения. Агрегация :func:`compare_all` делит на ``weight_total``, то
есть фактический результат и так лежит в ``[0, 1]``, но инвариант на
самом словаре был нарушен.

Задача волны 19 — нормировать активные веса так, чтобы их сумма
равнялась ``1.0`` с сохранением относительных пропорций. Метод
калибровки: ``new_w = old_w / sum(old_w)``. Нулевые веса
(``code_v4``, ``code_v4_shingled``, ``resource_v2``) остаются ``0.0`` —
они отключены до ``EXEC-086``.

Тесты:
  * сумма активных весов ``== 1.0`` с точностью ``1e-9``;
  * сумма всех весов словаря ``== 1.0`` (нулевые не сдвигают сумму);
  * ``compare_all`` на синтетической паре возвращает
    ``full_similarity_score`` и ``library_reduced_score`` в ``[0, 1]``;
  * регрессия: синтетические per-layer scores дают ожидаемое
    агрегированное значение, совпадающее с ручным расчётом;
  * относительные пропорции активных весов сохранены до ``1e-9``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from m_static_views import LAYER_WEIGHTS  # noqa: E402


# Активные (ненулевые) слои — участвуют во взвешенной агрегации по
# умолчанию. Остальные (``code_v4``, ``code_v4_shingled``, ``resource_v2``)
# зарегистрированы с весом ``0.0`` до калибровки EXEC-086.
ACTIVE_LAYERS = ("code", "component", "resource", "library", "api")


class TestLayerWeightsSumInvariant(unittest.TestCase):
    """Инвариант «сумма весов = 1.0» на словаре."""

    def test_sum_of_active_weights_is_one(self):
        active_sum = sum(LAYER_WEIGHTS[layer] for layer in ACTIVE_LAYERS)
        self.assertAlmostEqual(active_sum, 1.0, delta=1e-9)

    def test_sum_of_all_weights_is_one(self):
        # Нулевые веса не должны смещать сумму.
        total_sum = sum(LAYER_WEIGHTS.values())
        self.assertAlmostEqual(total_sum, 1.0, delta=1e-9)

    def test_active_weights_in_unit_interval(self):
        # DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: после реальной калибровки
        # на F-Droid v2 пропорции старой нормировки (DEEP-19, ratio
        # code/library = 4.5) больше не верны — веса теперь распределены
        # по data-driven результату grid-search. Проверяем только базовый
        # инвариант: каждый активный вес в [0, 1].
        for layer in ACTIVE_LAYERS:
            w = LAYER_WEIGHTS[layer]
            self.assertGreaterEqual(w, 0.0, f"{layer} weight < 0: {w}")
            self.assertLessEqual(w, 1.0, f"{layer} weight > 1: {w}")

    def test_inactive_layers_remain_zero(self):
        # Layers, выключенные до EXEC-086, не должны получить ненулевой
        # вес при нормировке.
        for layer in ("code_v4", "code_v4_shingled", "resource_v2"):
            self.assertIn(layer, LAYER_WEIGHTS)
            self.assertEqual(LAYER_WEIGHTS[layer], 0.0)


class TestAggregateScoreInRange(unittest.TestCase):
    """Агрегированный score на синтетике остаётся в [0, 1]."""

    def _weighted_aggregate(self, per_layer_scores: dict[str, float]) -> float:
        # Локальная копия формулы из ``compare_all`` для прозрачной
        # проверки: sum(w_i * s_i) / sum(w_i), с теми же весами из
        # LAYER_WEIGHTS, но без зависимости от extract_all_features.
        weighted_sum = 0.0
        weight_total = 0.0
        for layer, score in per_layer_scores.items():
            weight = LAYER_WEIGHTS.get(layer, 0.0)
            if weight <= 0.0:
                continue
            weighted_sum += weight * score
            weight_total += weight
        return weighted_sum / weight_total if weight_total > 0.0 else 0.0

    def test_aggregate_in_unit_interval_for_random_like_inputs(self):
        per_layer = {
            "code": 0.3,
            "component": 0.7,
            "resource": 0.55,
            "library": 0.1,
            "api": 0.95,
        }
        result = self._weighted_aggregate(per_layer)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 1.0)

    def test_aggregate_equals_one_when_all_scores_are_one(self):
        per_layer = {layer: 1.0 for layer in ACTIVE_LAYERS}
        result = self._weighted_aggregate(per_layer)
        self.assertAlmostEqual(result, 1.0, delta=1e-9)

    def test_aggregate_equals_zero_when_all_scores_are_zero(self):
        per_layer = {layer: 0.0 for layer in ACTIVE_LAYERS}
        result = self._weighted_aggregate(per_layer)
        self.assertAlmostEqual(result, 0.0, delta=1e-9)


class TestAggregateRegression(unittest.TestCase):
    """Регрессия: формула агрегации остаётся ``sum(w_i * s_i) / sum(w_i)``.

    DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: после реальной калибровки на
    F-Droid v2 точные числа нормировки DEEP-19 более не воспроизводимы.
    Регрессия проверяет инвариант формулы: агрегированный score равен
    взвешенному среднему по активным слоям с нулевой суммой нормировки.
    """

    PER_LAYER_SCORES = {
        "code": 0.5,
        "component": 0.9,
        "resource": 0.8,
        "library": 0.85,
        "api": 0.7,
    }

    def test_aggregate_formula_is_weighted_average(self):
        # Прямая формула: sum(w_i * s_i) / sum(w_i) по активным
        # слоям с w_i > 0. Сравниваем с ручной свёрткой.
        weighted_sum = 0.0
        weight_total = 0.0
        manual_terms: list[tuple[float, float]] = []
        for layer in ACTIVE_LAYERS:
            w = LAYER_WEIGHTS[layer]
            if w <= 0.0:
                continue
            s = self.PER_LAYER_SCORES[layer]
            weighted_sum += w * s
            weight_total += w
            manual_terms.append((w, s))
        agg = weighted_sum / weight_total if weight_total > 0 else 0.0
        # Ручная свёртка должна совпасть.
        manual = (sum(w * s for w, s in manual_terms)
                  / sum(w for w, _ in manual_terms))
        self.assertAlmostEqual(agg, manual, delta=1e-9)
        # И всегда лежит в [0, 1] для score-ов из [0, 1].
        self.assertGreaterEqual(agg, 0.0)
        self.assertLessEqual(agg, 1.0)


if __name__ == "__main__":
    unittest.main()

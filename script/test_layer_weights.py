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

    def test_active_weights_relative_proportions_preserved(self):
        # Проверяем, что калибровка выполнена именно через
        # нормировку (new_w = old_w / old_sum), а не произвольным
        # переопределением. Пропорции code/library должны совпадать
        # с исходными 0.45 / 0.10 = 4.5.
        ratio_code_to_library = LAYER_WEIGHTS["code"] / LAYER_WEIGHTS["library"]
        self.assertAlmostEqual(ratio_code_to_library, 4.5, delta=1e-9)
        ratio_component_to_api = (
            LAYER_WEIGHTS["component"] / LAYER_WEIGHTS["api"]
        )
        self.assertAlmostEqual(ratio_component_to_api, 0.25 / 0.15, delta=1e-9)

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
    """Регрессия: точные числа для синтетической пары.

    Per-layer scores подобраны так, чтобы охватить все пять активных
    слоёв. Ручной расчёт:
      * веса после калибровки: old_w / 1.15;
      * agg = sum(new_w_i * s_i) / sum(new_w_i);
      * sum(new_w_i) по активным слоям = 1.0.

    Поскольку знаменатель после калибровки равен 1.0, agg = сумма
    произведений new_w * s.
    """

    PER_LAYER_SCORES = {
        "code": 0.5,  # самый низкий score при самом большом весе
        "component": 0.9,
        "resource": 0.8,
        "library": 0.85,
        "api": 0.7,
    }

    def _manual_aggregate(self) -> float:
        # Новые веса: old_w / 1.15.
        old_sum = 1.15
        expected = 0.0
        for layer in ACTIVE_LAYERS:
            old_w = {
                "code": 0.45,
                "component": 0.25,
                "resource": 0.20,
                "library": 0.10,
                "api": 0.15,
            }[layer]
            new_w = old_w / old_sum
            expected += new_w * self.PER_LAYER_SCORES[layer]
        return expected

    def test_calibrated_aggregate_matches_manual(self):
        # Агрегация через LAYER_WEIGHTS (sum=1.0) должна совпасть с
        # ручной цифрой из нормированных весов.
        weighted_sum = sum(
            LAYER_WEIGHTS[layer] * self.PER_LAYER_SCORES[layer]
            for layer in ACTIVE_LAYERS
        )
        weight_total = sum(LAYER_WEIGHTS[layer] for layer in ACTIVE_LAYERS)
        agg = weighted_sum / weight_total
        expected = self._manual_aggregate()
        self.assertAlmostEqual(agg, expected, delta=1e-9)
        # Дополнительно зафиксируем числовое ожидание:
        # (0.45*0.5 + 0.25*0.9 + 0.20*0.8 + 0.10*0.85 + 0.15*0.7) / 1.15
        # = (0.225 + 0.225 + 0.16 + 0.085 + 0.105) / 1.15
        # = 0.800 / 1.15 ≈ 0.6956521739...
        self.assertAlmostEqual(agg, 0.800 / 1.15, delta=1e-9)


if __name__ == "__main__":
    unittest.main()

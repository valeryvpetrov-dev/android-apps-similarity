#!/usr/bin/env python3
"""EXEC-082a-SCORING: новые представления кода в агрегации m_static_views.

Проверяется, что ``code_v4`` и ``code_v4_shingled``:
  * зарегистрированы в ``ALL_LAYERS``;
  * имеют явный вес ``0.0`` в ``LAYER_WEIGHTS`` (сигнал «подключён, но в
    default-скоринге не участвует до калибровки EXEC-086»);
  * присутствуют в ``ABLATION_CONFIGS`` как отдельные конфигурации и в
    составной ``all_code_variants``;
  * сравниваются через единый диспетчер ``compare_m_static_layer``,
    который не ломает поведение существующих set-слоёв.
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

from m_static_views import (
    ABLATION_CONFIGS,
    ALL_LAYERS,
    LAYER_WEIGHTS,
    compare_all,
    compare_m_static_layer,
)


def _make_v4_bundle(fingerprints: dict[str, str]) -> dict:
    """Собрать синтетический bundle в контракте code_view_v4."""
    return {
        "method_fingerprints": dict(fingerprints),
        "total_methods": len(fingerprints),
        "mode": "v4",
    }


class TestAllLayersRegistration(unittest.TestCase):
    """ALL_LAYERS включает новые имена."""

    def test_code_v4_in_all_layers(self):
        self.assertIn("code_v4", ALL_LAYERS)

    def test_code_v4_shingled_in_all_layers(self):
        self.assertIn("code_v4_shingled", ALL_LAYERS)


class TestLayerWeightsDefaults(unittest.TestCase):
    """Новые слои подключены с весом 0.0 — сигнал отключённости в default."""

    def test_code_v4_default_weight_zero(self):
        self.assertIn("code_v4", LAYER_WEIGHTS)
        self.assertEqual(LAYER_WEIGHTS["code_v4"], 0.0)

    def test_code_v4_shingled_default_weight_zero(self):
        self.assertIn("code_v4_shingled", LAYER_WEIGHTS)
        self.assertEqual(LAYER_WEIGHTS["code_v4_shingled"], 0.0)


class TestAblationConfigs(unittest.TestCase):
    """ABLATION_CONFIGS расширены тремя новыми вариантами."""

    def test_code_only_v4_has_single_layer(self):
        self.assertIn("code_only_v4", ABLATION_CONFIGS)
        self.assertEqual(ABLATION_CONFIGS["code_only_v4"], ["code_v4"])

    def test_code_only_v4_shingled_has_single_layer(self):
        self.assertIn("code_only_v4_shingled", ABLATION_CONFIGS)
        self.assertEqual(
            ABLATION_CONFIGS["code_only_v4_shingled"], ["code_v4_shingled"],
        )

    def test_all_code_variants_contains_three_layers_in_order(self):
        self.assertIn("all_code_variants", ABLATION_CONFIGS)
        self.assertEqual(
            ABLATION_CONFIGS["all_code_variants"],
            ["code", "code_v4", "code_v4_shingled"],
        )


class TestCompareMStaticLayerCodeV4(unittest.TestCase):
    """Диспетчер возвращает контракт {'score', 'status'} для code_v4."""

    def test_identical_bundles_give_score_one(self):
        fp = {"Lcom/example/A;->foo()V": "sh1:abc", "Lcom/example/A;->bar()V": "sh1:def"}
        bundle_a = _make_v4_bundle(fp)
        bundle_b = _make_v4_bundle(fp)
        result = compare_m_static_layer("code_v4", bundle_a, bundle_b)
        self.assertIn("score", result)
        self.assertIn("status", result)
        self.assertIsInstance(result["score"], float)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["status"], "fuzzy_ok")

    def test_disjoint_bundles_give_score_zero(self):
        bundle_a = _make_v4_bundle({"Lcom/example/A;->foo()V": "sh1:aaa"})
        bundle_b = _make_v4_bundle({"Lcom/example/B;->foo()V": "sh1:bbb"})
        result = compare_m_static_layer("code_v4", bundle_a, bundle_b)
        self.assertEqual(result["score"], 0.0)
        self.assertIn("status", result)


class TestCompareMStaticLayerCodeV4Shingled(unittest.TestCase):
    """Тот же контракт для code_v4_shingled."""

    def test_identical_bundles_give_score_one(self):
        fp = {"Lcom/example/A;->foo()V": "sh1:abc", "Lcom/example/A;->bar()V": "sh1:def"}
        bundle_a = _make_v4_bundle(fp)
        bundle_b = _make_v4_bundle(fp)
        result = compare_m_static_layer("code_v4_shingled", bundle_a, bundle_b)
        self.assertIn("score", result)
        self.assertIn("status", result)
        self.assertIsInstance(result["score"], float)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["status"], "fuzzy_ok")


class TestCompareMStaticLayerLegacySet(unittest.TestCase):
    """Набор-слои (``code``) проходят через тот же диспетчер без регрессии."""

    def test_code_layer_jaccard_on_sets(self):
        set_a = {"com.example.A", "com.example.B"}
        set_b = {"com.example.A", "com.example.C"}
        result = compare_m_static_layer("code", set_a, set_b)
        self.assertIn("score", result)
        self.assertIn("status", result)
        # Jaccard = |{A}| / |{A, B, C}| = 1/3.
        self.assertAlmostEqual(result["score"], 1.0 / 3.0, places=6)
        self.assertEqual(result["status"], "quick")


def _make_quick_features(
    code: set[str],
    component: set[str],
    resource: set[str],
    library: set[str],
) -> dict:
    return {
        "mode": "quick",
        "code": set(code),
        "component": set(component),
        "resource": set(resource),
        "library": set(library),
        "metadata": set(),
    }


class TestCompareAllApiAggregation(unittest.TestCase):
    """Поведение `api`-слоя в weighted aggregation `full_similarity_score`.

    DEEP-24-LIBRARY-REDUCED-UNIFY (2026-04-26): assert-ы по
    ``library_reduced_score`` обновлены под единую каноническую формулу
    из контракта v1 раздела 4.4 (``|(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|``).
    Ранее эти assert-ы проверяли локальную для ``compare_all`` weighted-avg
    реализацию, которая с волны 24 заменена на вызов
    ``library_reduced_score_canonical`` (одна формула во всех трёх точках
    вызова: ``compare_all``, ``pairwise_runner.calculate_set_scores``,
    GED-путь). Ассерты на ``full_similarity_score`` не меняются — он
    по-прежнему считается weighted-avg per-layer score-ов.

    Каноническая формула на фикстуре всех трёх кейсов:
      F_A = {code:same, component:left-only, resource:same, library:left-only}
      F_B = {code:same, component:right-only, resource:same, library:right-only}
      L = {library:left-only, library:right-only}
      ∩ \\ L = {code:same, resource:same} → |·| = 2
      ∪ \\ L = {code:same, component:left-only, component:right-only,
                resource:same} → |·| = 4
      library_reduced_score = 2/4 = 0.5
    Значение одинаковое в трёх кейсах: api-слой не вносит признаков (пуст
    с обеих сторон / cosine-сходство по chain не попадает в множества
    ``F_A`` / ``F_B``), поэтому на library_reduced_score не влияет.
    """

    _CANONICAL_LIBRARY_REDUCED = 2.0 / 4.0

    @staticmethod
    def _expected_full_score(per_layer_scores: dict[str, float]) -> float:
        """Динамически собрать expected full_similarity_score на основе
        текущих ``LAYER_WEIGHTS``.

        DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: после реальной калибровки
        на F-Droid v2 точные числа DEEP-19-нормировки (``0.8 / 1.15``)
        более не верны. Тест больше не привязан к конкретным весам,
        проверяет только формулу sum(w_i*s_i)/sum(w_i) по активным слоям.
        """
        weighted_sum = 0.0
        weight_total = 0.0
        for layer, score in per_layer_scores.items():
            w = LAYER_WEIGHTS.get(layer, 0.0)
            if w <= 0.0:
                continue
            weighted_sum += w * score
            weight_total += w
        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def test_all_present_keeps_existing_weighted_aggregation(self) -> None:
        features_a = _make_quick_features(
            code={"same"},
            component={"left-only"},
            resource={"same"},
            library={"left-only"},
        )
        features_b = _make_quick_features(
            code={"same"},
            component={"right-only"},
            resource={"same"},
            library={"right-only"},
        )

        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["code", "component", "resource", "library", "api"],
            api_chain_a={"A->B": 1.0},
            api_chain_b={"A->B": 1.0},
        )

        # Per-layer scores (вычисляются Jaccard / cosine):
        #   code = J({same}, {same}) = 1.0
        #   component = J({left-only}, {right-only}) = 0.0
        #   resource = J({same}, {same}) = 1.0
        #   library = J({left-only}, {right-only}) = 0.0
        #   api = cosine_chain({A->B:1.0}, {A->B:1.0}) = 1.0
        expected = self._expected_full_score({
            "code": 1.0,
            "component": 0.0,
            "resource": 1.0,
            "library": 0.0,
            "api": 1.0,
        })
        self.assertEqual(result["per_layer"]["api"]["status"], "markov_cosine")
        self.assertAlmostEqual(
            result["full_similarity_score"], expected, places=6,
        )
        self.assertAlmostEqual(
            result["library_reduced_score"], self._CANONICAL_LIBRARY_REDUCED, places=6,
        )

    def test_api_both_empty_is_excluded_and_other_weights_are_renormalized(self) -> None:
        features_a = _make_quick_features(
            code={"same"},
            component={"left-only"},
            resource={"same"},
            library={"left-only"},
        )
        features_b = _make_quick_features(
            code={"same"},
            component={"right-only"},
            resource={"same"},
            library={"right-only"},
        )

        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["code", "component", "resource", "library", "api"],
            api_chain_a=None,
            api_chain_b=None,
        )

        # api both_empty → исключается из агрегации; остаются 4 слоя.
        # code=1.0, component=0.0, resource=1.0, library=0.0.
        expected = self._expected_full_score({
            "code": 1.0,
            "component": 0.0,
            "resource": 1.0,
            "library": 0.0,
        })
        self.assertEqual(result["per_layer"]["api"]["status"], "both_empty")
        self.assertAlmostEqual(
            result["full_similarity_score"], expected, places=6,
        )
        self.assertAlmostEqual(
            result["library_reduced_score"], self._CANONICAL_LIBRARY_REDUCED, places=6,
        )

    def test_api_one_empty_stays_in_aggregation_with_zero_score(self) -> None:
        features_a = _make_quick_features(
            code={"same"},
            component={"left-only"},
            resource={"same"},
            library={"left-only"},
        )
        features_b = _make_quick_features(
            code={"same"},
            component={"right-only"},
            resource={"same"},
            library={"right-only"},
        )

        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["code", "component", "resource", "library", "api"],
            api_chain_a={"A->B": 1.0},
            api_chain_b=None,
        )

        # api one_empty → score=0, но слой включается в weighted sum.
        # code=1, component=0, resource=1, library=0, api=0.
        expected = self._expected_full_score({
            "code": 1.0,
            "component": 0.0,
            "resource": 1.0,
            "library": 0.0,
            "api": 0.0,
        })
        self.assertEqual(result["per_layer"]["api"]["status"], "one_empty")
        self.assertAlmostEqual(
            result["full_similarity_score"], expected, places=6,
        )
        self.assertAlmostEqual(
            result["library_reduced_score"], self._CANONICAL_LIBRARY_REDUCED, places=6,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

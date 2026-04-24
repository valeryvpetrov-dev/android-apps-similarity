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


if __name__ == "__main__":
    unittest.main(verbosity=2)

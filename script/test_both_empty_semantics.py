#!/usr/bin/env python3
"""DEEP-20-BOTH-EMPTY-AUDIT: единая семантика «обе стороны пусты» по слоям.

Критик волны 18 (`inbox/critics/deep-verification-2026-04-24.md`, раздел 3)
указал системный риск: шаблон «пустой сигнал = сильный сигнал сходства»
по слоям static view был непоследовательным. В разных модулях `both_empty`
обрабатывался по-разному:

* ``api_view.compare_api`` — ``score=0.0, status='both_empty'`` (уже
  исправлено волной 17 в `EXEC-API-EMPTY-FILTER`);
* ``code_view_v4.compare_code_v4`` — ``score=1.0, status='both_empty'``
  (дефект: «оба без кода → клоны»);
* ``code_view_v4_shingled.compare_code_v4_shingled`` — ``score=1.0,
  status='both_empty'`` (тот же дефект);
* ``component_view._jaccard`` — возвращает ``1.0`` на двух пустых
  множествах, из-за чего ``compare_components`` на пустых
  features даёт ``component_jaccard_score=1.0`` без статуса
  ``both_empty``;
* ``library_view_v2.compare_libraries_v2`` — ``jaccard=0.0``, но без
  статуса ``both_empty`` (downstream не может отфильтровать);
* ``resource_view_v2.compare_resource_view_v2`` — ``combined_score=0.0,
  status='empty'`` (имя ``empty`` вместо канонического ``both_empty``);
* ``signing_view.compare_signatures(None, None)`` — ``score=0.0,
  status='missing'`` (имя ``missing`` вместо ``both_missing``).

Единое решение DEEP-20-BOTH-EMPTY (decision-log `D-2026-04-DEEP-20-BOTH-EMPTY`):

1. Для всех per-layer сравнений, где оба входа пусты, контракт ==
   ``score=0.0, status='both_empty'`` (signing: ``both_missing``).
2. Агрегация ``compare_all`` исключает любой слой со
   ``status=='both_empty'`` из взвешенного среднего и перераспределяет
   веса (обобщение старой частной ветки для ``api``).

Тесты написаны до правок (TDD): на момент коммита они падают, что и
фиксирует дефект.
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

from code_view_v4 import compare_code_v4
from code_view_v4_shingled import compare_code_v4_shingled
from component_view import compare_components
from library_view_v2 import compare_libraries_v2
from m_static_views import (
    LAYER_WEIGHTS,
    _include_layer_in_weighted_score,
    compare_all,
    compare_m_static_layer,
)
from resource_view_v2 import compare_resource_view_v2
from signing_view import compare_signatures


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

def _make_quick_features(
    code: set[str] | None = None,
    component: set[str] | None = None,
    resource: set[str] | None = None,
    library: set[str] | None = None,
    metadata: set[str] | None = None,
) -> dict:
    """Собрать quick-feature bundle c заданными множествами."""
    return {
        "mode": "quick",
        "code": set(code) if code is not None else set(),
        "component": set(component) if component is not None else set(),
        "resource": set(resource) if resource is not None else set(),
        "library": set(library) if library is not None else set(),
        "metadata": set(metadata) if metadata is not None else set(),
    }


def _empty_v4_bundle() -> dict:
    return {"method_fingerprints": {}, "total_methods": 0, "mode": "v4"}


# ---------------------------------------------------------------------------
# 1. code_v4 / code_v4_shingled
# ---------------------------------------------------------------------------

class TestCodeV4BothEmptySemantics(unittest.TestCase):
    """both_empty по code_v4: 0.0 + флаг, а не 1.0."""

    def test_code_v4_both_empty_bundles_return_zero_with_flag(self):
        result = compare_code_v4(_empty_v4_bundle(), _empty_v4_bundle())
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "both_empty")
        self.assertIs(result.get("both_empty"), True)

    def test_code_v4_both_none_return_zero_with_flag(self):
        result = compare_code_v4(None, None)
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "both_empty")
        self.assertIs(result.get("both_empty"), True)

    def test_code_v4_shingled_both_empty_returns_zero_with_flag(self):
        result = compare_code_v4_shingled(
            _empty_v4_bundle(), _empty_v4_bundle(),
        )
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "both_empty")
        self.assertIs(result.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 2. component layer
# ---------------------------------------------------------------------------

class TestComponentBothEmptySemantics(unittest.TestCase):
    """component: на пустых features score=0.0 + флаг both_empty."""

    def test_component_layer_both_empty_returns_zero_with_flag(self):
        empty = {
            "activities": [], "services": [], "receivers": [], "providers": [],
            "permissions": set(), "features": set(), "package": "",
        }
        comparison = compare_components(empty, empty)
        self.assertEqual(comparison.get("component_jaccard_score"), 0.0)
        self.assertEqual(comparison.get("status"), "both_empty")
        self.assertIs(comparison.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 3. resource layer (quick + v2)
# ---------------------------------------------------------------------------

class TestResourceBothEmptySemantics(unittest.TestCase):
    """resource-слой: 0.0 + status=both_empty для обоих режимов."""

    def test_resource_quick_layer_both_empty_returns_zero_with_flag(self):
        features_a = _make_quick_features(resource=set())
        features_b = _make_quick_features(resource=set())
        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["resource"],
        )
        per = result["per_layer"]["resource"]
        self.assertEqual(per.get("score"), 0.0)
        self.assertEqual(per.get("status"), "both_empty")
        self.assertIs(per.get("both_empty"), True)

    def test_resource_v2_both_empty_returns_zero_with_canonical_status(self):
        empty = {
            "res_strings": set(),
            "res_drawables": set(),
            "res_layouts": set(),
            "assets_bin": set(),
            "icon_phash": None,
            "mode": "v2",
        }
        result = compare_resource_view_v2(empty, empty)
        self.assertEqual(result.get("combined_score"), 0.0)
        self.assertEqual(result.get("status"), "both_empty")
        self.assertIs(result.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 4. metadata layer (quick)
# ---------------------------------------------------------------------------

class TestMetadataBothEmptySemantics(unittest.TestCase):
    """metadata-слой: 0.0 + status=both_empty при пустых входах."""

    def test_metadata_layer_both_empty_returns_zero_with_flag(self):
        features_a = _make_quick_features(metadata=set())
        features_b = _make_quick_features(metadata=set())
        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["metadata"],
        )
        per = result["per_layer"]["metadata"]
        self.assertEqual(per.get("score"), 0.0)
        self.assertEqual(per.get("status"), "both_empty")
        self.assertIs(per.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 5. library layer (quick + v2)
# ---------------------------------------------------------------------------

class TestLibraryBothEmptySemantics(unittest.TestCase):
    """library: 0.0 + both_empty в обоих режимах (quick set и v2 dict)."""

    def test_library_quick_layer_both_empty_returns_zero_with_flag(self):
        features_a = _make_quick_features(library=set())
        features_b = _make_quick_features(library=set())
        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["library"],
        )
        per = result["per_layer"]["library"]
        self.assertEqual(per.get("score"), 0.0)
        self.assertEqual(per.get("status"), "both_empty")
        self.assertIs(per.get("both_empty"), True)

    def test_library_v2_compare_both_empty_returns_zero_with_flag(self):
        empty_v2 = {"libraries": {}, "total_packages": 0, "v2": True}
        comparison = compare_libraries_v2(empty_v2, empty_v2)
        self.assertEqual(comparison.get("jaccard"), 0.0)
        self.assertEqual(comparison.get("status"), "both_empty")
        self.assertIs(comparison.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 6. signing layer
# ---------------------------------------------------------------------------

class TestSigningBothMissingSemantics(unittest.TestCase):
    """signing: оба None → 0.0 + status='both_missing' + флаг."""

    def test_signing_layer_both_missing_returns_zero_with_warning(self):
        result = compare_signatures(None, None)
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "both_missing")
        self.assertIs(result.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 7. Агрегация: both_empty слой исключается, веса перераспределяются
# ---------------------------------------------------------------------------

class TestBothEmptyExclusionInAggregation(unittest.TestCase):
    """compare_all исключает любой слой со status=='both_empty' из весов."""

    def test_include_helper_generalized_to_any_layer(self):
        # Любой слой со status=='both_empty' теперь исключается.
        self.assertFalse(
            _include_layer_in_weighted_score(
                "component", {"status": "both_empty", "score": 0.0},
            )
        )
        self.assertFalse(
            _include_layer_in_weighted_score(
                "library", {"status": "both_empty", "score": 0.0},
            )
        )
        self.assertFalse(
            _include_layer_in_weighted_score(
                "resource", {"status": "both_empty", "score": 0.0},
            )
        )
        # one_empty и обычный статус — остаются.
        self.assertTrue(
            _include_layer_in_weighted_score(
                "api", {"status": "one_empty", "score": 0.0},
            )
        )
        self.assertTrue(
            _include_layer_in_weighted_score(
                "code", {"status": "jaccard_dex", "score": 0.42},
            )
        )

    def test_resource_both_empty_excluded_weights_renormalized(self):
        """resource both_empty исключается — веса code/component/library
        перераспределяются, full_similarity_score считается только по ним."""
        features_a = _make_quick_features(
            code={"same"},
            component={"left-only"},
            resource=set(),
            library={"left-only"},
        )
        features_b = _make_quick_features(
            code={"same"},
            component={"right-only"},
            resource=set(),
            library={"right-only"},
        )
        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=["code", "component", "resource", "library", "api"],
            api_chain_a={"A->B": 1.0},
            api_chain_b={"A->B": 1.0},
        )
        self.assertEqual(
            result["per_layer"]["resource"]["status"], "both_empty",
        )
        # DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: после реальной калибровки
        # точные числа DEEP-19-нормировки (``0.60/0.95``) более не верны.
        # Тест проверяет инвариант: resource исключается, остальные слои
        # дают full_similarity_score = sum(w_i * s_i) / sum(w_i) только
        # по {code, component, library, api}. Per-layer scores:
        #   code = J({same},{same}) = 1.0
        #   component = J({left-only},{right-only}) = 0.0
        #   library = J({left-only},{right-only}) = 0.0
        #   api = cosine_chain({A->B:1},{A->B:1}) = 1.0
        per_layer_scores = {
            "code": 1.0,
            "component": 0.0,
            "library": 0.0,
            "api": 1.0,
        }
        weighted_sum = sum(
            LAYER_WEIGHTS.get(layer, 0.0) * score
            for layer, score in per_layer_scores.items()
        )
        weight_total = sum(
            LAYER_WEIGHTS.get(layer, 0.0)
            for layer in per_layer_scores
            if LAYER_WEIGHTS.get(layer, 0.0) > 0.0
        )
        expected = weighted_sum / weight_total if weight_total > 0 else 0.0
        self.assertAlmostEqual(
            result["full_similarity_score"], expected, places=6,
        )


if __name__ == "__main__":
    unittest.main()

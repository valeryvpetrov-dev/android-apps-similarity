#!/usr/bin/env python3
"""DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: тесты grid-search калибровки.

Калибровка LAYER_WEIGHTS на labelled-парах F-Droid v2 (350 APK):
clone-пары (одинаковый package, разные versionCode) vs non-clone (разные
package + разные signing).

Калибровка через grid-search по 4 активным слоям ``code/component/
resource/library`` (квик-mode без androguard) с шагом 0.05, sum=1.0.
Метрика — F1 по бинарному классификатору (full_similarity_score >=
threshold). Train/test split 70/30 c фиксированным seed=42.

Тесты (≥3):
  * (a) контракт ``calibrate_layer_weights_grid`` возвращает dict с
    ключами ``weights``, ``train_F1``, ``test_F1``, ``n_train_pairs``,
    ``n_test_pairs``;
  * (b) на synthetic-корпусе с known-clone и known-non-clone парами
    optimal-веса максимизируют F1;
  * (c) детерминированность: фиксированный seed → идентичный output.
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

from calibrate_layer_weights_fdroid import (  # noqa: E402
    ACTIVE_LAYERS,
    GROUND_TRUTH_LABEL_CLONE,
    GROUND_TRUTH_LABEL_NON_CLONE,
    calibrate_layer_weights_grid,
    iter_grid_weights,
    score_pair_with_weights,
)


def _make_synthetic_pair_features(*, code_a: set, component_a: set,
                                   resource_a: set, library_a: set,
                                   code_b: set, component_b: set,
                                   resource_b: set, library_b: set) -> dict:
    """Сформировать пару records-словарей, совместимых с
    score_pair_with_weights (мини-формат для синтетических тестов)."""
    return {
        "a": {
            "code": code_a,
            "component": component_a,
            "resource": resource_a,
            "library": library_a,
        },
        "b": {
            "code": code_b,
            "component": component_b,
            "resource": resource_b,
            "library": library_b,
        },
    }


class TestCalibrateContract(unittest.TestCase):
    """(a) Контракт результата calibrate_layer_weights_grid."""

    def test_returns_required_keys(self):
        # Минимальный synthetic-корпус: 1 clone + 1 non-clone.
        # Тест проверяет, что функция возвращает корректный shape, а не
        # содержательный смысл — проверка содержательности — в (b).
        pairs = [
            (
                _make_synthetic_pair_features(
                    code_a={"a", "b", "c"}, component_a={"x"},
                    resource_a={"r1"}, library_a={"libA"},
                    code_b={"a", "b", "c"}, component_b={"x"},
                    resource_b={"r1"}, library_b={"libA"},
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ),
            (
                _make_synthetic_pair_features(
                    code_a={"a", "b", "c"}, component_a={"x"},
                    resource_a={"r1"}, library_a={"libA"},
                    code_b={"x", "y", "z"}, component_b={"q"},
                    resource_b={"r9"}, library_b={"libZ"},
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ),
        ]
        result = calibrate_layer_weights_grid(
            pairs,
            grid_step=0.25,  # крупный шаг для скорости — 35 точек
            seed=42,
            test_size=0.5,
        )
        for key in ("weights", "train_F1", "test_F1",
                    "n_train_pairs", "n_test_pairs",
                    "calibration_method", "grid_step"):
            self.assertIn(key, result, f"missing key {key}")
        for layer in ACTIVE_LAYERS:
            self.assertIn(layer, result["weights"])
        self.assertAlmostEqual(
            sum(result["weights"][layer] for layer in ACTIVE_LAYERS),
            1.0,
            delta=1e-6,
        )

    def test_iter_grid_weights_step_05(self):
        # Шаг 0.05 на 4 слоях: число точек симплекса == C(4-1+20, 3) = 1771.
        # Шаг 0.5 на 4 слоях: точки симплекса c суммой 1 — 10 (1.0,0,0,0,
        # 0.5,0.5,0,0 и перестановки + 0.5,0,0.5,0 итд).
        all_w = list(iter_grid_weights(grid_step=0.5, n_layers=4))
        for w in all_w:
            self.assertAlmostEqual(sum(w), 1.0, delta=1e-9)
            self.assertEqual(len(w), 4)
            for v in w:
                self.assertGreaterEqual(v, 0.0)
        self.assertGreaterEqual(len(all_w), 4)


class TestCalibrateOnSynthetic(unittest.TestCase):
    """(b) Optimal weights на synthetic-корпусе должны максимизировать F1."""

    def test_synthetic_separates_via_code_layer(self):
        # Сценарий: ВСЕ clone-пары имеют идентичные code-features,
        # но разные component/resource/library. ВСЕ non-clone-пары
        # имеют разные code, но идентичные component/resource/library.
        # Optimal weights в этом сценарии: code должен иметь весомую долю
        # (в идеале — большую), потому что только code-слой различает
        # классы.
        pairs = []
        # Clone-пары (n=8): идентичный code, шумный остаток.
        for i in range(8):
            pairs.append((
                _make_synthetic_pair_features(
                    code_a={f"shared_code_{i}_a", f"shared_code_{i}_b"},
                    component_a={f"comp_a_{i}_1", f"comp_a_{i}_2"},
                    resource_a={f"res_a_{i}_1"},
                    library_a={f"lib_a_{i}_1"},
                    code_b={f"shared_code_{i}_a", f"shared_code_{i}_b"},
                    component_b={f"comp_b_{i}_1", f"comp_b_{i}_2"},
                    resource_b={f"res_b_{i}_1"},
                    library_b={f"lib_b_{i}_1"},
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ))
        # Non-clone (n=8): разный code, идентичный шум.
        common_component = {"shared_comp_x", "shared_comp_y"}
        common_resource = {"shared_res_x"}
        common_library = {"shared_lib_x"}
        for i in range(8):
            pairs.append((
                _make_synthetic_pair_features(
                    code_a={f"unique_a_{i}_1", f"unique_a_{i}_2"},
                    component_a=common_component,
                    resource_a=common_resource,
                    library_a=common_library,
                    code_b={f"unique_b_{i}_1", f"unique_b_{i}_2"},
                    component_b=common_component,
                    resource_b=common_resource,
                    library_b=common_library,
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ))
        result = calibrate_layer_weights_grid(
            pairs,
            grid_step=0.25,
            seed=42,
            test_size=0.5,
        )
        # На таком сепарабельном датасете F1 должен быть высокий.
        self.assertGreaterEqual(result["train_F1"], 0.8)
        # Выбранный вес code должен быть положительным.
        self.assertGreater(result["weights"]["code"], 0.0)
        # И существенно больше веса component/resource/library
        # (потому что они шумные).
        self.assertGreater(
            result["weights"]["code"],
            max(result["weights"]["component"],
                result["weights"]["resource"],
                result["weights"]["library"]),
            msg=f"code weight should dominate, got {result['weights']}",
        )


class TestCalibrateDeterminism(unittest.TestCase):
    """(c) Фиксированный seed → идентичный output."""

    def test_same_seed_same_result(self):
        pairs = []
        for i in range(6):
            pairs.append((
                _make_synthetic_pair_features(
                    code_a={f"c{i}_a", f"c{i}_b"}, component_a={f"x{i}"},
                    resource_a={f"r{i}"}, library_a={f"l{i}"},
                    code_b={f"c{i}_a", f"c{i}_b"}, component_b={f"x{i}"},
                    resource_b={f"r{i}"}, library_b={f"l{i}"},
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ))
            pairs.append((
                _make_synthetic_pair_features(
                    code_a={f"u{i}"}, component_a={f"unk{i}"},
                    resource_a={f"unr{i}"}, library_a={f"unl{i}"},
                    code_b={f"u_other_{i}"}, component_b={f"unk_o_{i}"},
                    resource_b={f"unr_o_{i}"}, library_b={f"unl_o_{i}"},
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ))
        r1 = calibrate_layer_weights_grid(pairs, grid_step=0.25, seed=42)
        r2 = calibrate_layer_weights_grid(pairs, grid_step=0.25, seed=42)
        self.assertEqual(r1["weights"], r2["weights"])
        self.assertEqual(r1["train_F1"], r2["train_F1"])
        self.assertEqual(r1["test_F1"], r2["test_F1"])
        # Другой seed — может дать другой split (не обязан, но имеем
        # право).
        r3 = calibrate_layer_weights_grid(pairs, grid_step=0.25, seed=7)
        # Output обязан быть валиден независимо от seed.
        self.assertAlmostEqual(
            sum(r3["weights"][layer] for layer in ACTIVE_LAYERS),
            1.0,
            delta=1e-6,
        )


class TestScorePairWithWeights(unittest.TestCase):
    """Helper score_pair_with_weights: единая формула per-layer score-ов."""

    def test_score_pair_identity(self):
        pair = _make_synthetic_pair_features(
            code_a={"x"}, component_a={"y"}, resource_a={"z"},
            library_a={"w"}, code_b={"x"}, component_b={"y"},
            resource_b={"z"}, library_b={"w"},
        )
        weights = {"code": 0.25, "component": 0.25,
                   "resource": 0.25, "library": 0.25}
        score = score_pair_with_weights(pair, weights)
        self.assertAlmostEqual(score, 1.0, delta=1e-9)

    def test_score_pair_disjoint(self):
        pair = _make_synthetic_pair_features(
            code_a={"x"}, component_a={"y"}, resource_a={"z"},
            library_a={"w"}, code_b={"a"}, component_b={"b"},
            resource_b={"c"}, library_b={"d"},
        )
        weights = {"code": 0.25, "component": 0.25,
                   "resource": 0.25, "library": 0.25}
        score = score_pair_with_weights(pair, weights)
        self.assertAlmostEqual(score, 0.0, delta=1e-9)


if __name__ == "__main__":
    unittest.main()

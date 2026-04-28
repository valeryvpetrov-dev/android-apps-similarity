#!/usr/bin/env python3
"""DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED: тесты grid-search калибровки на
смешанном корпусе F-Droid v2 + DEEP-30 inject-пары.

Гипотеза DEEP-31: при добавлении inject-пар DEEP-30 в train-set
калибровка LAYER_WEIGHTS поднимет вес ``code`` существенно выше DEEP-27
0.05 (минимум >0.10), потому что в F-Droid v2 не было примеров, в которых
code-слой различает clone от non-clone, а в inject-парах он различает.

Тесты (≥3):
  * (a) контракт ``calibrate_layer_weights_mixed`` возвращает dict с
    ключами ``weights``, ``train_F1``, ``test_F1``, ``weight_delta_vs_deep27``;
  * (b) на synthetic-корпусе с known-clone (включая code-mod пары, где
    различает только code-слой) и known-non-clone — выбранный вес
    ``code > 0.10`` (выше DEEP-27 0.05);
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
    GROUND_TRUTH_LABEL_CLONE,
    GROUND_TRUTH_LABEL_NON_CLONE,
)
from calibrate_layer_weights_mixed import (  # noqa: E402
    ACTIVE_LAYERS_MIXED,
    DEEP27_WEIGHTS_REFERENCE,
    calibrate_layer_weights_mixed,
)


def _make_synthetic_pair(*, code_a, component_a, resource_a, library_a,
                         code_b, component_b, resource_b, library_b):
    return {
        "a": {
            "code": set(code_a),
            "component": set(component_a),
            "resource": set(resource_a),
            "library": set(library_a),
        },
        "b": {
            "code": set(code_b),
            "component": set(component_b),
            "resource": set(resource_b),
            "library": set(library_b),
        },
    }


class TestCalibrateMixedContract(unittest.TestCase):
    """(a) Контракт результата calibrate_layer_weights_mixed."""

    def test_returns_required_keys(self):
        # Минимальный синтетический корпус.
        pairs = [
            (
                _make_synthetic_pair(
                    code_a=("a", "b"), component_a=("x",),
                    resource_a=("r",), library_a=("lib",),
                    code_b=("a", "b"), component_b=("x",),
                    resource_b=("r",), library_b=("lib",),
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ),
            (
                _make_synthetic_pair(
                    code_a=("a", "b"), component_a=("x",),
                    resource_a=("r",), library_a=("lib",),
                    code_b=("u", "v"), component_b=("y",),
                    resource_b=("s",), library_b=("lab",),
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ),
        ]
        result = calibrate_layer_weights_mixed(
            pairs,
            grid_step=0.25,
            seed=42,
            test_size=0.5,
        )
        for key in ("weights", "train_F1", "test_F1",
                    "n_train_pairs", "n_test_pairs",
                    "weight_delta_vs_deep27"):
            self.assertIn(key, result, f"missing key {key}")
        # weights суммируются в 1.0 на ACTIVE_LAYERS_MIXED.
        self.assertAlmostEqual(
            sum(result["weights"][layer] for layer in ACTIVE_LAYERS_MIXED),
            1.0,
            delta=1e-6,
        )
        # weight_delta_vs_deep27 — dict layer → delta.
        for layer in ACTIVE_LAYERS_MIXED:
            self.assertIn(layer, result["weight_delta_vs_deep27"])
        # Проверка: дельта = (новый вес) - (DEEP-27 reference) для каждого слоя.
        for layer in ACTIVE_LAYERS_MIXED:
            expected = (result["weights"][layer]
                        - DEEP27_WEIGHTS_REFERENCE.get(layer, 0.0))
            self.assertAlmostEqual(
                result["weight_delta_vs_deep27"][layer],
                expected,
                delta=1e-9,
            )


class TestCodeWeightLiftsOnInjectScenario(unittest.TestCase):
    """(b) На сценарии с inject-парами вес code > 0.10 (DEEP-27 = 0.05)."""

    def test_code_weight_above_deep27_when_inject_pairs_added(self):
        # Сценарий: смешанный корпус, в котором именно code-слой даёт
        # разделяющий сигнал. Компоненты/ресурсы/библиотеки специально
        # зашумлены так, что давая им большой вес — F1 деградирует.
        #
        # Фрагмент 1 (clone, F-Droid v2 — version-пары одного package):
        #   * code: 100% (одинаковые method-id, как у inject — для теста
        #     это допустимое упрощение, реальная F-Droid v2 будет ниже);
        #   * component/resource/library: 100% (manifest+ресурсы общие
        #     между версиями).
        #
        # Фрагмент 2 (inject, DEEP-30 — original vs original+inject):
        #   * code: 100%;
        #   * component/resource/library: 100%.
        #   Это идентично фрагменту 1 по содержательной структуре.
        #
        # Фрагмент 3 (non-clone — два РАЗНЫХ приложения, но обе
        # используют один и тот же набор runtime-библиотек / AndroidX /
        # одинаковые манифестные toolkit-токены, что характерно для
        # F-Droid v2):
        #   * code: 0% (method-id уникальны, разные классы);
        #   * component/resource/library: HIGH overlap (общие AndroidX
        #     компоненты, layout-токены AppCompat, lib_abi:armeabi-v7a и
        #     META-INF: SF/MF/RSA — это даёт Jaccard 0.6+ на каждом из
        #     этих слоёв, имитируя реальный F-Droid corpus).
        #
        # Таким образом, ТОЛЬКО code-слой различает clone от non-clone,
        # а grid-search обязан положительно взвесить code, чтобы не
        # путать non-clone с одинаковым component/resource/library
        # шумом.
        pairs = []
        # Фрагмент 1: F-Droid v2 clone (n=20).
        for i in range(20):
            methods = {f"FD_M_{i}_{j}" for j in range(8)}
            comp = {f"comp_{i}", "androidx", "kotlin", "appcompat"}
            res = {"res_layout", "res_drawable", "res_values", f"res_{i}"}
            lib = {"lib_abi:arm64-v8a", "meta_inf:SF", "meta_inf:MF",
                   f"lib_extra_{i}"}
            pairs.append((
                _make_synthetic_pair(
                    code_a=methods,
                    component_a=comp,
                    resource_a=res,
                    library_a=lib,
                    code_b=methods,
                    component_b=comp,
                    resource_b=res,
                    library_b=lib,
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ))
        # Фрагмент 2: inject-пары DEEP-30 (n=20).
        for i in range(20):
            methods = {f"INJECT_M_{i}_{j}" for j in range(8)}
            comp = {f"inj_comp_{i}", "androidx", "kotlin", "appcompat"}
            res = {"res_layout", "res_drawable", "res_values", f"res_inj_{i}"}
            lib = {"lib_abi:arm64-v8a", "meta_inf:SF", "meta_inf:MF",
                   f"lib_inj_{i}"}
            pairs.append((
                _make_synthetic_pair(
                    code_a=methods,
                    component_a=comp,
                    resource_a=res,
                    library_a=lib,
                    code_b=methods,
                    component_b=comp,
                    resource_b=res,
                    library_b=lib,
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ))
        # Фрагмент 3: non-clone (n=40), code разный, компоненты/
        # ресурсы/library — высокий overlap (общие AndroidX-токены).
        # Jaccard для component = |{androidx,kotlin,appcompat}| /
        # |{nc_a_i, nc_b_i, androidx, kotlin, appcompat}| = 3/5 = 0.6.
        # Jaccard для resource = |{res_layout, res_drawable, res_values}|
        # / |{nr_a_i, nr_b_i, res_layout, res_drawable, res_values}| =
        # 3/5 = 0.6. Jaccard для library = аналогично 0.6.
        for i in range(40):
            pairs.append((
                _make_synthetic_pair(
                    code_a={f"non_a_{i}_{j}" for j in range(5)},
                    component_a={f"nc_a_{i}", "androidx", "kotlin",
                                  "appcompat"},
                    resource_a={f"nr_a_{i}", "res_layout", "res_drawable",
                                 "res_values"},
                    library_a={f"nl_a_{i}", "lib_abi:arm64-v8a",
                                "meta_inf:SF", "meta_inf:MF"},
                    code_b={f"non_b_{i}_{j}" for j in range(5)},
                    component_b={f"nc_b_{i}", "androidx", "kotlin",
                                  "appcompat"},
                    resource_b={f"nr_b_{i}", "res_layout", "res_drawable",
                                 "res_values"},
                    library_b={f"nl_b_{i}", "lib_abi:arm64-v8a",
                                "meta_inf:SF", "meta_inf:MF"},
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ))
        result = calibrate_layer_weights_mixed(
            pairs,
            grid_step=0.05,
            seed=42,
            test_size=0.3,
        )
        # На таком сепарабельном датасете F1 должен быть высокий.
        self.assertGreaterEqual(result["train_F1"], 0.85)
        # Главное условие гипотезы DEEP-31: вес code > DEEP-27 0.05,
        # то есть >= 0.10 (с шагом 0.05 это первый шаг вверх от 0.05).
        self.assertGreater(
            result["weights"]["code"],
            0.05,
            msg=(f"code weight should rise above DEEP-27 baseline 0.05 "
                 f"when inject-пары добавлены, got "
                 f"{result['weights']['code']}"),
        )
        # Контроль: weight_delta_vs_deep27["code"] > 0.
        self.assertGreater(
            result["weight_delta_vs_deep27"]["code"],
            0.0,
            msg="delta for code must be positive (inject lifts code weight)",
        )


class TestCalibrateMixedDeterminism(unittest.TestCase):
    """(c) Фиксированный seed → идентичный output."""

    def test_same_seed_same_result(self):
        pairs = []
        for i in range(8):
            pairs.append((
                _make_synthetic_pair(
                    code_a=({f"c_{i}", "common_m"}),
                    component_a=({f"x_{i}"}),
                    resource_a=({f"r_{i}"}),
                    library_a=({f"l_{i}"}),
                    code_b=({f"c_{i}", "common_m"}),
                    component_b=({f"x_{i}"}),
                    resource_b=({f"r_{i}"}),
                    library_b=({f"l_{i}"}),
                ),
                GROUND_TRUTH_LABEL_CLONE,
            ))
            pairs.append((
                _make_synthetic_pair(
                    code_a=({f"u_{i}"}),
                    component_a=({f"unkx_{i}"}),
                    resource_a=({f"unkr_{i}"}),
                    library_a=({f"unkl_{i}"}),
                    code_b=({f"u_other_{i}"}),
                    component_b=({f"unkx_o_{i}"}),
                    resource_b=({f"unkr_o_{i}"}),
                    library_b=({f"unkl_o_{i}"}),
                ),
                GROUND_TRUTH_LABEL_NON_CLONE,
            ))
        r1 = calibrate_layer_weights_mixed(pairs, grid_step=0.25, seed=42)
        r2 = calibrate_layer_weights_mixed(pairs, grid_step=0.25, seed=42)
        self.assertEqual(r1["weights"], r2["weights"])
        self.assertEqual(r1["train_F1"], r2["train_F1"])
        self.assertEqual(r1["test_F1"], r2["test_F1"])
        self.assertEqual(r1["threshold"], r2["threshold"])


if __name__ == "__main__":
    unittest.main()

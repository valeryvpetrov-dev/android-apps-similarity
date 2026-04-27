"""Tests for DEEP-28-LAYER-WEIGHTS-PROD-APPLY.

DEEP-27 откалибровал веса grid-search на F-Droid v2 и положил их в
`experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json`,
а также скопировал в canonical-путь
`experiments/artifacts/DEEP-22-LAYER-WEIGHTS-EXTERNALIZED/calibrated_weights.json`,
который читается через `m_static_views.CALIBRATED_WEIGHTS_PATH`.

Эти тесты фиксируют, что production действительно использует новые веса
DEEP-27 (component=0.60 / library=0.35 / code=0.05 / resource=0.0 / api=0.0)
и что старая нормировка DEEP-19 (code=0.45/1.15 ≈ 0.391 и так далее) больше
не активна.

(a) `LAYER_WEIGHTS["component"] >= 0.5` — новый вес component (0.60) из DEEP-27.
(b) `LAYER_WEIGHTS["code"] <= 0.10` — новый вес code (0.05), был 0.391 в DEEP-19.
(c) sanity replay на synthetic паре: code=1.0, component=0.0; full_similarity_score
    со старыми весами (DEEP-19) ≈ 0.39+, с новыми (DEEP-27) = 0.05 — сильно ниже.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from script.m_static_views import (
    CALIBRATED_WEIGHTS_PATH,
    LAYER_WEIGHTS,
    _LAYER_WEIGHTS_FALLBACK,
)


def _full_score_from_per_layer(
    per_layer_scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Re-implement compare_all weighted-average aggregation for tests.

    Формула (см. m_static_views.compare_all):
    full_similarity_score = sum_l(w_l * score_l) / sum_l(w_l)
    по слоям, у которых задан вес и есть score (status != "both_empty",
    но для синтетических данных это не проверяется).
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for layer, score in per_layer_scores.items():
        weight = weights.get(layer)
        if weight is None or weight <= 0.0:
            continue
        weighted_sum += weight * score
        weight_total += weight
    return weighted_sum / weight_total if weight_total > 0.0 else 0.0


class TestLayerWeightsProdApply(unittest.TestCase):
    def test_component_weight_is_dominant_after_deep27(self) -> None:
        """component >= 0.5 — DEEP-27 сделал его доминирующим (0.60).

        Раньше (DEEP-19): component = 0.25 / 1.15 ≈ 0.217.
        После DEEP-27: component = 0.60.
        """
        self.assertIn("component", LAYER_WEIGHTS)
        self.assertGreaterEqual(
            LAYER_WEIGHTS["component"],
            0.5,
            f"component weight {LAYER_WEIGHTS['component']} < 0.5; "
            "production читает старые веса DEEP-19 вместо DEEP-27",
        )

    def test_code_weight_is_low_after_deep27(self) -> None:
        """code <= 0.10 — DEEP-27 снизил вес code с 0.391 до 0.05.

        Quick-mode `code` извлекает string-set имён классов без TLSH/v4-
        фингерпринта, поэтому на F-Droid v2 без обфускации Jaccard слабо
        разделяет clone от non-clone. Калибровка дала код низкий вес.
        """
        self.assertIn("code", LAYER_WEIGHTS)
        self.assertLessEqual(
            LAYER_WEIGHTS["code"],
            0.10,
            f"code weight {LAYER_WEIGHTS['code']} > 0.10; "
            "production не применил новые веса DEEP-27",
        )

    def test_synthetic_replay_old_vs_new_weights(self) -> None:
        """Sanity replay: code=1.0, component=0.0 → старые ≈ 0.39+, новые ≈ 0.05.

        Synthetic-пара, где совпадают только code-имена классов (1.0), но
        компоненты различны (0.0). Остальные слои не задаём.

        Со старыми весами DEEP-19 (code=0.391) full_similarity_score
        получает существенный вклад от code и оказывается >= 0.30.

        С новыми весами DEEP-27 (code=0.05, component=0.60) код почти не
        вносит вклад, а component=0.0 доминирует — итог получается <= 0.10.
        """
        per_layer = {"code": 1.0, "component": 0.0}

        old_score = _full_score_from_per_layer(per_layer, _LAYER_WEIGHTS_FALLBACK)
        new_score = _full_score_from_per_layer(per_layer, LAYER_WEIGHTS)

        # Старые веса DEEP-19: ratio code:component = 0.391 : 0.217;
        # full_score = (0.391 * 1.0 + 0.217 * 0.0) / (0.391 + 0.217) ≈ 0.643.
        self.assertGreaterEqual(
            old_score,
            0.30,
            f"old DEEP-19 weights expected >= 0.30 on code=1/component=0, got {old_score}",
        )

        # Новые веса DEEP-27: code=0.05, component=0.60;
        # full_score = (0.05 * 1.0 + 0.60 * 0.0) / (0.05 + 0.60) ≈ 0.077.
        self.assertLessEqual(
            new_score,
            0.10,
            f"new DEEP-27 weights expected <= 0.10 on code=1/component=0, got {new_score}",
        )

        # И главное — новые веса заметно ниже старых на этой паре.
        self.assertLess(
            new_score,
            old_score - 0.20,
            f"new score {new_score} should be much lower than old {old_score}",
        )

    def test_canonical_weights_file_matches_deep27_artifact(self) -> None:
        """Canonical CALIBRATED_WEIGHTS_PATH совпадает с DEEP-27 artifact.

        Это то самое «production использует DEEP-27» — проверяем не только
        в памяти (LAYER_WEIGHTS), но и на диске: оба JSON-файла идентичны
        по полю `weights` и по `snapshot_id`.
        """
        deep27_path = (
            CALIBRATED_WEIGHTS_PATH.parent.parent
            / "DEEP-27-LAYER-WEIGHTS-FDROID"
            / "calibrated_weights.json"
        )
        self.assertTrue(deep27_path.exists(), f"missing DEEP-27 artifact {deep27_path}")
        self.assertTrue(
            CALIBRATED_WEIGHTS_PATH.exists(),
            f"missing canonical {CALIBRATED_WEIGHTS_PATH}",
        )
        canonical = json.loads(CALIBRATED_WEIGHTS_PATH.read_text(encoding="utf-8"))
        deep27 = json.loads(deep27_path.read_text(encoding="utf-8"))
        self.assertEqual(canonical["weights"], deep27["weights"])
        self.assertEqual(canonical.get("snapshot_id"), deep27.get("snapshot_id"))


if __name__ == "__main__":
    unittest.main()

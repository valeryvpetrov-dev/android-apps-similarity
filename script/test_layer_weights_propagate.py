"""Tests for DEEP-22-LAYER-WEIGHTS-PROPAGATE.

Проверяют, что:
1. JSON-артефакт ``calibrated_weights.json`` загружается через _load_layer_weights.
2. При отсутствии файла — fallback на hard-coded значения с warning.
3. При повреждённом JSON — fallback с warning, не падает.
4. Текущий файл совпадает по сумме непустых весов с hard-coded fallback (sanity).
"""

from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from script.m_static_views import (
    CALIBRATED_WEIGHTS_PATH,
    LAYER_WEIGHTS,
    _LAYER_WEIGHTS_FALLBACK,
    _load_layer_weights,
)


class TestLayerWeightsPropagate(unittest.TestCase):
    def test_calibrated_weights_file_exists_in_artifacts(self) -> None:
        """Артефакт реально лежит на диске после волны 22."""
        self.assertTrue(
            CALIBRATED_WEIGHTS_PATH.exists(),
            f"missing {CALIBRATED_WEIGHTS_PATH}",
        )

    def test_loaded_weights_match_fallback_after_externalisation(self) -> None:
        """Externalised JSON численно совпадает с hard-coded fallback (refactor, не feat)."""
        for layer, expected in _LAYER_WEIGHTS_FALLBACK.items():
            self.assertAlmostEqual(LAYER_WEIGHTS[layer], expected, places=10)

    def test_load_falls_back_with_warning_when_file_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "no-such-file.json"
            with self.assertLogs(level=logging.WARNING) as cm:
                weights = _load_layer_weights(missing_path)
            self.assertEqual(weights, _LAYER_WEIGHTS_FALLBACK)
            self.assertTrue(
                any("not found" in msg for msg in cm.output),
                f"warning about missing file expected, got {cm.output}",
            )

    def test_load_falls_back_with_warning_when_json_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            broken_path = Path(tmp) / "broken.json"
            broken_path.write_text("{not valid json", encoding="utf-8")
            with self.assertLogs(level=logging.WARNING) as cm:
                weights = _load_layer_weights(broken_path)
            self.assertEqual(weights, _LAYER_WEIGHTS_FALLBACK)
            self.assertTrue(
                any("invalid" in msg for msg in cm.output),
                f"warning about invalid JSON expected, got {cm.output}",
            )


if __name__ == "__main__":
    unittest.main()

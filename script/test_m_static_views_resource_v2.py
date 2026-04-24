#!/usr/bin/env python3
"""EXEC-R_resource_v2-INTEGRATION: слой ``resource_v2`` в агрегации
m_static_views.

Проверяется, что ``resource_v2``:
  * зарегистрирован в ``ALL_LAYERS``;
  * имеет явный вес ``0.0`` в ``LAYER_WEIGHTS`` (сигнал «подключено, не
    активировано до калибровки EXEC-086»);
  * присутствует в ``ABLATION_CONFIGS`` как отдельная конфигурация
    ``resource_only_v2``;
  * извлекается в enhanced-режиме через ``_extract_enhanced`` и даёт
    валидную структуру bundle;
  * при недоступном ``unpacked_dir`` возвращает null-stub с
    ``mode="v2_unavailable"``;
  * сравнивается через ``compare_m_static_layer``, возвращая dict с
    ``combined_score`` и максимум 1.0 для одинаковых features.
"""
from __future__ import annotations

import sys
import tempfile
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
    _extract_enhanced,
    _extract_resource_v2,
    compare_m_static_layer,
)


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _make_sample_apk(root: Path) -> None:
    """Создаёт «распакованный APK» для enhanced-экстракции."""
    _write(
        root / "res" / "values" / "strings.xml",
        '<resources>\n'
        '  <string name="app_name">Demo</string>\n'
        '  <string name="hello">Hi</string>\n'
        '</resources>\n',
    )
    _write(root / "res" / "drawable" / "ic_bg.xml", "<vector/>")
    _write(
        root / "res" / "mipmap-xxhdpi" / "ic_launcher.png",
        b"\x89PNG\r\n\x1a\nsample_icon_bytes",
    )
    _write(root / "res" / "layout" / "activity_main.xml", "<LinearLayout/>")
    _write(root / "assets" / "data.json", b'{"k":"v"}')


class TestAllLayersRegistration(unittest.TestCase):
    """``resource_v2`` зарегистрирован среди слоёв M_static."""

    def test_resource_v2_in_all_layers(self) -> None:
        self.assertIn("resource_v2", ALL_LAYERS)


class TestLayerWeightsDefault(unittest.TestCase):
    """Новый слой имеет вес 0.0 — сигнал «не активирован до калибровки»."""

    def test_resource_v2_default_weight_zero(self) -> None:
        self.assertIn("resource_v2", LAYER_WEIGHTS)
        self.assertEqual(LAYER_WEIGHTS["resource_v2"], 0.0)


class TestAblationConfigs(unittest.TestCase):
    """``resource_only_v2`` — отдельный пресет с ровно одним слоем."""

    def test_resource_only_v2_has_single_layer(self) -> None:
        self.assertIn("resource_only_v2", ABLATION_CONFIGS)
        self.assertEqual(
            ABLATION_CONFIGS["resource_only_v2"], ["resource_v2"],
        )
        self.assertEqual(len(ABLATION_CONFIGS["resource_only_v2"]), 1)


class TestExtractEnhancedResourceV2(unittest.TestCase):
    """``_extract_enhanced`` прокидывает resource_v2 bundle корректной формы."""

    def test_enhanced_contains_resource_v2_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = _extract_enhanced(tmpdir, None)
            self.assertIn("resource_v2", features)
            rv2 = features["resource_v2"]
            self.assertIsInstance(rv2, dict)
            for key in (
                "res_strings",
                "res_drawables",
                "res_layouts",
                "assets_bin",
                "icon_phash",
                "mode",
            ):
                self.assertIn(key, rv2)
            # Контракт на типы подмножеств.
            for key in (
                "res_strings",
                "res_drawables",
                "res_layouts",
                "assets_bin",
            ):
                self.assertIsInstance(rv2[key], set)
            # Режим реально v2, а не fallback-stub.
            self.assertEqual(rv2["mode"], "v2")
            # Ненулевые подмножества в собранном примере.
            self.assertIn("string:app_name", rv2["res_strings"])
            self.assertIn("layout:activity_main", rv2["res_layouts"])


class TestExtractResourceV2NullStub(unittest.TestCase):
    """Null-stub при отсутствии unpacked_dir и при недоступной зависимости."""

    def test_null_stub_when_unpacked_dir_is_none(self) -> None:
        bundle = _extract_resource_v2(None)
        self.assertEqual(bundle["mode"], "v2_unavailable")
        for key in (
            "res_strings",
            "res_drawables",
            "res_layouts",
            "assets_bin",
        ):
            self.assertEqual(bundle[key], set())
        self.assertIsNone(bundle["icon_phash"])

    def test_null_stub_when_extractor_missing(self) -> None:
        # Симулируем недоступную зависимость, временно сбрасывая extractor в None.
        import m_static_views as mv

        original = mv.extract_resource_view_v2
        try:
            mv.extract_resource_view_v2 = None
            with tempfile.TemporaryDirectory() as tmpdir:
                _make_sample_apk(Path(tmpdir))
                bundle = mv._extract_resource_v2(tmpdir)
            self.assertEqual(bundle["mode"], "v2_unavailable")
            self.assertEqual(bundle["res_strings"], set())
            self.assertIsNone(bundle["icon_phash"])
        finally:
            mv.extract_resource_view_v2 = original


class TestCompareMStaticLayerResourceV2(unittest.TestCase):
    """Диспетчер делегирует resource_v2 в compare_resource_view_v2."""

    def test_identical_features_give_combined_score_close_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = _extract_enhanced(tmpdir, None)
            rv2 = features["resource_v2"]
            result = compare_m_static_layer("resource_v2", rv2, rv2)
            self.assertIn("combined_score", result)
            self.assertIn("score", result)
            self.assertIsInstance(result["combined_score"], float)
            # Идентичные подмножества -> Jaccard = 1.0 для каждого подсигнала.
            self.assertAlmostEqual(result["combined_score"], 1.0, places=6)
            self.assertAlmostEqual(result["score"], 1.0, places=6)
            self.assertEqual(result.get("status"), "partial")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit-тесты для ``resource_view_v2`` (EXEC-R_resource_v2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from resource_view_v2 import (
    ICON_HASH_HEX_LEN,
    ICON_TOKEN_PREFIX,
    MODE,
    compare_resource_view_v2,
    extract_resource_view_v2,
)


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _make_sample_apk(root: Path) -> None:
    """Создаёт небольшой «распакованный APK» для тестов извлечения."""
    _write(
        root / "res" / "values" / "strings.xml",
        '<resources>\n'
        '  <string name="app_name">SimpleApplication</string>\n'
        '  <string name="welcome">Hello</string>\n'
        '</resources>\n',
    )
    _write(
        root / "res" / "values-ru" / "strings.xml",
        '<resources>\n'
        '  <string name="app_name">ПростоеПриложение</string>\n'
        '  <string name="welcome">Привет</string>\n'
        '</resources>\n',
    )
    # Drawables
    _write(root / "res" / "drawable" / "ic_launcher_background.xml", "<vector/>")
    _write(root / "res" / "drawable-v24" / "ic_launcher_foreground.xml", "<vector/>")
    _write(root / "res" / "mipmap-mdpi" / "ic_launcher.png", b"\x89PNG\r\n\x1a\nmdpi_icon")
    _write(
        root / "res" / "mipmap-xxhdpi" / "ic_launcher.png",
        b"\x89PNG\r\n\x1a\nxxhdpi_icon_bytes",
    )
    # Layouts
    _write(root / "res" / "layout" / "activity_main.xml", "<LinearLayout/>")
    _write(root / "res" / "layout" / "item_row.xml", "<TextView/>")
    _write(root / "res" / "layout-land" / "activity_main.xml", "<LinearLayout/>")
    # Assets различных размеров
    _write(root / "assets" / "small.json", b'{"k":"v"}')  # ~1KB bucket 0_1KB
    _write(root / "assets" / "medium.bin", b"A" * 5000)  # 1_10KB
    _write(root / "assets" / "nested" / "large.dat", b"B" * 50_000)  # 10_100KB


class TestExtractResourceViewV2Basic(unittest.TestCase):
    """Базовые свойства структуры результата."""

    def test_returns_all_keys_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            for key in (
                "res_strings",
                "res_drawables",
                "res_layouts",
                "assets_bin",
                "icon_phash",
                "mode",
            ):
                self.assertIn(key, features)
            self.assertEqual(features["mode"], MODE)

    def test_subsets_are_sets_of_str(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            for key in ("res_strings", "res_drawables", "res_layouts", "assets_bin"):
                self.assertIsInstance(features[key], set)
                for tok in features[key]:
                    self.assertIsInstance(tok, str)
            if features["icon_phash"] is not None:
                self.assertIsInstance(features["icon_phash"], str)


class TestExtractResourceViewV2Content(unittest.TestCase):
    """Содержательные проверки по каждому подмножеству."""

    def test_strings_from_values_and_localizations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            # Имена стабильны между локалями — дедуплицируются.
            self.assertIn("string:app_name", features["res_strings"])
            self.assertIn("string:welcome", features["res_strings"])
            self.assertEqual(len(features["res_strings"]), 2)

    def test_drawables_have_no_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            self.assertIn("drawable:ic_launcher_background", features["res_drawables"])
            self.assertIn("drawable:ic_launcher_foreground", features["res_drawables"])
            self.assertIn("drawable:ic_launcher", features["res_drawables"])
            for tok in features["res_drawables"]:
                # Никаких .png/.xml в токенах быть не должно.
                self.assertFalse(tok.endswith(".png"))
                self.assertFalse(tok.endswith(".xml"))

    def test_layouts_contain_main_and_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            self.assertIn("layout:activity_main", features["res_layouts"])
            self.assertIn("layout:item_row", features["res_layouts"])
            # activity_main присутствует и в layout/, и в layout-land/ —
            # дедупликация по имени.
            self.assertEqual(len(features["res_layouts"]), 2)

    def test_assets_bin_format_and_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            # Все токены должны начинаться с "asset:" и содержать валидный bucket.
            valid_buckets = {"0_1KB", "1_10KB", "10_100KB", "100KB_1MB", "1MB+"}
            for tok in features["assets_bin"]:
                self.assertTrue(tok.startswith("asset:"), tok)
                parts = tok.split(":")
                self.assertEqual(len(parts), 3, tok)
                self.assertIn(parts[2], valid_buckets, tok)
            # Проверяем конкретные ожидания.
            self.assertIn("asset:assets/small.json:0_1KB", features["assets_bin"])
            self.assertIn("asset:assets/medium.bin:1_10KB", features["assets_bin"])
            self.assertIn(
                "asset:assets/nested/large.dat:10_100KB", features["assets_bin"]
            )

    def test_icon_phash_format_when_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            token = features["icon_phash"]
            self.assertIsNotNone(token)
            prefix = "{}:".format(ICON_TOKEN_PREFIX)
            self.assertTrue(token.startswith(prefix), token)
            hex_part = token[len(prefix):]
            self.assertEqual(len(hex_part), ICON_HASH_HEX_LEN)
            # Должно парситься как hex.
            int(hex_part, 16)

    def test_icon_phash_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Только строки — без иконок.
            _write(
                Path(tmpdir) / "res" / "values" / "strings.xml",
                '<resources><string name="x">y</string></resources>',
            )
            features = extract_resource_view_v2(tmpdir)
            self.assertIsNone(features["icon_phash"])

    def test_empty_dir_gives_empty_subsets_and_no_icon(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            features = extract_resource_view_v2(tmpdir)
            self.assertEqual(features["res_strings"], set())
            self.assertEqual(features["res_drawables"], set())
            self.assertEqual(features["res_layouts"], set())
            self.assertEqual(features["assets_bin"], set())
            self.assertIsNone(features["icon_phash"])


class TestExtractResourceViewV2Errors(unittest.TestCase):
    def test_nonexistent_dir_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            extract_resource_view_v2("/nonexistent/phd/resource_v2/path")

    def test_file_instead_of_dir_raises(self) -> None:
        with tempfile.NamedTemporaryFile() as tmp:
            with self.assertRaises(NotADirectoryError):
                extract_resource_view_v2(tmp.name)


class TestCompareResourceViewV2(unittest.TestCase):
    """Сравнение признаков."""

    def _features(
        self,
        strings=None,
        drawables=None,
        layouts=None,
        assets=None,
        icon=None,
    ) -> dict:
        return {
            "res_strings": set(strings or []),
            "res_drawables": set(drawables or []),
            "res_layouts": set(layouts or []),
            "assets_bin": set(assets or []),
            "icon_phash": icon,
            "mode": MODE,
        }

    def test_identical_features_all_scores_one(self) -> None:
        f = self._features(
            strings={"string:a", "string:b"},
            drawables={"drawable:x"},
            layouts={"layout:main"},
            assets={"asset:assets/d.json:0_1KB"},
            icon="{}:{}".format(ICON_TOKEN_PREFIX, "a" * ICON_HASH_HEX_LEN),
        )
        result = compare_resource_view_v2(f, f)
        self.assertEqual(result["res_strings_score"], 1.0)
        self.assertEqual(result["res_drawables_score"], 1.0)
        self.assertEqual(result["res_layouts_score"], 1.0)
        self.assertEqual(result["assets_bin_score"], 1.0)
        self.assertEqual(result["icon_phash_similarity"], 1.0)
        self.assertEqual(result["combined_score"], 1.0)
        self.assertEqual(result["status"], "ok")

    def test_all_empty_status_empty(self) -> None:
        f = self._features()
        result = compare_resource_view_v2(f, f)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["combined_score"], 0.0)

    def test_partial_status_when_some_subsets_empty(self) -> None:
        # Только строки есть, остальное пусто — status=partial.
        f_a = self._features(strings={"string:a", "string:b"})
        f_b = self._features(strings={"string:a"})
        result = compare_resource_view_v2(f_a, f_b)
        self.assertEqual(result["status"], "partial")
        # Jaccard = 1/2
        self.assertAlmostEqual(result["res_strings_score"], 0.5)
        # combined_score считается только по непустому подмножеству.
        self.assertAlmostEqual(result["combined_score"], 0.5)

    def test_disjoint_features_produce_zero_scores(self) -> None:
        f_a = self._features(
            strings={"string:a"},
            drawables={"drawable:x"},
            layouts={"layout:main"},
            assets={"asset:assets/d.json:0_1KB"},
        )
        f_b = self._features(
            strings={"string:b"},
            drawables={"drawable:y"},
            layouts={"layout:other"},
            assets={"asset:assets/e.json:0_1KB"},
        )
        result = compare_resource_view_v2(f_a, f_b)
        self.assertEqual(result["res_strings_score"], 0.0)
        self.assertEqual(result["res_drawables_score"], 0.0)
        self.assertEqual(result["res_layouts_score"], 0.0)
        self.assertEqual(result["assets_bin_score"], 0.0)
        # Без иконки — status=partial (есть 4 непустых подмножества из 5 сигналов).
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["combined_score"], 0.0)

    def test_icon_similarity_hamming(self) -> None:
        # Два хеша, отличающиеся ровно в одном бите.
        hex_a = "0" * ICON_HASH_HEX_LEN
        # 0x1 = один установленный бит
        hex_b = "0" * (ICON_HASH_HEX_LEN - 1) + "1"
        f_a = self._features(icon="{}:{}".format(ICON_TOKEN_PREFIX, hex_a))
        f_b = self._features(icon="{}:{}".format(ICON_TOKEN_PREFIX, hex_b))
        result = compare_resource_view_v2(f_a, f_b)
        # 1 бит из 64 различается — сходство = 1 - 1/64 ≈ 0.984375.
        self.assertAlmostEqual(result["icon_phash_similarity"], 1.0 - 1.0 / 64.0)

    def test_icon_missing_on_one_side_gives_zero_and_not_counted(self) -> None:
        f_a = self._features(
            strings={"string:a"},
            icon="{}:{}".format(ICON_TOKEN_PREFIX, "a" * ICON_HASH_HEX_LEN),
        )
        f_b = self._features(strings={"string:a"})  # без иконки
        result = compare_resource_view_v2(f_a, f_b)
        # Иконка учитывается в combined_score только если обе стороны имеют токен.
        self.assertEqual(result["icon_phash_similarity"], 0.0)
        self.assertEqual(result["res_strings_score"], 1.0)
        self.assertAlmostEqual(result["combined_score"], 1.0)

    def test_ok_status_when_all_five_signals_present(self) -> None:
        f_a = self._features(
            strings={"string:a"},
            drawables={"drawable:x"},
            layouts={"layout:main"},
            assets={"asset:assets/d.json:0_1KB"},
            icon="{}:{}".format(ICON_TOKEN_PREFIX, "a" * ICON_HASH_HEX_LEN),
        )
        f_b = self._features(
            strings={"string:a"},
            drawables={"drawable:x"},
            layouts={"layout:main"},
            assets={"asset:assets/d.json:0_1KB"},
            icon="{}:{}".format(ICON_TOKEN_PREFIX, "a" * ICON_HASH_HEX_LEN),
        )
        result = compare_resource_view_v2(f_a, f_b)
        self.assertEqual(result["status"], "ok")


class TestEndToEndExtractAndCompare(unittest.TestCase):
    """Полный цикл: извлечение → сравнение на синтетических APK."""

    def test_identical_apks_all_signals_match(self) -> None:
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            _make_sample_apk(Path(dir_a))
            _make_sample_apk(Path(dir_b))
            fa = extract_resource_view_v2(dir_a)
            fb = extract_resource_view_v2(dir_b)
            result = compare_resource_view_v2(fa, fb)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["res_strings_score"], 1.0)
            self.assertEqual(result["res_drawables_score"], 1.0)
            self.assertEqual(result["res_layouts_score"], 1.0)
            self.assertEqual(result["assets_bin_score"], 1.0)
            self.assertEqual(result["icon_phash_similarity"], 1.0)
            self.assertEqual(result["combined_score"], 1.0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit-тесты для ``resource_view_v2`` (EXEC-R_resource_v2)."""

from __future__ import annotations

import io
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

try:
    from PIL import Image  # type: ignore
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    _PILLOW_AVAILABLE = False

_PILLOW_REQUIRED_MSG = (
    "Pillow не установлен; перцептивный хеш и тесты иконки пропущены"
)


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _make_gradient_png(
    width: int = 48,
    height: int = 48,
    brightness: float = 1.0,
    invert: bool = False,
) -> bytes:
    """Создаёт простой PNG с диагональным градиентом.

    Используется в тестах dHash: градиент даёт предсказуемый перцептивный
    хеш, устойчивый к ресайзу. ``brightness`` умножает яркость, ``invert``
    меняет направление градиента (для генерации совершенно разных иконок).
    """
    if not _PILLOW_AVAILABLE:
        raise RuntimeError("Pillow не установлен — _make_gradient_png недоступен")
    img = Image.new("L", (width, height))
    pixels = []
    for y in range(height):
        for x in range(width):
            if invert:
                raw = (width - 1 - x + y) / (width + height - 2)
            else:
                raw = (x + y) / (width + height - 2)
            value = int(min(255, max(0, raw * 255 * brightness)))
            pixels.append(value)
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
    # Иконки: если Pillow доступен — реальные PNG-градиенты; иначе
    # фиктивные байты (тогда icon_phash честно вернёт None).
    if _PILLOW_AVAILABLE:
        icon_bytes_mdpi = _make_gradient_png(width=48, height=48)
        icon_bytes_xxhdpi = _make_gradient_png(width=96, height=96)
    else:
        icon_bytes_mdpi = b"\x89PNG\r\n\x1a\nmdpi_icon"
        icon_bytes_xxhdpi = b"\x89PNG\r\n\x1a\nxxhdpi_icon_bytes"
    _write(root / "res" / "mipmap-mdpi" / "ic_launcher.png", icon_bytes_mdpi)
    _write(root / "res" / "mipmap-xxhdpi" / "ic_launcher.png", icon_bytes_xxhdpi)
    # Layouts
    _write(root / "res" / "layout" / "activity_main.xml", "<LinearLayout/>")
    _write(root / "res" / "layout" / "item_row.xml", "<TextView/>")
    _write(root / "res" / "layout-land" / "activity_main.xml", "<LinearLayout/>")
    # Assets различных размеров
    _write(root / "assets" / "small.json", b'{"k":"v"}')  # ~1KB bucket 0_1KB
    _write(root / "assets" / "medium.bin", b"A" * 5000)  # 1_10KB
    _write(root / "assets" / "nested" / "large.dat", b"B" * 50_000)  # 10_100KB


def _hamming_hex(hex_a: str, hex_b: str) -> int:
    """Хеммингово расстояние между двумя hex-строками одинаковой длины."""
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def _phash_hex(token: str) -> str:
    """Извлекает hex-часть из токена иконки.

    Поддерживает оба формата:
    * post-REPR-16: ``icon_phash:<method>:<hex>``;
    * legacy: ``icon_phash:<hex>``.
    """
    prefix = "{}:".format(ICON_TOKEN_PREFIX)
    assert token.startswith(prefix), token
    tail = token[len(prefix):]
    # Новый формат содержит ещё одно двоеточие после метода.
    if ":" in tail:
        _method, _, hex_part = tail.partition(":")
        return hex_part
    return tail


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

    @unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED_MSG)
    def test_icon_phash_format_when_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_sample_apk(Path(tmpdir))
            features = extract_resource_view_v2(tmpdir)
            token = features["icon_phash"]
            self.assertIsNotNone(token)
            prefix = "{}:".format(ICON_TOKEN_PREFIX)
            self.assertTrue(token.startswith(prefix), token)
            # Post-REPR-16: формат ``icon_phash:<method>:<hex>``.
            hex_part = _phash_hex(token)
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

    def test_icon_phash_none_when_icon_bytes_invalid(self) -> None:
        """Честный fallback: мусорные байты в PNG → ``icon_phash = None``.

        Без маскировки через blake2b. Этот тест важен и тогда, когда Pillow
        установлен — проверяет честность провала декодера.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "res" / "values" / "strings.xml",
                   '<resources><string name="x">y</string></resources>')
            _write(
                root / "res" / "mipmap-mdpi" / "ic_launcher.png",
                b"not a real png at all",
            )
            features = extract_resource_view_v2(tmpdir)
            if _PILLOW_AVAILABLE:
                # Pillow не сможет декодировать мусор — честный None.
                self.assertIsNone(features["icon_phash"])
            else:
                # Без Pillow вообще нет перцептивного хеша.
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
        # DEEP-20-BOTH-EMPTY-AUDIT: канонический статус для «обе
        # стороны без ресурсов и без иконки» — 'both_empty' + флаг
        # both_empty=True (ранее — 'empty' без флага). Единая семантика
        # со всеми слоями static view; downstream может исключить
        # resource_v2 из взвешенного среднего по признаку both_empty.
        f = self._features()
        result = compare_resource_view_v2(f, f)
        self.assertEqual(result["status"], "both_empty")
        self.assertIs(result.get("both_empty"), True)
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


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED_MSG)
class TestDHashPerceptualProperties(unittest.TestCase):
    """Проверка перцептивных свойств dHash на реальных PNG-иконках.

    Требует Pillow — без него dHash не вычисляется (честный None).
    Каждый тест сравнивает хеш эталонной иконки с его варьируемой
    версией по Хеммингу. Границы выбраны с запасом над шумом dHash.
    """

    def _extract_hex(self, root: Path) -> str:
        """Создаёт APK с заданной уже иконкой и возвращает hex-часть хеша."""
        features = extract_resource_view_v2(str(root))
        token = features["icon_phash"]
        self.assertIsNotNone(token, "Ожидался непустой icon_phash")
        return _phash_hex(token)

    def _apk_with_icon(self, root: Path, png_bytes: bytes) -> None:
        # Минимальная структура APK с одной иконкой.
        _write(
            root / "res" / "values" / "strings.xml",
            '<resources><string name="x">y</string></resources>',
        )
        _write(root / "res" / "mipmap-mdpi" / "ic_launcher.png", png_bytes)

    def test_identical_icons_hamming_zero(self) -> None:
        png = _make_gradient_png(width=48, height=48)
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            self._apk_with_icon(Path(dir_a), png)
            self._apk_with_icon(Path(dir_b), png)
            hex_a = self._extract_hex(Path(dir_a))
            hex_b = self._extract_hex(Path(dir_b))
            hamming = _hamming_hex(hex_a, hex_b)
            self.assertEqual(
                hamming, 0,
                "Идентичные иконки: ожидается Hamming = 0, получено {}".format(hamming),
            )

    def test_resized_icon_small_hamming(self) -> None:
        # Та же иконка, ресайз +20% по обеим сторонам.
        base_png = _make_gradient_png(width=48, height=48)
        resized_png = _make_gradient_png(width=58, height=58)  # ~ +20%
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            self._apk_with_icon(Path(dir_a), base_png)
            self._apk_with_icon(Path(dir_b), resized_png)
            hex_a = self._extract_hex(Path(dir_a))
            hex_b = self._extract_hex(Path(dir_b))
            hamming = _hamming_hex(hex_a, hex_b)
            self.assertLessEqual(
                hamming, 10,
                "Ресайз +20% не должен радикально менять dHash: Hamming={}".format(hamming),
            )

    def test_brightness_shift_small_hamming(self) -> None:
        # Та же иконка, +10% яркости.
        base_png = _make_gradient_png(width=48, height=48, brightness=1.0)
        bright_png = _make_gradient_png(width=48, height=48, brightness=1.1)
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            self._apk_with_icon(Path(dir_a), base_png)
            self._apk_with_icon(Path(dir_b), bright_png)
            hex_a = self._extract_hex(Path(dir_a))
            hex_b = self._extract_hex(Path(dir_b))
            hamming = _hamming_hex(hex_a, hex_b)
            self.assertLessEqual(
                hamming, 10,
                "Лёгкое изменение яркости не должно радикально менять dHash: "
                "Hamming={}".format(hamming),
            )

    def test_different_icons_large_hamming(self) -> None:
        # Совершенно разные иконки — прямой и инвертированный градиент.
        base_png = _make_gradient_png(width=48, height=48, invert=False)
        other_png = _make_gradient_png(width=48, height=48, invert=True)
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            self._apk_with_icon(Path(dir_a), base_png)
            self._apk_with_icon(Path(dir_b), other_png)
            hex_a = self._extract_hex(Path(dir_a))
            hex_b = self._extract_hex(Path(dir_b))
            hamming = _hamming_hex(hex_a, hex_b)
            self.assertGreaterEqual(
                hamming, 20,
                "Совершенно разные иконки должны давать большой dHash-разрыв: "
                "Hamming={}".format(hamming),
            )


class TestEndToEndExtractAndCompare(unittest.TestCase):
    """Полный цикл: извлечение → сравнение на синтетических APK."""

    def test_identical_apks_all_signals_match(self) -> None:
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            _make_sample_apk(Path(dir_a))
            _make_sample_apk(Path(dir_b))
            fa = extract_resource_view_v2(dir_a)
            fb = extract_resource_view_v2(dir_b)
            result = compare_resource_view_v2(fa, fb)
            # Базовые подмножества совпадают всегда.
            self.assertEqual(result["res_strings_score"], 1.0)
            self.assertEqual(result["res_drawables_score"], 1.0)
            self.assertEqual(result["res_layouts_score"], 1.0)
            self.assertEqual(result["assets_bin_score"], 1.0)
            if _PILLOW_AVAILABLE:
                # С Pillow обе APK получают валидный dHash одной и той же иконки.
                self.assertEqual(result["icon_phash_similarity"], 1.0)
                self.assertEqual(result["combined_score"], 1.0)
                self.assertEqual(result["status"], "ok")
            else:
                # Без Pillow icon_phash = None с обеих сторон, сигнал иконки
                # не учитывается — status=partial, combined_score по 4 ключам.
                self.assertEqual(result["icon_phash_similarity"], 0.0)
                self.assertEqual(result["combined_score"], 1.0)
                self.assertEqual(result["status"], "partial")


if __name__ == "__main__":
    unittest.main()

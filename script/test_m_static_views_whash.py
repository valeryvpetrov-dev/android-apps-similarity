#!/usr/bin/env python3
"""REPR-16-WHASH-HAAR: тесты перехода с dHash на wHash в resource-слое.

Проверяется:

1. ``test_whash_hashes_identical_for_same_image`` — один и тот же PNG
   даёт один и тот же wHash (детерминизм и устойчивость).
2. ``test_whash_differs_for_perceptually_different_images`` — два
   перцептивно разных PNG дают разные wHash (реальный сигнал, а не
   константа).
3. ``test_whash_token_prefix_is_whash`` — в токене иконки присутствует
   явный префикс метода (``icon_phash:whash:<hex>``), а не legacy-формат
   без метода и не dHash-префикс.
4. ``test_dhash_fallback_with_env_var`` — через переменную окружения
   ``ANDROID_SIM_IMAGE_HASH_METHOD=dhash`` метод откатывается на dHash,
   токен получает префикс ``icon_phash:dhash:<hex>``.

Все тесты требуют Pillow. Тесты wHash дополнительно требуют ``imagehash``
— при отсутствии библиотеки они честно пропускаются, чтобы
соответствовать политике резервного пути (fallback на dHash).
"""
from __future__ import annotations

import io
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from PIL import Image  # type: ignore
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    _PILLOW_AVAILABLE = False

try:
    import imagehash  # noqa: F401
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    _IMAGEHASH_AVAILABLE = False


_PILLOW_REQUIRED = "Pillow не установлен; тесты wHash/dHash пропущены"
_IMAGEHASH_REQUIRED = "imagehash не установлен; тесты wHash пропущены"


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _make_gradient_png(
    width: int = 48,
    height: int = 48,
    invert: bool = False,
) -> bytes:
    """PNG с диагональным градиентом — предсказуемый перцептивный сигнал."""
    if not _PILLOW_AVAILABLE:
        raise RuntimeError("Pillow не установлен")
    img = Image.new("L", (width, height))
    pixels = []
    for y in range(height):
        for x in range(width):
            if invert:
                raw = (width - 1 - x + y) / (width + height - 2)
            else:
                raw = (x + y) / (width + height - 2)
            value = int(min(255, max(0, raw * 255)))
            pixels.append(value)
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_apk_with_icon(root: Path, png_bytes: bytes) -> None:
    """Минимальная структура распакованного APK с одной иконкой mipmap-mdpi."""
    _write(
        root / "res" / "values" / "strings.xml",
        '<resources><string name="x">y</string></resources>',
    )
    _write(root / "res" / "mipmap-mdpi" / "ic_launcher.png", png_bytes)


def _reload_resource_view_v2():
    """Перезагружает resource_view_v2, чтобы заново перечитать env var.

    Активный метод хеша кэшируется на module level в ``ICON_HASH_METHOD``
    при первом импорте. Тесты, которые меняют
    ``ANDROID_SIM_IMAGE_HASH_METHOD``, обязаны делать reload, иначе
    изменение не подхватится.
    """
    import resource_view_v2  # noqa: WPS433 - импорт внутри функции по дизайну
    return importlib.reload(resource_view_v2)


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
@unittest.skipIf(not _IMAGEHASH_AVAILABLE, _IMAGEHASH_REQUIRED)
class TestWHashBasics(unittest.TestCase):
    """Базовые свойства wHash: детерминизм и чувствительность к картинке."""

    def setUp(self) -> None:
        # Явно выставляем whash, чтобы тест не зависел от окружения.
        self._prev_env = os.environ.get("ANDROID_SIM_IMAGE_HASH_METHOD")
        os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = "whash"
        self.module = _reload_resource_view_v2()

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("ANDROID_SIM_IMAGE_HASH_METHOD", None)
        else:
            os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = self._prev_env
        _reload_resource_view_v2()

    def test_whash_hashes_identical_for_same_image(self) -> None:
        """Одинаковые PNG должны давать один и тот же wHash."""
        png = _make_gradient_png(width=48, height=48)
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            _make_apk_with_icon(Path(dir_a), png)
            _make_apk_with_icon(Path(dir_b), png)
            features_a = self.module.extract_resource_view_v2(dir_a)
            features_b = self.module.extract_resource_view_v2(dir_b)
            self.assertIsNotNone(features_a["icon_phash"])
            self.assertEqual(features_a["icon_phash"], features_b["icon_phash"])

    def test_whash_differs_for_perceptually_different_images(self) -> None:
        """Два перцептивно разных PNG должны давать разные wHash.

        Это критическая защита от «пустого» сигнала (постоянный хеш
        для любой картинки).
        """
        png_a = _make_gradient_png(width=48, height=48, invert=False)
        png_b = _make_gradient_png(width=48, height=48, invert=True)
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            _make_apk_with_icon(Path(dir_a), png_a)
            _make_apk_with_icon(Path(dir_b), png_b)
            features_a = self.module.extract_resource_view_v2(dir_a)
            features_b = self.module.extract_resource_view_v2(dir_b)
            self.assertIsNotNone(features_a["icon_phash"])
            self.assertIsNotNone(features_b["icon_phash"])
            self.assertNotEqual(features_a["icon_phash"], features_b["icon_phash"])


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
@unittest.skipIf(not _IMAGEHASH_AVAILABLE, _IMAGEHASH_REQUIRED)
class TestWHashTokenFormat(unittest.TestCase):
    """Формат токена: префикс метода обязателен."""

    def setUp(self) -> None:
        self._prev_env = os.environ.get("ANDROID_SIM_IMAGE_HASH_METHOD")
        os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = "whash"
        self.module = _reload_resource_view_v2()

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("ANDROID_SIM_IMAGE_HASH_METHOD", None)
        else:
            os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = self._prev_env
        _reload_resource_view_v2()

    def test_whash_token_prefix_is_whash(self) -> None:
        """Токен должен быть в формате ``icon_phash:whash:<16 hex>``.

        Причина: на смешанных корпусах (pre- и post-migration) Jaccard
        по разным методам даст нули. Префикс метода делает это явным
        и позволяет ``compare_resource_view_v2`` корректно отсеивать
        токены разных методов.
        """
        png = _make_gradient_png(width=48, height=48)
        with tempfile.TemporaryDirectory() as apk_dir:
            _make_apk_with_icon(Path(apk_dir), png)
            features = self.module.extract_resource_view_v2(apk_dir)
            token = features["icon_phash"]
            self.assertIsNotNone(token)
            # Формат: icon_phash:whash:<16 hex>
            parts = token.split(":")
            self.assertEqual(len(parts), 3, "Ожидался токен из 3 частей: {}".format(token))
            self.assertEqual(parts[0], "icon_phash")
            self.assertEqual(parts[1], "whash")
            self.assertEqual(len(parts[2]), self.module.ICON_HASH_HEX_LEN)
            # Должно парситься как hex.
            int(parts[2], 16)


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
class TestDHashFallbackViaEnvVar(unittest.TestCase):
    """Env var ``ANDROID_SIM_IMAGE_HASH_METHOD=dhash`` включает legacy dHash."""

    def setUp(self) -> None:
        self._prev_env = os.environ.get("ANDROID_SIM_IMAGE_HASH_METHOD")
        os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = "dhash"
        self.module = _reload_resource_view_v2()

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("ANDROID_SIM_IMAGE_HASH_METHOD", None)
        else:
            os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = self._prev_env
        _reload_resource_view_v2()

    def test_dhash_fallback_with_env_var(self) -> None:
        """Через env var можно явно вернуться на dHash.

        Токен должен иметь префикс ``icon_phash:dhash:<16 hex>`` и не
        зависеть от наличия ``imagehash`` — dHash реализован на чистом
        Pillow.
        """
        self.assertEqual(self.module.ICON_HASH_METHOD, "dhash")
        png = _make_gradient_png(width=48, height=48)
        with tempfile.TemporaryDirectory() as apk_dir:
            _make_apk_with_icon(Path(apk_dir), png)
            features = self.module.extract_resource_view_v2(apk_dir)
            token = features["icon_phash"]
            self.assertIsNotNone(token)
            parts = token.split(":")
            self.assertEqual(len(parts), 3, "Ожидался токен из 3 частей: {}".format(token))
            self.assertEqual(parts[0], "icon_phash")
            self.assertEqual(parts[1], "dhash")
            self.assertEqual(len(parts[2]), self.module.ICON_HASH_HEX_LEN)
            int(parts[2], 16)


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
class TestLegacyTokenCompatibility(unittest.TestCase):
    """Backward-compat: старые токены ``icon_phash:<hex>`` без метода."""

    def setUp(self) -> None:
        self._prev_env = os.environ.get("ANDROID_SIM_IMAGE_HASH_METHOD")
        # Включаем dhash, чтобы сгенерировать токен с новым префиксом и
        # затем вручную собрать legacy-эквивалент.
        os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = "dhash"
        self.module = _reload_resource_view_v2()

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("ANDROID_SIM_IMAGE_HASH_METHOD", None)
        else:
            os.environ["ANDROID_SIM_IMAGE_HASH_METHOD"] = self._prev_env
        _reload_resource_view_v2()

    def test_legacy_token_without_method_is_read_as_dhash(self) -> None:
        """Legacy-токен ``icon_phash:<hex>`` должен сравниваться с новым
        ``icon_phash:dhash:<hex>`` как равный (один и тот же hex → Hamming=0).
        """
        hex_part = "a" * self.module.ICON_HASH_HEX_LEN
        legacy = "{}:{}".format(self.module.ICON_TOKEN_PREFIX, hex_part)
        modern = "{}:dhash:{}".format(self.module.ICON_TOKEN_PREFIX, hex_part)
        sim = self.module._icon_similarity(legacy, modern)
        self.assertEqual(sim, 1.0)

    def test_mixed_methods_return_none_similarity(self) -> None:
        """Токены разных методов (whash vs dhash) не сравниваются — ``None``.

        Это защищает от ложного сходства на смешанных корпусах.
        """
        hex_part = "a" * self.module.ICON_HASH_HEX_LEN
        token_whash = "{}:whash:{}".format(self.module.ICON_TOKEN_PREFIX, hex_part)
        token_dhash = "{}:dhash:{}".format(self.module.ICON_TOKEN_PREFIX, hex_part)
        sim = self.module._icon_similarity(token_whash, token_dhash)
        self.assertIsNone(sim)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for EXEC-R_metadata_v2: extended cheap metadata tokens.

Проверяет что ``screening_runner.extract_layers_from_apk`` добавляет
в слой ``metadata`` новые дешёвые токены: версию DEX, признак наличия
подписи, схему подписи, префикс отпечатка сертификата, токены
разрешений и возможностей. Обратная совместимость со старым набором
токенов сохраняется.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from screening_runner import extract_layers_from_apk  # noqa: E402


SIMPLE_APK = (
    Path(__file__).resolve().parents[1]
    / "apk"
    / "simple_app"
    / "simple_app-releaseNonOptimized.apk"
)


HEX_CHARS = set("0123456789abcdef")


class TestMetadataV2OnSimpleApk(unittest.TestCase):
    """Проверки на реальном тестовом APK (с подписью и classes.dex)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not SIMPLE_APK.exists():
            raise unittest.SkipTest("simple_app APK не найден: {}".format(SIMPLE_APK))
        cls.layers = extract_layers_from_apk(SIMPLE_APK)
        cls.metadata = cls.layers["metadata"]

    def test_dex_version_token_has_three_digits(self) -> None:
        """Токен dex_version:NNN присутствует, NNN — ровно три цифры."""
        dex_tokens = [t for t in self.metadata if t.startswith("dex_version:")]
        self.assertEqual(len(dex_tokens), 1, "ожидался один dex_version токен")
        token = dex_tokens[0]
        self.assertRegex(token, r"^dex_version:\d{3}$")

    def test_signing_present_is_one(self) -> None:
        """У подписанного simple_app должен быть токен signing_present:1."""
        self.assertIn("signing_present:1", self.metadata)
        self.assertNotIn("signing_present:0", self.metadata)

    def test_signing_scheme_v1_detected(self) -> None:
        """У simple_app классическая META-INF подпись (.RSA) — схема v1."""
        self.assertIn("signing_scheme:v1", self.metadata)

    def test_signing_prefix_is_eight_hex_chars(self) -> None:
        """signing_prefix:XXXXXXXX — ровно 8 hex-символов в нижнем регистре."""
        prefix_tokens = [t for t in self.metadata if t.startswith("signing_prefix:")]
        self.assertEqual(len(prefix_tokens), 1, "ожидался один signing_prefix")
        prefix = prefix_tokens[0].split(":", 1)[1]
        self.assertEqual(len(prefix), 8, "префикс должен быть ровно 8 символов")
        self.assertTrue(
            all(ch in HEX_CHARS for ch in prefix),
            "префикс должен состоять из hex-символов нижнего регистра: {!r}".format(prefix),
        )

    def test_permission_and_feature_token_counts_non_negative(self) -> None:
        """Количество uses_permission:* и uses_feature:* — неотрицательное целое."""
        perm_count = sum(1 for t in self.metadata if t.startswith("uses_permission:"))
        feat_count = sum(1 for t in self.metadata if t.startswith("uses_feature:"))
        self.assertGreaterEqual(perm_count, 0)
        self.assertGreaterEqual(feat_count, 0)

    def test_extract_is_deterministic(self) -> None:
        """Повторный вызов даёт идентичное множество токенов метаданных."""
        layers_b = extract_layers_from_apk(SIMPLE_APK)
        self.assertEqual(self.metadata, layers_b["metadata"])

    def test_backward_compatible_legacy_tokens_present(self) -> None:
        """Существующие токены сохранены: apk_name, entry_bin, dex_count_bin,
        manifest_present, resources_arsc_present."""
        legacy_prefixes = (
            "apk_name:",
            "entry_bin:",
            "dex_count_bin:",
            "manifest_present:",
            "resources_arsc_present:",
        )
        for prefix in legacy_prefixes:
            matches = [t for t in self.metadata if t.startswith(prefix)]
            self.assertTrue(
                matches,
                "missing legacy token with prefix {!r}".format(prefix),
            )


class TestMetadataV2OnSyntheticApk(unittest.TestCase):
    """Проверки на синтетических APK, собранных стандартным zipfile."""

    def _write_apk(
        self,
        tmpdir: Path,
        name: str,
        *,
        manifest_bytes: bytes | None = None,
        dex_bytes: bytes | None = None,
        include_meta_inf_rsa: bool = False,
    ) -> Path:
        apk_path = tmpdir / name
        with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
            if manifest_bytes is not None:
                archive.writestr("AndroidManifest.xml", manifest_bytes)
            if dex_bytes is not None:
                archive.writestr("classes.dex", dex_bytes)
            if include_meta_inf_rsa:
                archive.writestr("META-INF/CERT.RSA", b"\x00\x01\x02\x03cert")
                archive.writestr("META-INF/CERT.SF", b"sig")
                archive.writestr("META-INF/MANIFEST.MF", b"mf")
        return apk_path

    def test_unsigned_apk_has_no_signing_scheme_or_prefix(self) -> None:
        """APK без META-INF и без APK Sig Block: signing_present:0, нет scheme/prefix."""
        manifest = b"<manifest package=\"com.example\"/>"
        dex_bytes = b"dex\n035\x00" + b"\x00" * 16
        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            apk_path = self._write_apk(
                tmpdir,
                "unsigned.apk",
                manifest_bytes=manifest,
                dex_bytes=dex_bytes,
                include_meta_inf_rsa=False,
            )
            layers = extract_layers_from_apk(apk_path)
        metadata = layers["metadata"]

        self.assertIn("signing_present:0", metadata)
        self.assertFalse(
            any(t.startswith("signing_scheme:") for t in metadata),
            "signing_scheme:* не должен появляться у неподписанного APK",
        )
        self.assertFalse(
            any(t.startswith("signing_prefix:") for t in metadata),
            "signing_prefix:* не должен появляться у неподписанного APK",
        )

    def test_permission_and_feature_tokens_extracted(self) -> None:
        """Из UTF-16LE манифеста извлекаются uses_permission и uses_feature."""
        # имитируем бинарный AXML: строки кодируем в UTF-16LE (как в StringPool)
        manifest_txt = (
            "<manifest package=\"com.example.perms\">"
            "<uses-permission android:name=\"android.permission.INTERNET\"/>"
            "<uses-permission android:name=\"android.permission.CAMERA\"/>"
            "<uses-feature android:name=\"android.hardware.camera\"/>"
            "<uses-feature android:name=\"android.software.leanback\"/>"
            "</manifest>"
        )
        manifest_bytes = manifest_txt.encode("utf-16le")
        dex_bytes = b"dex\n038\x00" + b"\x00" * 16

        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            apk_path = self._write_apk(
                tmpdir,
                "perms.apk",
                manifest_bytes=manifest_bytes,
                dex_bytes=dex_bytes,
                include_meta_inf_rsa=True,
            )
            layers = extract_layers_from_apk(apk_path)
        metadata = layers["metadata"]

        self.assertIn("uses_permission:INTERNET", metadata)
        self.assertIn("uses_permission:CAMERA", metadata)
        self.assertIn("uses_feature:camera", metadata)
        self.assertIn("uses_feature:leanback", metadata)


if __name__ == "__main__":
    unittest.main()

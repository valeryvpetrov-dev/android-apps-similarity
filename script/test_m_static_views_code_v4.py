#!/usr/bin/env python3
"""EXEC-082a-INTEGRATION: code_view_v4 bundle in extract_all_features.

Verifies that extract_all_features now returns a `code_v4` key in
both quick and enhanced modes, that existing layer keys are
preserved, and that the signal is deterministic on the same APK.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from m_static_views import extract_all_features

APK_DIR = _PROJECT_ROOT / "apk"
APK_NON_OPTIMIZED = APK_DIR / "simple_app" / "simple_app-releaseNonOptimized.apk"


class TestExtractAllFeaturesCodeV4(unittest.TestCase):
    """Ключ `code_v4` появляется в результате extract_all_features."""

    def test_quick_mode_includes_code_v4_key(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest("Тестовый APK не найден")
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        self.assertIn("code_v4", features)

    def test_quick_mode_code_v4_shape_and_nonzero(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest("Тестовый APK не найден")
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        bundle = features["code_v4"]
        self.assertIsInstance(bundle, dict)
        for key in ("method_fingerprints", "total_methods", "mode"):
            self.assertIn(key, bundle, f"Ключ {key!r} отсутствует в code_v4")
        self.assertGreaterEqual(bundle["total_methods"], 1)
        self.assertIsInstance(bundle["method_fingerprints"], dict)
        self.assertTrue(bundle["method_fingerprints"])

    def test_enhanced_mode_without_apk_path_returns_unavailable_stub(self):
        """Без apk_path code_view_v4 извлечь нельзя — честный stub."""
        unpacked = APK_DIR / "simple_app"
        if not unpacked.is_dir():
            self.skipTest("Тестовая распакованная директория не найдена")
        features = extract_all_features(unpacked_dir=str(unpacked))
        self.assertIn("code_v4", features)
        self.assertEqual(features["code_v4"]["mode"], "v4_unavailable")
        self.assertEqual(features["code_v4"]["method_fingerprints"], {})
        self.assertEqual(features["code_v4"]["total_methods"], 0)

    def test_other_layer_keys_preserved(self):
        """EXEC-082a-INTEGRATION не должен ломать существующий контракт ключей."""
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest("Тестовый APK не найден")
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        for key in ("code", "component", "resource", "metadata", "library", "signing", "mode"):
            self.assertIn(key, features, f"Ключ {key!r} потерян")

    def test_quick_mode_code_v4_deterministic(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest("Тестовый APK не найден")
        f1 = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))["code_v4"]
        f2 = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))["code_v4"]
        self.assertEqual(f1["method_fingerprints"], f2["method_fingerprints"])
        self.assertEqual(f1["total_methods"], f2["total_methods"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

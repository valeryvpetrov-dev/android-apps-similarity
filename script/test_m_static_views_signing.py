#!/usr/bin/env python3
"""EXEC-089: интеграция signing_view в extract_all_features.

Проверяет, что extract_all_features возвращает ключ `signing` с
хешем сертификата подписи APK. Ключ должен существовать и в quick,
и в enhanced режиме; при отсутствии apk_path hash = None.
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

APK_DIR = _PROJECT_ROOT / 'apk'
APK_NON_OPTIMIZED = APK_DIR / 'simple_app' / 'simple_app-releaseNonOptimized.apk'


class TestExtractAllFeaturesSigning(unittest.TestCase):
    """Поле `signing` появляется в результате extract_all_features."""

    def test_quick_mode_includes_signing_key(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        self.assertIn('signing', features)

    def test_quick_mode_signing_is_dict(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        self.assertIsInstance(features['signing'], dict)
        self.assertIn('hash', features['signing'])

    def test_quick_mode_signing_hash_is_str_or_none(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        sig_hash = features['signing']['hash']
        self.assertTrue(sig_hash is None or isinstance(sig_hash, str))

    def test_quick_mode_signing_hash_is_64_hex_for_signed_apk(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        sig_hash = features['signing']['hash']
        if sig_hash is None:
            self.skipTest('У тестового APK нет подписи')
        self.assertEqual(len(sig_hash), 64)
        int(sig_hash, 16)

    def test_enhanced_mode_without_apk_path_has_none_signing(self):
        """Без apk_path извлечь хеш подписи нельзя — поле должно быть None."""
        unpacked = APK_DIR / 'simple_app'
        if not unpacked.is_dir():
            self.skipTest('Тестовая распакованная директория не найдена')
        features = extract_all_features(unpacked_dir=str(unpacked))
        self.assertIn('signing', features)
        self.assertIsNone(features['signing']['hash'])

    def test_other_layer_keys_still_present(self):
        """EXEC-089 не должен ломать существующий контракт ключей."""
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        for key in ('code', 'component', 'resource', 'metadata', 'library', 'mode'):
            self.assertIn(key, features, f'Ключ {key!r} потерян')


if __name__ == '__main__':
    unittest.main(verbosity=2)

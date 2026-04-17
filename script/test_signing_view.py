#!/usr/bin/env python3
"""Тесты для signing_view.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from signing_view import (
    extract_apk_signature_hash,
    compare_signatures,
    extract_apk_signatures_v2_fingerprint,
)

APK_DIR = _PROJECT_ROOT / 'apk'
APK_NON_OPTIMIZED = APK_DIR / 'simple_app' / 'simple_app-releaseNonOptimized.apk'


class TestExtractApkSignatureHash(unittest.TestCase):

    def test_nonexistent_file_returns_none(self):
        self.assertIsNone(extract_apk_signature_hash(Path('/tmp/nonexistent_xyz_123.apk')))

    def test_directory_returns_none(self):
        self.assertIsNone(extract_apk_signature_hash(APK_DIR))

    def test_real_apk_returns_hex_hash(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        h = extract_apk_signature_hash(APK_NON_OPTIMIZED)
        if h is None:
            self.skipTest('У тестового APK нет подписи')
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)
        int(h, 16)

    def test_same_apk_same_hash(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        h1 = extract_apk_signature_hash(APK_NON_OPTIMIZED)
        h2 = extract_apk_signature_hash(APK_NON_OPTIMIZED)
        if h1 is None:
            self.skipTest('Нет подписи')
        self.assertEqual(h1, h2)


class TestCompareSignatures(unittest.TestCase):

    def test_both_none(self):
        r = compare_signatures(None, None)
        self.assertEqual(r, {'score': 0.0, 'status': 'missing'})

    def test_one_none(self):
        self.assertEqual(compare_signatures('a', None)['status'], 'missing')
        self.assertEqual(compare_signatures(None, 'b')['status'], 'missing')

    def test_match(self):
        r = compare_signatures('abcd1234', 'abcd1234')
        self.assertEqual(r, {'score': 1.0, 'status': 'match'})

    def test_mismatch(self):
        r = compare_signatures('abcd', 'ffff')
        self.assertEqual(r, {'score': 0.0, 'status': 'mismatch'})


class TestV2Fingerprint(unittest.TestCase):

    def test_nonexistent_file(self):
        self.assertIsNone(extract_apk_signatures_v2_fingerprint(Path('/tmp/yyy_no.apk')))


if __name__ == '__main__':
    unittest.main(verbosity=2)

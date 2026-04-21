#!/usr/bin/env python3
"""EXEC-R_metadata_v2-CREATOR: creator-centric разрешения и цепочка сертификатов.

Проверяет:
1. Маппинг разрешения на creator-группу:
   - android.permission.* -> "android";
   - com.google.* -> "google";
   - com.facebook.* -> "facebook";
   - неизвестное -> "third_party".
2. Появление параллельных токенов ``perm_group:<creator>:<permission>``
   рядом со старыми ``uses_permission:*`` в metadata.
3. Наличие непустой цепочки сертификатов подписи для simple_app:
   хотя бы один сертификат с непустыми Issuer / Subject / SHA-256 (64 hex).

Тесты корректно пропускаются, если нет тестового APK, нет cryptography
или нет signing_view.extract_signing_chain в текущей ветке.
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

from m_static_views import (
    PERMISSION_CREATOR_GROUPS,
    _creator_group_for_permission,
    _enrich_metadata_with_perm_groups,
    extract_all_features,
)

try:
    from signing_view import extract_signing_chain as _extract_signing_chain
except ImportError:
    _extract_signing_chain = None

try:
    import cryptography  # noqa: F401
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

APK_DIR = _PROJECT_ROOT / 'apk'
APK_NON_OPTIMIZED = APK_DIR / 'simple_app' / 'simple_app-releaseNonOptimized.apk'


class TestCreatorGroupMapping(unittest.TestCase):
    """Маппинг permission -> creator_group."""

    def test_android_permission_maps_to_android(self):
        group = _creator_group_for_permission('android.permission.INTERNET')
        self.assertEqual(group, 'android')

    def test_google_permission_maps_to_google(self):
        group = _creator_group_for_permission(
            'com.google.android.c2dm.permission.RECEIVE'
        )
        self.assertEqual(group, 'google')

    def test_facebook_permission_maps_to_facebook(self):
        group = _creator_group_for_permission(
            'com.facebook.permission.prod.FB_APP_COMMUNICATION'
        )
        self.assertEqual(group, 'facebook')

    def test_unknown_permission_maps_to_third_party(self):
        group = _creator_group_for_permission(
            'com.example.myapp.permission.CUSTOM'
        )
        self.assertEqual(group, 'third_party')

    def test_mapping_table_contains_expected_groups(self):
        groups = {g for _prefix, g in PERMISSION_CREATOR_GROUPS}
        self.assertIn('android', groups)
        self.assertIn('google', groups)
        self.assertIn('facebook', groups)


class TestEnrichMetadataWithPermGroups(unittest.TestCase):
    """Enrichment metadata рядом со старыми uses_permission-токенами."""

    def test_adds_perm_group_tokens_in_parallel(self):
        source = {
            'uses_permission:android.permission.INTERNET',
            'uses_permission:com.google.android.c2dm.permission.RECEIVE',
            'uses_permission:com.example.custom.FOO',
        }
        enriched = _enrich_metadata_with_perm_groups(source)
        # Старые токены обязаны остаться
        self.assertTrue(source.issubset(enriched))
        # Добавились новые перпендикулярные токены
        self.assertIn(
            'perm_group:android:android.permission.INTERNET', enriched
        )
        self.assertIn(
            'perm_group:google:com.google.android.c2dm.permission.RECEIVE',
            enriched,
        )
        self.assertIn(
            'perm_group:third_party:com.example.custom.FOO', enriched
        )

    def test_empty_metadata_returns_empty(self):
        self.assertEqual(_enrich_metadata_with_perm_groups(set()), set())

    def test_no_permissions_leaves_metadata_unchanged(self):
        source = {'apk_name:foo', 'dex_version:035', 'signing_present:1'}
        enriched = _enrich_metadata_with_perm_groups(source)
        self.assertEqual(enriched, source)

    def test_extract_all_features_includes_perm_group_tokens(self):
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        metadata = features.get('metadata', set())
        has_uses_permission = any(
            tok.startswith('uses_permission:') for tok in metadata
        )
        has_perm_group = any(
            tok.startswith('perm_group:') for tok in metadata
        )
        # Либо разрешений нет совсем, либо оба вида токенов живут рядом.
        if has_uses_permission:
            self.assertTrue(
                has_perm_group,
                'Если есть uses_permission:*, должны появиться perm_group:*',
            )


class TestSigningChain(unittest.TestCase):
    """Полная цепочка сертификатов: Issuer / Subject / SHA-256 на каждый сертификат."""

    def setUp(self):
        if _extract_signing_chain is None:
            self.skipTest('signing_view.extract_signing_chain недоступен')
        if not _HAS_CRYPTO:
            self.skipTest('cryptography не установлен')
        if not APK_NON_OPTIMIZED.exists():
            self.skipTest('Тестовый APK не найден')

    def test_chain_is_non_empty_for_signed_apk(self):
        chain = _extract_signing_chain(APK_NON_OPTIMIZED)
        self.assertIsInstance(chain, list)
        self.assertGreaterEqual(len(chain), 1, 'simple_app должен быть подписан')

    def test_chain_entries_have_issuer_subject_sha256(self):
        chain = _extract_signing_chain(APK_NON_OPTIMIZED)
        self.assertGreaterEqual(len(chain), 1)
        for entry in chain:
            self.assertIn('issuer', entry)
            self.assertIn('subject', entry)
            self.assertIn('sha256', entry)
            self.assertIsInstance(entry['issuer'], str)
            self.assertIsInstance(entry['subject'], str)
            self.assertIsInstance(entry['sha256'], str)
            self.assertTrue(entry['issuer'], 'Issuer не должен быть пустым')
            self.assertTrue(entry['subject'], 'Subject не должен быть пустым')
            self.assertEqual(
                len(entry['sha256']), 64,
                'SHA-256 сертификата должен быть 64 hex-символа',
            )
            int(entry['sha256'], 16)  # валидный hex

    def test_extract_all_features_signing_has_chain_key(self):
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        signing = features.get('signing', {})
        self.assertIn('chain', signing)
        self.assertIsInstance(signing['chain'], list)

    def test_extract_all_features_chain_matches_direct_call(self):
        features = extract_all_features(apk_path=str(APK_NON_OPTIMIZED))
        via_features = features['signing']['chain']
        via_direct = _extract_signing_chain(APK_NON_OPTIMIZED)
        self.assertEqual(via_features, via_direct)


if __name__ == '__main__':
    unittest.main(verbosity=2)

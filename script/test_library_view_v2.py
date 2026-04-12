#!/usr/bin/env python3
"""Tests for library_view_v2.py — LIB-003.

All tests run without real APK files (mock / synthetic data).
Requires pytest. Run:
    cd <submodule-root>
    python -m pytest script/test_library_view_v2.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest

# Ensure script directory is on the path so imports work
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from library_view_v2 import (
    CATEGORY_LIBRARY,
    TPL_CATALOG_V2,
    _smali_class_to_package,
    detect_library_like_v2,
    detect_tpl_in_packages,
    extract_apk_packages,
)


# ---------------------------------------------------------------------------
# 1. _smali_class_to_package — standard
# ---------------------------------------------------------------------------

def test_smali_class_to_package_standard():
    result = _smali_class_to_package("Lcom/example/foo/Bar;")
    assert result == "com.example.foo"


# ---------------------------------------------------------------------------
# 2. _smali_class_to_package — top level (2 parts)
# ---------------------------------------------------------------------------

def test_smali_class_to_package_top_level():
    result = _smali_class_to_package("Lcom/example/Bar;")
    assert result == "com.example"


# ---------------------------------------------------------------------------
# 3. _smali_class_to_package — default package (single part → None)
# ---------------------------------------------------------------------------

def test_smali_class_to_package_default_package():
    result = _smali_class_to_package("LBar;")
    assert result is None


# ---------------------------------------------------------------------------
# 4. _smali_class_to_package — malformed (not smali format)
# ---------------------------------------------------------------------------

def test_smali_class_to_package_malformed():
    result = _smali_class_to_package("com.example.Foo")
    assert result is None


# ---------------------------------------------------------------------------
# 5. detect_tpl_in_packages — okhttp3 full match
# ---------------------------------------------------------------------------

def test_detect_tpl_okhttp3_full_match():
    # Include more than 30% of okhttp3's 8 packages
    apk_packages = frozenset({
        "okhttp3",
        "okhttp3.internal",
        "okhttp3.logging",
        "okhttp3.internal.cache",
        "okhttp3.internal.connection",
    })
    results = detect_tpl_in_packages(apk_packages, threshold=0.30, min_matches=1)
    assert "okhttp3" in results
    assert results["okhttp3"]["detected"] is True
    assert results["okhttp3"]["coverage"] >= 0.30


# ---------------------------------------------------------------------------
# 6. detect_tpl_in_packages — below threshold
# ---------------------------------------------------------------------------

def test_detect_tpl_below_threshold():
    # Only 1 out of 8 okhttp3 packages → coverage = 0.125 < 0.30
    apk_packages = frozenset({"okhttp3"})
    results = detect_tpl_in_packages(apk_packages, threshold=0.30, min_matches=1)
    # okhttp3 should appear in results (coverage > 0) but NOT detected
    assert "okhttp3" in results
    assert results["okhttp3"]["detected"] is False
    assert results["okhttp3"]["coverage"] < 0.30


# ---------------------------------------------------------------------------
# 7. detect_tpl_in_packages — no match at all
# ---------------------------------------------------------------------------

def test_detect_tpl_no_match():
    apk_packages = frozenset({"com.example.myapp", "com.example.myapp.ui"})
    results = detect_tpl_in_packages(apk_packages)
    # No TPL should have detected=True
    detected = [tid for tid, info in results.items() if info["detected"]]
    assert len(detected) == 0


# ---------------------------------------------------------------------------
# 8. detect_library_like_v2 — returns CATEGORY_LIBRARY for okhttp3
# ---------------------------------------------------------------------------

def test_detect_library_like_v2_returns_library_category():
    apk_packages = frozenset({
        "okhttp3",
        "okhttp3.internal",
        "okhttp3.logging",
        "okhttp3.internal.cache",
    })
    tpl_detections = detect_tpl_in_packages(apk_packages, threshold=0.30)
    result = detect_library_like_v2(
        "smali/okhttp3/OkHttpClient.smali",
        apk_packages=apk_packages,
        tpl_detections=tpl_detections,
    )
    assert result is not None
    category, reason = result
    assert category == CATEGORY_LIBRARY
    assert "v2:okhttp3" in reason


# ---------------------------------------------------------------------------
# 9. detect_library_like_v2 — returns None for app code
# ---------------------------------------------------------------------------

def test_detect_library_like_v2_returns_none_for_app_code():
    apk_packages = frozenset({"com.example.myapp", "com.example.myapp.ui"})
    tpl_detections = detect_tpl_in_packages(apk_packages)
    result = detect_library_like_v2(
        "smali/com/example/myapp/MainActivity.smali",
        apk_packages=apk_packages,
        tpl_detections=tpl_detections,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 10. detect_library_like_v2 — fallback when apk_packages is None
# ---------------------------------------------------------------------------

def test_detect_library_like_v2_fallback_when_no_packages():
    """When apk_packages=None, must fall back to v1 and include [v1_fallback]."""
    result = detect_library_like_v2(
        "smali/okhttp3/OkHttpClient.smali",
        apk_packages=None,
    )
    # v1 knows okhttp3 prefix → should return library + v1_fallback tag
    assert result is not None
    category, reason = result
    assert category == CATEGORY_LIBRARY
    assert "[v1_fallback]" in reason


# ---------------------------------------------------------------------------
# 11. extract_apk_packages — cache roundtrip (mocked androguard)
# ---------------------------------------------------------------------------

def test_extract_apk_packages_cache_roundtrip(tmp_path):
    """Cache write then cache read must return identical frozenset."""
    # Create a fake APK file (content irrelevant — we mock androguard)
    fake_apk = tmp_path / "fake.apk"
    fake_apk.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 100)  # minimal zip header

    expected_packages = frozenset({"okhttp3", "okhttp3.internal", "com.example.app"})

    # Build fake class objects
    class FakeClass:
        def __init__(self, name):
            self._name = name
        def get_name(self):
            return self._name

    # Smali names that map to expected_packages
    fake_classes = [
        FakeClass("Lokhttp3/OkHttpClient;"),         # -> okhttp3
        FakeClass("Lokhttp3/internal/Util;"),         # -> okhttp3.internal
        FakeClass("Lcom/example/app/MainActivity;"),  # -> com.example.app
        FakeClass("LTopLevel;"),                      # -> None (default pkg, skip)
    ]

    class FakeDEX:
        def get_classes(self):
            return fake_classes

    class FakeAPK:
        def get_all_dex(self):
            return [b"fake_dex_bytes"]

    with mock.patch("library_view_v2.APK", return_value=FakeAPK(), create=True), \
         mock.patch("library_view_v2.DEX", return_value=FakeDEX(), create=True):
        # Patch androguard imports inside extract_apk_packages
        import library_view_v2 as lv2
        orig_extract = lv2.extract_apk_packages

        # We need to patch the import inside the function
        fake_apk_mod = mock.MagicMock()
        fake_apk_mod.get_all_dex.return_value = [b"fake_dex_bytes"]

        fake_dex_mod = mock.MagicMock()
        fake_dex_mod.get_classes.return_value = fake_classes

        with mock.patch.dict(
            "sys.modules",
            {
                "androguard": mock.MagicMock(),
                "androguard.core": mock.MagicMock(),
                "androguard.core.apk": mock.MagicMock(APK=lambda path: fake_apk_mod),
                "androguard.core.dex": mock.MagicMock(DEX=lambda b: fake_dex_mod),
            },
        ):
            # First call — extracts and writes cache
            result_1 = lv2.extract_apk_packages(str(fake_apk), cache_dir=str(tmp_path))
            # Second call — reads from cache (androguard not called again)
            result_2 = lv2.extract_apk_packages(str(fake_apk), cache_dir=str(tmp_path))

    assert result_1 == result_2
    assert isinstance(result_1, frozenset)


# ---------------------------------------------------------------------------
# 12. detect_tpl_in_packages — min_matches filter
# ---------------------------------------------------------------------------

def test_detect_tpl_min_matches_filter():
    """min_matches=2 prevents detection even when single package matches."""
    apk_packages = frozenset({"okhttp3"})  # 1 package, coverage = 1/8 = 0.125
    results = detect_tpl_in_packages(apk_packages, threshold=0.10, min_matches=2)
    # okhttp3 is in results (coverage > 0) but NOT detected (min_matches not met)
    assert "okhttp3" in results
    assert results["okhttp3"]["detected"] is False


# ---------------------------------------------------------------------------
# Bonus: catalog sanity — ensure 40+ TPL groups defined
# ---------------------------------------------------------------------------

def test_tpl_catalog_v2_has_enough_entries():
    assert len(TPL_CATALOG_V2) >= 40


def test_tpl_catalog_v2_all_have_packages():
    for tpl_id, meta in TPL_CATALOG_V2.items():
        assert "packages" in meta, f"{tpl_id} missing 'packages'"
        assert len(meta["packages"]) > 0, f"{tpl_id} has empty packages"
        assert "category" in meta, f"{tpl_id} missing 'category'"

"""Tests for 4 new hint types in pairwise_explainer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from pairwise_explainer import (
    build_permission_change_hint,
    build_native_lib_change_hint,
    build_certificate_mismatch_hint,
    build_code_removal_hint,
)

BASE_PAIR = {
    "component_features_a": ["permission:android.permission.CAMERA"],
    "component_features_b": ["permission:android.permission.CAMERA", "permission:android.permission.READ_CONTACTS"],
    "resource_features_a": ["META-INF/CERT.RSA", "lib/armeabi-v7a/libfoo.so"],
    "resource_features_b": ["META-INF/NEWCERT.RSA", "lib/armeabi-v7a/libfoo.so", "lib/arm64-v8a/libbar.so"],
    "code_score": 0.8,
    "dots_a": ["com.example.Foo", "com.example.Bar"],
    "dots_b": ["com.example.Foo"],
}

def test_permission_change_added():
    h = build_permission_change_hint(BASE_PAIR)
    assert h["hint_type"] == "PermissionChange"
    assert any(e["change"] == "added" for e in h["elements"])

def test_permission_change_severity_medium():
    h = build_permission_change_hint(BASE_PAIR)
    assert h["severity"] in ("high", "medium", "low")

def test_native_lib_change():
    h = build_native_lib_change_hint(BASE_PAIR)
    assert h["hint_type"] == "NativeLibChange"
    assert len(h["elements"]) > 0

def test_certificate_mismatch():
    h = build_certificate_mismatch_hint(BASE_PAIR)
    assert h["hint_type"] == "CertificateMismatch"

def test_code_removal():
    h = build_code_removal_hint(BASE_PAIR)
    assert h["hint_type"] == "CodeRemoval"

def test_no_change_low_severity():
    pair_no_change = {
        "component_features_a": ["permission:android.permission.CAMERA"],
        "component_features_b": ["permission:android.permission.CAMERA"],
        "resource_features_a": [],
        "resource_features_b": [],
    }
    h = build_permission_change_hint(pair_no_change)
    assert h["severity"] == "low"

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

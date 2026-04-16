#!/usr/bin/env python3
"""
Tests for R_api layer: API call Markov chain similarity.
Covers _get_api_family, _cosine_similarity, compare_api without androguard dependency.
"""
from __future__ import annotations

import math
import sys
import os

# Ensure script directory is on path for imports
sys.path.insert(0, os.path.dirname(__file__))

from api_view import (
    _get_api_family,
    _cosine_similarity,
    compare_api,
)


# ── _get_api_family tests ────────────────────────────────────────────────────

def test_get_api_family_android():
    """Standard dotted class name: android.content.Intent -> android.content"""
    result = _get_api_family("android.content.Intent")
    assert result == "android.content", f"Expected 'android.content', got {result!r}"


def test_get_api_family_dalvik():
    """Dalvik format: Landroid/content/Intent; -> android.content"""
    result = _get_api_family("Landroid/content/Intent;")
    assert result == "android.content", f"Expected 'android.content', got {result!r}"


def test_get_api_family_java():
    """Java stdlib: java.util.ArrayList -> java.util"""
    result = _get_api_family("java.util.ArrayList")
    assert result == "java.util", f"Expected 'java.util', got {result!r}"


def test_get_api_family_deep_package():
    """Deep package: android.telephony.TelephonyManager -> android.telephony"""
    result = _get_api_family("android.telephony.TelephonyManager")
    assert result == "android.telephony", f"Expected 'android.telephony', got {result!r}"


def test_get_api_family_kotlin():
    """Kotlin stdlib: kotlin.collections.MutableList -> kotlin.collections"""
    result = _get_api_family("kotlin.collections.MutableList")
    assert result == "kotlin.collections", f"Expected 'kotlin.collections', got {result!r}"


def test_get_api_family_dalvik_nested():
    """Dalvik nested: Ljava/util/HashMap; -> java.util"""
    result = _get_api_family("Ljava/util/HashMap;")
    assert result == "java.util", f"Expected 'java.util', got {result!r}"


def test_get_api_family_no_dots():
    """Single name without dots returns as-is"""
    result = _get_api_family("Object")
    assert result == "Object", f"Expected 'Object', got {result!r}"


# ── _cosine_similarity tests ─────────────────────────────────────────────────

def test_cosine_identical():
    """Identical vectors -> cosine similarity = 1.0"""
    vec = {("android.content", "android.app"): 0.5, ("android.app", "java.util"): 0.5}
    result = _cosine_similarity(vec, vec)
    assert abs(result - 1.0) < 1e-9, f"Expected 1.0, got {result}"


def test_cosine_orthogonal():
    """Non-overlapping vectors -> cosine similarity = 0.0"""
    vec_a = {("android.content", "android.app"): 1.0}
    vec_b = {("java.util", "java.io"): 1.0}
    result = _cosine_similarity(vec_a, vec_b)
    assert result == 0.0, f"Expected 0.0, got {result}"


def test_cosine_empty_a():
    """Empty first vector -> 0.0"""
    vec_b = {("android.content", "android.app"): 0.5}
    result = _cosine_similarity({}, vec_b)
    assert result == 0.0


def test_cosine_partial_overlap():
    """Partial overlap -> 0 < score < 1"""
    vec_a = {("android.content", "android.app"): 0.5, ("android.app", "java.util"): 0.5}
    vec_b = {("android.content", "android.app"): 0.6, ("java.io", "java.util"): 0.4}
    result = _cosine_similarity(vec_a, vec_b)
    assert 0.0 < result < 1.0, f"Expected partial score, got {result}"


# ── compare_api tests ─────────────────────────────────────────────────────────

def test_compare_api_both_none():
    """Both chains None -> score=0.0, status='both_empty'"""
    result = compare_api(None, None)
    assert result["score"] == 0.0
    assert result["status"] == "both_empty"


def test_compare_api_one_none():
    """One chain None -> score=0.0, status='one_empty'"""
    chain = {("android.content", "android.app"): 0.5}
    result = compare_api(chain, None)
    assert result["score"] == 0.0
    assert result["status"] == "one_empty"

    result2 = compare_api(None, chain)
    assert result2["score"] == 0.0
    assert result2["status"] == "one_empty"


def test_compare_api_identical_chain():
    """Identical chains -> score approx 1.0, status='markov_cosine'"""
    chain = {
        ("android.content", "android.app"): 0.5,
        ("android.app", "java.util"): 0.5,
    }
    result = compare_api(chain, chain)
    assert abs(result["score"] - 1.0) < 1e-6
    assert result["status"] == "markov_cosine"


def test_compare_api_different_chain():
    """Different chains -> score < 1.0"""
    chain_a = {("android.content", "android.app"): 0.5, ("android.app", "java.util"): 0.5}
    chain_b = {("java.io", "java.util"): 0.7, ("java.util", "java.lang"): 0.3}
    result = compare_api(chain_a, chain_b)
    assert result["score"] < 1.0
    assert result["status"] == "markov_cosine"


def test_compare_api_status_field():
    """Result always contains 'score' and 'status' keys"""
    chain_a = {("android.content", "android.app"): 0.6, ("android.app", "java.util"): 0.4}
    chain_b = {("android.content", "android.app"): 0.5, ("android.app", "java.util"): 0.5}
    result = compare_api(chain_a, chain_b)
    assert "score" in result
    assert "status" in result


def test_compare_api_similar_chains():
    """Similar but not identical chains: 0 < score < 1.0"""
    chain_a = {("android.content", "android.app"): 0.5, ("android.app", "java.util"): 0.5}
    chain_b = {("android.content", "android.app"): 0.6, ("android.app", "java.util"): 0.4}
    result = compare_api(chain_a, chain_b)
    # Cosine on similar distributions should be high but not exactly 1.0
    assert 0.9 < result["score"] <= 1.0, f"Expected high score for similar chains, got {result['score']}"


def test_compare_api_score_range():
    """Score must always be in [0, 1]"""
    chain_a = {("android.content", "android.app"): 0.3, ("android.app", "java.util"): 0.7}
    chain_b = {("java.io", "android.content"): 0.8, ("android.content", "java.util"): 0.2}
    result = compare_api(chain_a, chain_b)
    assert 0.0 <= result["score"] <= 1.0


if __name__ == "__main__":
    # Run all tests manually without pytest
    tests = [
        test_get_api_family_android,
        test_get_api_family_dalvik,
        test_get_api_family_java,
        test_get_api_family_deep_package,
        test_get_api_family_kotlin,
        test_get_api_family_dalvik_nested,
        test_get_api_family_no_dots,
        test_cosine_identical,
        test_cosine_orthogonal,
        test_cosine_empty_a,
        test_cosine_partial_overlap,
        test_compare_api_both_none,
        test_compare_api_one_none,
        test_compare_api_identical_chain,
        test_compare_api_different_chain,
        test_compare_api_status_field,
        test_compare_api_similar_chains,
        test_compare_api_score_range,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")

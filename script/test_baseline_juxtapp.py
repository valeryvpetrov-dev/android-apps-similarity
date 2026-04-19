#!/usr/bin/env python3
"""Tests for EXEC-081: baseline_juxtapp — Juxtapp-style bit-vector baseline.

Run from project root or script/ directory:
  python3 -m unittest script.test_baseline_juxtapp -v

Test APKs (relative to project root):
  apk/simple_app/simple_app-releaseNonOptimized.apk  (primary)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from script.baseline_juxtapp import (
        DEFAULT_NGRAM_SIZE,
        DEFAULT_VECTOR_BITS,
        MODE,
        compare_juxtapp,
        extract_juxtapp_vector,
    )
except ImportError:
    from baseline_juxtapp import (  # type: ignore[no-redef]
        DEFAULT_NGRAM_SIZE,
        DEFAULT_VECTOR_BITS,
        MODE,
        compare_juxtapp,
        extract_juxtapp_vector,
    )


# ---------------------------------------------------------------------------
# APK paths
# ---------------------------------------------------------------------------

_APK_DIR = _PROJECT_ROOT / "apk"
APK_NON_OPTIMIZED = _APK_DIR / "simple_app" / "simple_app-releaseNonOptimized.apk"


def _require_apk(path: Path) -> Path:
    if not path.exists():
        raise unittest.SkipTest(f"Test APK not found: {path}")
    return path


# ---------------------------------------------------------------------------
# extract_juxtapp_vector
# ---------------------------------------------------------------------------

class TestExtractJuxtappVector(unittest.TestCase):
    """Structural tests for extract_juxtapp_vector()."""

    def test_structure_fields_present(self):
        """Result carries vector, total_ngrams, mode — the contract."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_juxtapp_vector(str(apk))
        self.assertIn("vector", features)
        self.assertIn("total_ngrams", features)
        self.assertIn("mode", features)
        self.assertEqual(features["mode"], MODE)
        self.assertEqual(features["mode"], "juxtapp-v1")

    def test_vector_length_matches_vector_bits(self):
        """The bit array is exactly ``vector_bits`` entries long."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        # Default length.
        features_default = extract_juxtapp_vector(str(apk))
        self.assertEqual(len(features_default["vector"]), DEFAULT_VECTOR_BITS)
        # Custom length — still matches.
        features_custom = extract_juxtapp_vector(str(apk), vector_bits=2048)
        self.assertEqual(len(features_custom["vector"]), 2048)

    def test_set_bits_leq_total_ngrams(self):
        """Number of set bits cannot exceed ``total_ngrams`` (collisions
        can only reduce the count, never inflate it)."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_juxtapp_vector(str(apk))
        set_bits = sum(features["vector"])
        self.assertLessEqual(set_bits, features["total_ngrams"])
        # Every vector entry is a 0/1 bit.
        self.assertTrue(
            all(b in (0, 1) for b in features["vector"]),
            "vector must contain only 0/1 entries",
        )

    def test_deterministic(self):
        """Re-extracting from the same APK yields the same vector."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        f1 = extract_juxtapp_vector(str(apk))
        f2 = extract_juxtapp_vector(str(apk))
        self.assertEqual(f1["vector"], f2["vector"])
        self.assertEqual(f1["total_ngrams"], f2["total_ngrams"])
        self.assertEqual(f1["mode"], f2["mode"])

    def test_simple_app_has_ngrams(self):
        """The test APK yields at least one n-gram — the bit vector is
        non-trivial and downstream comparison is meaningful."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_juxtapp_vector(str(apk))
        self.assertGreaterEqual(features["total_ngrams"], 1)
        self.assertGreaterEqual(sum(features["vector"]), 1)


# ---------------------------------------------------------------------------
# compare_juxtapp
# ---------------------------------------------------------------------------

def _feature_dict(vector: list[int], total_ngrams: int = 0) -> dict:
    """Tiny helper: build a juxtapp-v1 shaped dict from a raw bit vector."""
    return {
        "vector": list(vector),
        "total_ngrams": total_ngrams,
        "mode": MODE,
    }


class TestCompareJuxtapp(unittest.TestCase):
    """Logic tests for compare_juxtapp() — no APK required."""

    def test_identical_features_score_one(self):
        """Identical feature dicts yield Jaccard=1.0 and status=ok."""
        vec = [0] * DEFAULT_VECTOR_BITS
        vec[10] = 1
        vec[256] = 1
        vec[1024] = 1
        features = _feature_dict(vec, total_ngrams=3)
        r = compare_juxtapp(features, features)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["matched_bits"], 3)
        self.assertEqual(r["union_bits"], 3)
        self.assertEqual(r["status"], "ok")

    def test_empty_features_status_empty(self):
        """All-zero vectors (or None sides) yield status=empty, score=0."""
        empty = _feature_dict([0] * DEFAULT_VECTOR_BITS, total_ngrams=0)
        r = compare_juxtapp(empty, empty)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["status"], "empty")
        self.assertEqual(r["matched_bits"], 0)
        self.assertEqual(r["union_bits"], 0)
        # None-side — also counted as empty for this baseline.
        r2 = compare_juxtapp(None, empty)
        self.assertEqual(r2["status"], "empty")
        self.assertEqual(r2["score"], 0.0)

    def test_mismatched_vector_bits(self):
        """Vectors of different lengths cannot be Jaccard-compared."""
        fa = _feature_dict([1, 0, 1, 0], total_ngrams=2)
        fb = _feature_dict([1, 0, 1, 0, 1, 0, 1, 0], total_ngrams=4)
        r = compare_juxtapp(fa, fb)
        self.assertEqual(r["status"], "mismatched_vector_bits")
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["matched_bits"], 0)
        self.assertEqual(r["union_bits"], 0)

    def test_partial_overlap_jaccard_math(self):
        """A = {0,2,4}, B = {2,4,6,8} — Jaccard = 2/5."""
        vec_a = [0] * 16
        vec_b = [0] * 16
        for i in (0, 2, 4):
            vec_a[i] = 1
        for i in (2, 4, 6, 8):
            vec_b[i] = 1
        fa = _feature_dict(vec_a, total_ngrams=3)
        fb = _feature_dict(vec_b, total_ngrams=4)
        r = compare_juxtapp(fa, fb)
        self.assertEqual(r["matched_bits"], 2)
        self.assertEqual(r["union_bits"], 5)
        self.assertAlmostEqual(r["score"], 2 / 5, places=6)
        self.assertEqual(r["status"], "ok")


# ---------------------------------------------------------------------------
# Integration with a real APK
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):

    def test_simple_app_total_ngrams_positive(self):
        """The known-good test APK yields a non-trivial n-gram count and
        self-compare returns Jaccard=1.0."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_juxtapp_vector(str(apk))
        self.assertGreaterEqual(features["total_ngrams"], 1)
        r = compare_juxtapp(features, features)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["status"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)

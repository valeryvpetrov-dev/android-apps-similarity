#!/usr/bin/env python3
"""Tests for EXEC-082a: code_view_v4 — method-level fuzzy opcode fingerprint.

Run from project root or script/ directory:
  python3 -m unittest script.test_code_view_v4 -v
  python3 -m pytest script/test_code_view_v4.py -v

Test APKs (relative to project root):
  apk/simple_app/simple_app-releaseNonOptimized.apk  (primary)
  apk/simple_app/simple_app-releaseRename.apk        (clone)
  apk/snake/snake.apk                                (non-clone)
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
    import script.code_view_v4 as code_v4
    from script.code_view_v4 import (
        MODE,
        compare_code_v4,
        extract_code_view_v4,
        _simhash_fingerprint,
        _TLSH_AVAILABLE,
    )
except ImportError:
    import code_view_v4  # type: ignore[no-redef]
    from code_view_v4 import (  # type: ignore[no-redef]
        MODE,
        compare_code_v4,
        extract_code_view_v4,
        _simhash_fingerprint,
        _TLSH_AVAILABLE,
    )


# ---------------------------------------------------------------------------
# APK paths
# ---------------------------------------------------------------------------

_APK_DIR = _PROJECT_ROOT / "apk"
APK_NON_OPTIMIZED = _APK_DIR / "simple_app" / "simple_app-releaseNonOptimized.apk"
APK_RENAME = _APK_DIR / "simple_app" / "simple_app-releaseRename.apk"
APK_SNAKE = _APK_DIR / "snake" / "snake.apk"


def _require_apk(path: Path) -> Path:
    if not path.exists():
        raise unittest.SkipTest(f"Test APK not found: {path}")
    return path


# ---------------------------------------------------------------------------
# extract_code_view_v4
# ---------------------------------------------------------------------------

class TestExtractCodeViewV4(unittest.TestCase):
    """Tests for extract_code_view_v4()."""

    def test_nonexistent_file_returns_none(self):
        """Missing APK -> None, no exception."""
        self.assertIsNone(
            extract_code_view_v4(Path("/tmp/does_not_exist_4782.apk"))
        )

    def test_directory_returns_none(self):
        """Directory instead of APK -> None."""
        self.assertIsNone(extract_code_view_v4(_APK_DIR))

    def test_structure_fields_present(self):
        """Result has method_fingerprints, total_methods, mode."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        self.assertIsNotNone(features)
        self.assertIn("method_fingerprints", features)
        self.assertIn("total_methods", features)
        self.assertIn("mode", features)
        self.assertEqual(features["mode"], MODE)
        self.assertEqual(features["mode"], "v4")

    def test_real_apk_has_methods(self):
        """Non-trivial APK yields non-empty method_fingerprints."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        self.assertIsNotNone(features)
        fps = features["method_fingerprints"]
        self.assertIsInstance(fps, dict)
        self.assertGreater(len(fps), 0, "Expected non-empty method_fingerprints")
        self.assertEqual(features["total_methods"], len(fps))

    def test_method_id_shape(self):
        """method_id keys look like 'Lclass/Descriptor;->name(proto)return'."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        for method_id, fp in features["method_fingerprints"].items():
            self.assertIsInstance(method_id, str)
            self.assertIn("->", method_id)
            self.assertTrue(
                method_id.startswith("L"),
                f"Expected Dalvik class descriptor, got: {method_id!r}",
            )
            self.assertIsInstance(fp, str)
            self.assertGreater(len(fp), 2, f"Fingerprint too short: {fp!r}")

    def test_fingerprint_prefix_known(self):
        """Every fingerprint carries a known backend prefix."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        for fp in features["method_fingerprints"].values():
            self.assertTrue(
                fp.startswith(("T:", "S:", "B:")),
                f"Unknown fingerprint backend: {fp!r}",
            )

    def test_deterministic(self):
        """Re-extracting from the same APK yields identical output."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        f1 = extract_code_view_v4(apk)
        f2 = extract_code_view_v4(apk)
        self.assertEqual(f1, f2)

    def test_simple_app_has_expected_methods(self):
        """simple_app non-optimized APK carries at least the known BuildConfig,
        Greeting and MainActivity methods."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        method_ids = set(features["method_fingerprints"].keys())
        for needle in (
            "Lcom/example/simpleapplication/MainActivity;->onCreate",
            "Lcom/example/simpleapplication/Greeting;->greet",
        ):
            self.assertTrue(
                any(mid.startswith(needle) for mid in method_ids),
                f"Expected a method id starting with {needle!r}, "
                f"got method_ids sample={list(method_ids)[:5]}",
            )


# ---------------------------------------------------------------------------
# compare_code_v4
# ---------------------------------------------------------------------------

def _feature_dict(pairs: dict[str, str]) -> dict:
    """Tiny helper: build a v4-shaped dict from a plain mapping."""
    return {
        "method_fingerprints": dict(pairs),
        "total_methods": len(pairs),
        "mode": MODE,
    }


def _simhash_feature_from_opcodes(method_id: str, opcodes: tuple[int, ...]) -> dict:
    return _feature_dict({method_id: _simhash_fingerprint(opcodes)})


class TestCompareCodeV4Logic(unittest.TestCase):
    """Pure logic tests — no APK required."""

    def test_both_none_returns_zero_with_both_empty_flag(self):
        # DEEP-20-BOTH-EMPTY-AUDIT: единая семантика both_empty.
        # Оба входа None → score=0.0 (а не 1.0!) + both_empty=True.
        # Ранее считалось «два пустых = клоны», что маскировало
        # отсутствие сигнала. Канонический контракт:
        # `D-2026-04-DEEP-20-BOTH-EMPTY`.
        r = compare_code_v4(None, None)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["status"], "both_empty")
        self.assertIs(r.get("both_empty"), True)

    def test_one_none_score_0(self):
        fa = _feature_dict({"Lfoo;->bar()V": "S:abcd"})
        r = compare_code_v4(fa, None)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["status"], "one_empty")
        r2 = compare_code_v4(None, fa)
        self.assertEqual(r2["score"], 0.0)
        self.assertEqual(r2["status"], "one_empty")

    def test_both_empty_returns_zero_with_both_empty_flag(self):
        # DEEP-20-BOTH-EMPTY-AUDIT: два пустых v4-бандла больше не
        # считаются клонами — score=0.0, both_empty=True.
        fa = _feature_dict({})
        r = compare_code_v4(fa, fa)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["status"], "both_empty")
        self.assertIs(r.get("both_empty"), True)

    def test_identical_features_score_1(self):
        pairs = {
            "Lcom/a;->one()V": "S:1111111111111111",
            "Lcom/a;->two()V": "S:2222222222222222",
        }
        fa = _feature_dict(pairs)
        fb = _feature_dict(pairs)
        r = compare_code_v4(fa, fb)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["matched_methods"], 2)
        self.assertEqual(r["union_methods"], 2)
        self.assertEqual(r["status"], "fuzzy_ok")

    def test_disjoint_features_score_0(self):
        fa = _feature_dict({"Lcom/a;->one()V": "S:1111"})
        fb = _feature_dict({"Lcom/b;->two()V": "S:2222"})
        r = compare_code_v4(fa, fb)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["matched_methods"], 0)
        self.assertEqual(r["union_methods"], 2)
        self.assertEqual(r["status"], "fuzzy_ok")

    def test_same_method_id_but_different_fp_does_not_match(self):
        """Maximally distant simhashes on the same method_id score zero."""
        fa = _feature_dict({"Lcom/a;->one()V": "S:0000000000000000"})
        fb = _feature_dict({"Lcom/a;->one()V": "S:ffffffffffffffff"})
        r = compare_code_v4(fa, fb)
        self.assertEqual(r["score"], 0.0)

    def test_partial_overlap(self):
        fa = _feature_dict({
            "Lcom/a;->one()V": "S:1",
            "Lcom/a;->two()V": "S:2",
        })
        fb = _feature_dict({
            "Lcom/a;->two()V": "S:2",
            "Lcom/a;->three()V": "S:3",
        })
        r = compare_code_v4(fa, fb)
        self.assertEqual(r["matched_methods"], 1)
        self.assertEqual(r["union_methods"], 3)
        self.assertAlmostEqual(r["score"], 0.5, places=6)

    def test_one_opcode_mutation_keeps_high_simhash_similarity(self):
        opcodes_a = tuple((i % 251) for i in range(120))
        opcodes_b = list(opcodes_a)
        opcodes_b[60] = 0xFF
        fa = _simhash_feature_from_opcodes("Lcom/a;->mutated()V", opcodes_a)
        fb = _simhash_feature_from_opcodes("Lcom/a;->mutated()V", tuple(opcodes_b))

        r = compare_code_v4(fa, fb)

        self.assertGreater(
            r["score"], 0.9,
            f"Expected fuzzy simhash similarity >0.9, got {r['score']}",
        )

    def test_half_methods_different_scores_half(self):
        fa = _feature_dict({
            "Lcom/a;->one()V": "S:0000000000000000",
            "Lcom/a;->two()V": "S:1111111111111111",
            "Lcom/a;->three()V": "S:0000000000000000",
            "Lcom/a;->four()V": "S:ffffffffffffffff",
        })
        fb = _feature_dict({
            "Lcom/a;->one()V": "S:0000000000000000",
            "Lcom/a;->two()V": "S:1111111111111111",
            "Lcom/a;->three()V": "S:ffffffffffffffff",
            "Lcom/a;->four()V": "S:0000000000000000",
        })

        r = compare_code_v4(fa, fb)

        self.assertAlmostEqual(r["score"], 0.5, places=6)

    def test_completely_different_same_methods_score_zero(self):
        fa = _feature_dict({
            "Lcom/a;->one()V": "S:0000000000000000",
            "Lcom/a;->two()V": "S:0000000000000000",
        })
        fb = _feature_dict({
            "Lcom/a;->one()V": "S:ffffffffffffffff",
            "Lcom/a;->two()V": "S:ffffffffffffffff",
        })

        r = compare_code_v4(fa, fb)

        self.assertEqual(r["score"], 0.0)

def test_code_v4_tlsh_diff_backend_scores_near_match(monkeypatch):
    class FakeTlsh:
        @staticmethod
        def diff(_left: str, _right: str) -> int:
            return 12

    monkeypatch.setattr(code_v4, "_TLSH_AVAILABLE", True)
    monkeypatch.setattr(code_v4, "_tlsh_module", FakeTlsh)
    fa = _feature_dict({"Lcom/a;->one()V": "T:T1ABC"})
    fb = _feature_dict({"Lcom/a;->one()V": "T:T2DEF"})

    r = compare_code_v4(fa, fb)

    assert r["score"] == 0.96


def test_code_v4_no_tlsh_simhash_backend_scores_near_match(monkeypatch):
    monkeypatch.setattr(code_v4, "_TLSH_AVAILABLE", False)
    fa = _feature_dict({"Lcom/a;->one()V": "S:0000000000000000"})
    fb = _feature_dict({"Lcom/a;->one()V": "S:0000000000000003"})

    r = compare_code_v4(fa, fb)

    assert r["score"] == 0.96875


class TestCompareCodeV4Integration(unittest.TestCase):
    """Integration tests requiring real APKs."""

    def test_self_compare_is_one(self):
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4(apk)
        r = compare_code_v4(features, features)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["matched_methods"], features["total_methods"])

    def test_non_clone_score_low(self):
        """simple_app vs snake should yield low Jaccard (disjoint method ids)."""
        apk_a = _require_apk(APK_NON_OPTIMIZED)
        apk_b = _require_apk(APK_SNAKE)
        f_a = extract_code_view_v4(apk_a)
        f_b = extract_code_view_v4(apk_b)
        r = compare_code_v4(f_a, f_b)
        self.assertLess(
            r["score"], 0.1,
            f"Expected low score for non-clone pair, got {r['score']}",
        )


# ---------------------------------------------------------------------------
# Simhash fallback — exercised even when TLSH is installed
# ---------------------------------------------------------------------------

class TestSimhashFingerprint(unittest.TestCase):

    def test_simhash_deterministic(self):
        opcodes = tuple(range(20))
        self.assertEqual(
            _simhash_fingerprint(opcodes),
            _simhash_fingerprint(opcodes),
        )

    def test_simhash_prefix(self):
        fp = _simhash_fingerprint((0x12, 0x6e, 0x0e, 0x22, 0x70))
        self.assertTrue(fp.startswith("S:"))

    def test_simhash_short_body_fallbacks_to_blake(self):
        """Bodies shorter than the n-gram window fall back to BLAKE2b."""
        fp = _simhash_fingerprint((0x12,))
        self.assertTrue(fp.startswith("B:"))


# ---------------------------------------------------------------------------
# TLSH availability probe (pure informational test)
# ---------------------------------------------------------------------------

class TestTlshAvailability(unittest.TestCase):

    def test_tlsh_available_flag_is_bool(self):
        self.assertIsInstance(_TLSH_AVAILABLE, bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)

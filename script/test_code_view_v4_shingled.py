#!/usr/bin/env python3
"""Tests for EXEC-082.1: code_view_v4_shingled — shingled opcode fingerprint.

Run from project root or script/ directory:
  python3 -m unittest script.test_code_view_v4_shingled -v
  python3 -m pytest script/test_code_view_v4_shingled.py -v
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
    import script.code_view_v4_shingled as code_v4_shingled
    from script.code_view_v4_shingled import (
        DEFAULT_SHINGLE_SIZE,
        MODE,
        compare_code_v4_shingled,
        extract_code_view_v4_shingled,
        shingle_opcodes,
        shingled_method_fingerprint,
    )
    from script.code_view_v4 import extract_code_view_v4
except ImportError:
    import code_view_v4_shingled  # type: ignore[no-redef]
    from code_view_v4_shingled import (  # type: ignore[no-redef]
        DEFAULT_SHINGLE_SIZE,
        MODE,
        compare_code_v4_shingled,
        extract_code_view_v4_shingled,
        shingle_opcodes,
        shingled_method_fingerprint,
    )
    from code_view_v4 import extract_code_view_v4  # type: ignore[no-redef]


_APK_DIR = _PROJECT_ROOT / "apk"
APK_NON_OPTIMIZED = _APK_DIR / "simple_app" / "simple_app-releaseNonOptimized.apk"


def _require_apk(path: Path) -> Path:
    if not path.exists():
        raise unittest.SkipTest(f"Test APK not found: {path}")
    return path


def _feature_dict(pairs: dict[str, str]) -> dict:
    return {
        "method_fingerprints": dict(pairs),
        "total_methods": len(pairs),
        "mode": MODE,
    }


# ---------------------------------------------------------------------------
# shingle_opcodes
# ---------------------------------------------------------------------------

class TestShingleOpcodes(unittest.TestCase):
    """Unit tests for shingle_opcodes()."""

    def test_length_5_window_3_yields_3_shingles(self):
        """Sequence of length 5 with shingle_size=3 → exactly 3 shingles."""
        shingles = shingle_opcodes([1, 2, 3, 4, 5], shingle_size=3)
        self.assertEqual(len(shingles), 3)

    def test_too_short_returns_empty_set(self):
        """Sequence shorter than shingle_size → empty set (no exception)."""
        shingles = shingle_opcodes([1, 2], shingle_size=3)
        self.assertEqual(shingles, set())

    def test_returns_set_of_bytes(self):
        """Result is set[bytes] with deterministic byte encoding."""
        shingles = shingle_opcodes([1, 2, 3, 4], shingle_size=2)
        self.assertIsInstance(shingles, set)
        for sh in shingles:
            self.assertIsInstance(sh, bytes)
            self.assertEqual(len(sh), 2)
        # Expected shingles: (1,2), (2,3), (3,4) — encoded as bytes.
        self.assertIn(b"\x01\x02", shingles)
        self.assertIn(b"\x02\x03", shingles)
        self.assertIn(b"\x03\x04", shingles)

    def test_identical_sequences_yield_equal_sets(self):
        """Same opcode lists produce equal shingle sets."""
        seq = [0x12, 0x6e, 0x0e, 0x22, 0x70, 0x54, 0x71]
        self.assertEqual(
            shingle_opcodes(seq, shingle_size=4),
            shingle_opcodes(list(seq), shingle_size=4),
        )

    def test_single_middle_edit_preserves_most_shingles(self):
        """A single-opcode edit in the middle of a long sequence keeps
        at least 80% of the shingles unchanged — this is the whole point
        of shingling over raw-sequence hashing."""
        original = list(range(40))  # 40 opcodes -> 37 shingles at size=4
        mutated = list(original)
        mutated[20] = 0xff  # flip one opcode in the middle
        a = shingle_opcodes(original, shingle_size=4)
        b = shingle_opcodes(mutated, shingle_size=4)
        preserved = len(a & b) / len(a)
        self.assertGreaterEqual(
            preserved, 0.80,
            f"Expected ≥80% shingles preserved, got {preserved:.2%}",
        )

    def test_non_positive_shingle_size_raises(self):
        """shingle_size<=0 is a programming error, surface it."""
        with self.assertRaises(ValueError):
            shingle_opcodes([1, 2, 3], shingle_size=0)


# ---------------------------------------------------------------------------
# shingled_method_fingerprint
# ---------------------------------------------------------------------------

class TestShingledMethodFingerprint(unittest.TestCase):

    def test_deterministic(self):
        """Repeated calls with the same opcodes yield the same fingerprint."""
        opcodes = [0x12, 0x6e, 0x0e, 0x22, 0x70, 0x54, 0x71, 0x0f, 0x1a, 0x38]
        self.assertEqual(
            shingled_method_fingerprint(opcodes),
            shingled_method_fingerprint(list(opcodes)),
        )

    def test_short_body_fingerprint_prefix_blake(self):
        """Method shorter than the shingle window -> BLAKE2b fallback ('B:')."""
        fp = shingled_method_fingerprint([0x12], shingle_size=4)
        self.assertTrue(fp.startswith("B:"), f"got {fp!r}")

    def test_prefix_is_one_of_known(self):
        """Any non-empty output must carry a known backend prefix."""
        fp = shingled_method_fingerprint(list(range(30)))
        self.assertTrue(fp.startswith(("T:", "S:", "B:")), f"got {fp!r}")


# ---------------------------------------------------------------------------
# extract_code_view_v4_shingled
# ---------------------------------------------------------------------------

class TestExtractShingled(unittest.TestCase):

    def test_missing_apk_returns_none(self):
        self.assertIsNone(
            extract_code_view_v4_shingled(Path("/tmp/does_not_exist_8127.apk"))
        )

    def test_real_apk_has_methods(self):
        """Real APK -> total_methods>=1 and non-empty method_fingerprints."""
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4_shingled(apk)
        self.assertIsNotNone(features)
        self.assertGreaterEqual(features["total_methods"], 1)
        self.assertGreater(len(features["method_fingerprints"]), 0)

    def test_mode_is_v4_shingled(self):
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4_shingled(apk)
        self.assertEqual(features["mode"], "v4_shingled")
        self.assertEqual(MODE, "v4_shingled")

    def test_default_shingle_size_is_4(self):
        """The documented default must match EXEC-082.1 artefact spec."""
        self.assertEqual(DEFAULT_SHINGLE_SIZE, 4)


# ---------------------------------------------------------------------------
# compare_code_v4_shingled
# ---------------------------------------------------------------------------

class TestCompareShingled(unittest.TestCase):

    def test_identical_features_score_1(self):
        pairs = {
            "Lcom/a;->one()V": "S:1111111111111111",
            "Lcom/a;->two()V": "S:2222222222222222",
        }
        fa = _feature_dict(pairs)
        fb = _feature_dict(pairs)
        r = compare_code_v4_shingled(fa, fb)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["matched_methods"], 2)
        self.assertEqual(r["union_methods"], 2)
        self.assertEqual(r["status"], "fuzzy_ok")

    def test_disjoint_features_score_0(self):
        fa = _feature_dict({"Lcom/a;->one()V": "S:aaaa"})
        fb = _feature_dict({"Lcom/b;->two()V": "S:bbbb"})
        r = compare_code_v4_shingled(fa, fb)
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["matched_methods"], 0)
        self.assertEqual(r["union_methods"], 2)
        self.assertEqual(r["status"], "fuzzy_ok")

    def test_self_compare_on_real_apk(self):
        apk = _require_apk(APK_NON_OPTIMIZED)
        features = extract_code_view_v4_shingled(apk)
        r = compare_code_v4_shingled(features, features)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["matched_methods"], features["total_methods"])

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

        r = compare_code_v4_shingled(fa, fb)

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

        r = compare_code_v4_shingled(fa, fb)

        self.assertEqual(r["score"], 0.0)


def test_shingled_one_opcode_mutation_keeps_high_simhash_similarity(monkeypatch):
    monkeypatch.setattr(code_v4_shingled, "_TLSH_AVAILABLE", False)
    monkeypatch.setattr(code_v4_shingled, "_tlsh_module", None)
    opcodes_a = [(i % 251) for i in range(240)]
    opcodes_b = list(opcodes_a)
    opcodes_b[120] = 0xFF
    fa = _feature_dict({
        "Lcom/a;->mutated()V": shingled_method_fingerprint(opcodes_a)
    })
    fb = _feature_dict({
        "Lcom/a;->mutated()V": shingled_method_fingerprint(opcodes_b)
    })

    r = compare_code_v4_shingled(fa, fb)

    assert r["score"] > 0.9


def test_shingled_tlsh_diff_backend_scores_near_match(monkeypatch):
    class FakeTlsh:
        @staticmethod
        def diff(_left: str, _right: str) -> int:
            return 9

    monkeypatch.setattr(code_v4_shingled, "_TLSH_AVAILABLE", True)
    monkeypatch.setattr(code_v4_shingled, "_tlsh_module", FakeTlsh)
    fa = _feature_dict({"Lcom/a;->one()V": "T:T1ABC"})
    fb = _feature_dict({"Lcom/a;->one()V": "T:T2DEF"})

    r = compare_code_v4_shingled(fa, fb)

    assert r["score"] == 0.97


def test_shingled_no_tlsh_simhash_backend_scores_near_match(monkeypatch):
    monkeypatch.setattr(code_v4_shingled, "_TLSH_AVAILABLE", False)
    monkeypatch.setattr(code_v4_shingled, "_tlsh_module", None)
    fa = _feature_dict({"Lcom/a;->one()V": "S:0000000000000000"})
    fb = _feature_dict({"Lcom/a;->one()V": "S:0000000000000007"})

    r = compare_code_v4_shingled(fa, fb)

    assert r["score"] == 0.953125


# ---------------------------------------------------------------------------
# Integration: shingled fp differs from non-shingled fp
# ---------------------------------------------------------------------------

class TestDiffersFromNonShingled(unittest.TestCase):
    """The whole point of EXEC-082.1 is a *different* fingerprint family.

    If the two views produced identical digests there would be nothing to
    wire up downstream and no value in the extra view.
    """

    def test_at_least_one_method_has_different_fp(self):
        apk = _require_apk(APK_NON_OPTIMIZED)
        f_v4 = extract_code_view_v4(apk)
        f_sh = extract_code_view_v4_shingled(apk)
        self.assertIsNotNone(f_v4)
        self.assertIsNotNone(f_sh)
        common = set(f_v4["method_fingerprints"]) & set(
            f_sh["method_fingerprints"]
        )
        self.assertGreater(len(common), 0, "No common method ids — APK mismatch?")
        diffs = [
            m for m in common
            if f_v4["method_fingerprints"][m] != f_sh["method_fingerprints"][m]
        ]
        self.assertGreater(
            len(diffs), 0,
            "Shingled fingerprint coincided with raw-sequence fingerprint "
            "for every method — the two views would be redundant.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

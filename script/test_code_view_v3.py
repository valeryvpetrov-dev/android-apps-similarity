#!/usr/bin/env python3
"""
Tests for R_code v3: method-level opcode multiset similarity.

Tests are split into two categories:
1. Logic tests — pure Python, no androguard required (mock frozensets/dicts)
2. Integration tests — require androguard + real APK files

When androguard is unavailable, integration tests are skipped with SKIP marker.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

# Ensure script dir is importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from code_view_v3 import (
    compare_code_v3,
    compare_code_v3_weighted,
    extract_method_opcode_fingerprint,
    extract_method_opcode_multiset,
    _ANDROGUARD_AVAILABLE,
)

APK_ROOT = SCRIPT_DIR.parent / "apk" / "simple_app"
APK_NONOPT = APK_ROOT / "simple_app-releaseNonOptimized.apk"
APK_RENAME = APK_ROOT / "simple_app-releaseRename.apk"
APK_OPT = APK_ROOT / "simple_app-releaseOptimized.apk"
APK_SNAKE = SCRIPT_DIR.parent / "apk" / "snake" / "snake.apk"

FDROID_ROOT = SCRIPT_DIR.parent / "experiments" / "datasets" / "fdroid-corpus-v1-apks"

requires_androguard = pytest.mark.skipif(
    not _ANDROGUARD_AVAILABLE,
    reason="androguard not installed"
)

# ---------------------------------------------------------------------------
# 1. Logic tests (pure Python, mock frozensets)
# ---------------------------------------------------------------------------

class TestCompareCodeV3Logic:

    def test_none_none_returns_score_1(self):
        """Both None → identical (both absent)."""
        result = compare_code_v3(None, None)
        assert result["score"] == 1.0
        assert result["status"] == "both_empty"

    def test_one_none_returns_score_0(self):
        """One None, one non-None → 0 (one APK has no code)."""
        fp = frozenset([("const/4", "return-void")])
        result = compare_code_v3(fp, None)
        assert result["score"] == 0.0
        assert result["status"] == "one_empty"

        result2 = compare_code_v3(None, fp)
        assert result2["score"] == 0.0

    def test_empty_set_both_returns_score_1(self):
        """Both empty frozensets → treat as both_empty → score 1.0."""
        result = compare_code_v3(frozenset(), frozenset())
        assert result["score"] == 1.0
        assert result["status"] == "both_empty"

    def test_identical_sets_score_1(self):
        """Identical non-empty sets → Jaccard = 1.0."""
        fp = frozenset([
            ("const/4", "iput", "return-void"),
            ("invoke-virtual", "move-result", "return-object"),
        ])
        result = compare_code_v3(fp, fp)
        assert result["score"] == 1.0
        assert result["status"] == "jaccard_ok"

    def test_disjoint_sets_score_0(self):
        """Completely disjoint sets → Jaccard = 0.0."""
        fp_a = frozenset([("const/4", "return-void")])
        fp_b = frozenset([("invoke-virtual", "move-result")])
        result = compare_code_v3(fp_a, fp_b)
        assert result["score"] == 0.0
        assert result["status"] == "jaccard_ok"

    def test_partial_overlap(self):
        """Partial overlap: |intersection|=1, |union|=3 → 1/3."""
        fp_a = frozenset([("a",), ("b",)])
        fp_b = frozenset([("b",), ("c",)])
        result = compare_code_v3(fp_a, fp_b)
        assert abs(result["score"] - 1/3) < 1e-6
        assert result["status"] == "jaccard_ok"

    def test_status_field_present(self):
        """Result always contains 'score' and 'status'."""
        fp = frozenset([("const/4",)])
        result = compare_code_v3(fp, fp)
        assert "score" in result
        assert "status" in result

    def test_score_in_range(self):
        """Score is always in [0, 1]."""
        fp_a = frozenset([("a",), ("b",), ("c",)])
        fp_b = frozenset([("b",), ("c",), ("d",), ("e",)])
        result = compare_code_v3(fp_a, fp_b)
        assert 0.0 <= result["score"] <= 1.0


class TestCompareCodeV3Weighted:

    def test_none_none(self):
        result = compare_code_v3_weighted(None, None)
        assert result["score"] == 1.0

    def test_one_none(self):
        ms = {("const/4", "return-void"): 2}
        result = compare_code_v3_weighted(ms, None)
        assert result["score"] == 0.0

    def test_identical_multisets(self):
        ms = {("const/4",): 3, ("invoke-virtual",): 1}
        result = compare_code_v3_weighted(ms, ms)
        assert result["score"] == 1.0
        assert result["status"] == "weighted_jaccard_ok"

    def test_weighted_vs_set_jaccard_differ(self):
        """Weighted Jaccard should differ from set Jaccard when counts vary."""
        ms_a = {("a",): 5, ("b",): 1}
        ms_b = {("a",): 1, ("b",): 5}
        fp_a = frozenset(ms_a.keys())
        fp_b = frozenset(ms_b.keys())

        set_result = compare_code_v3(fp_a, fp_b)
        weighted_result = compare_code_v3_weighted(ms_a, ms_b)

        # Set Jaccard = 1.0 (same keys), weighted < 1.0 (different counts)
        assert set_result["score"] == 1.0
        assert weighted_result["score"] < 1.0


# ---------------------------------------------------------------------------
# 2. Integration tests (require androguard + real APKs)
# ---------------------------------------------------------------------------

@requires_androguard
class TestCodeViewV3Integration:

    def test_same_apk_score_1(self):
        """Same APK compared with itself → score = 1.0."""
        assert APK_NONOPT.exists(), f"APK not found: {APK_NONOPT}"
        fp = extract_method_opcode_fingerprint(APK_NONOPT)
        assert fp is not None
        result = compare_code_v3(fp, fp)
        assert result["score"] == 1.0

    def test_rename_clone_high_score(self):
        """NonOptimized vs Rename clone → high score (opcodes survive rename)."""
        assert APK_NONOPT.exists() and APK_RENAME.exists()
        fp_a = extract_method_opcode_fingerprint(APK_NONOPT)
        fp_b = extract_method_opcode_fingerprint(APK_RENAME)
        assert fp_a is not None
        assert fp_b is not None
        result = compare_code_v3(fp_a, fp_b)
        # Rename should not change opcodes → high Jaccard
        assert result["score"] > 0.5, f"Expected high score for rename clone, got {result['score']}"

    def test_non_clone_low_score(self):
        """simple_app vs snake → low similarity."""
        assert APK_NONOPT.exists() and APK_SNAKE.exists()
        fp_a = extract_method_opcode_fingerprint(APK_NONOPT)
        fp_b = extract_method_opcode_fingerprint(APK_SNAKE)
        # At least one should extract ok
        result = compare_code_v3(fp_a, fp_b)
        # Unrelated apps should have low score
        assert result["score"] < 0.5, f"Expected low score for non-clone pair, got {result['score']}"

    def test_fingerprint_is_frozenset(self):
        """extract_method_opcode_fingerprint returns frozenset."""
        assert APK_NONOPT.exists()
        fp = extract_method_opcode_fingerprint(APK_NONOPT)
        assert fp is not None
        assert isinstance(fp, frozenset)

    def test_fingerprint_nonempty(self):
        """Real APK should yield non-empty fingerprint."""
        assert APK_NONOPT.exists()
        fp = extract_method_opcode_fingerprint(APK_NONOPT)
        assert fp is not None
        assert len(fp) > 0

    def test_multiset_is_dict(self):
        """extract_method_opcode_multiset returns dict."""
        assert APK_NONOPT.exists()
        ms = extract_method_opcode_multiset(APK_NONOPT)
        assert ms is not None
        assert isinstance(ms, dict)
        assert len(ms) > 0

    def test_multiset_counts_positive(self):
        """All counts in multiset are positive integers."""
        assert APK_NONOPT.exists()
        ms = extract_method_opcode_multiset(APK_NONOPT)
        assert ms is not None
        for key, count in ms.items():
            assert isinstance(count, int)
            assert count >= 1

    def test_missing_apk_returns_none(self):
        """Nonexistent APK path → None, not exception."""
        fp = extract_method_opcode_fingerprint(Path("/nonexistent/path.apk"))
        assert fp is None


# ---------------------------------------------------------------------------
# 3. F-Droid anomalous pairs (optional — skipped if files absent)
# ---------------------------------------------------------------------------

def _find_fdroid_apk(patterns: list[str]) -> Path | None:
    """Search for APK matching any of the given glob patterns."""
    if not FDROID_ROOT.exists():
        return None
    for pattern in patterns:
        matches = sorted(FDROID_ROOT.glob(pattern))
        if matches:
            return matches[0]
    return None


@pytest.mark.skipif(
    not _ANDROGUARD_AVAILABLE,
    reason="androguard not installed"
)
class TestFDroidAnomalies:

    def test_redmoon_structure_change(self):
        """redmoon 38 vs 39: structure_change — v3 should score > 0.3."""
        apk_a = _find_fdroid_apk(["*redmoon*38*", "*com.jmstudios.redmoon_38*"])
        apk_b = _find_fdroid_apk(["*redmoon*39*", "*com.jmstudios.redmoon_39*"])
        if apk_a is None or apk_b is None:
            pytest.skip(f"redmoon APKs not found in {FDROID_ROOT}")
        fp_a = extract_method_opcode_fingerprint(apk_a)
        fp_b = extract_method_opcode_fingerprint(apk_b)
        result = compare_code_v3(fp_a, fp_b)
        assert result["score"] > 0.3, (
            f"redmoon 38 vs 39: expected > 0.3, got {result['score']} "
            f"(improvement vs v2≈0.02)"
        )

    def test_fantastischmemo_multidex(self):
        """fantastischmemo 223 vs 237: single→multi-dex — v3 should score > 0.3."""
        apk_a = _find_fdroid_apk(["*anki*223*", "*flashcards*223*", "*fantastischmemo*223*"])
        apk_b = _find_fdroid_apk(["*anki*237*", "*flashcards*237*", "*fantastischmemo*237*"])
        if apk_a is None or apk_b is None:
            pytest.skip(f"fantastischmemo APKs not found in {FDROID_ROOT}")
        fp_a = extract_method_opcode_fingerprint(apk_a)
        fp_b = extract_method_opcode_fingerprint(apk_b)
        result = compare_code_v3(fp_a, fp_b)
        assert result["score"] > 0.3, (
            f"fantastischmemo 223 vs 237: expected > 0.3, got {result['score']} "
            f"(improvement vs v2≈0.06)"
        )

    def test_ipcam_kotlin_rewrite(self):
        """ipcam 241 vs 322: kotlin_rewrite — score may remain low (language change)."""
        apk_a = _find_fdroid_apk(["*ip_webcam*241*", "*ipcam*241*", "*webcam*241*"])
        apk_b = _find_fdroid_apk(["*ip_webcam*322*", "*ipcam*322*", "*webcam*322*"])
        if apk_a is None or apk_b is None:
            pytest.skip(f"ipcam APKs not found in {FDROID_ROOT}")
        fp_a = extract_method_opcode_fingerprint(apk_a)
        fp_b = extract_method_opcode_fingerprint(apk_b)
        result = compare_code_v3(fp_a, fp_b)
        # kotlin_rewrite generates very different bytecode → expected low score
        # just verify it runs and returns a valid score
        assert 0.0 <= result["score"] <= 1.0
        assert "status" in result


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

#!/usr/bin/env python3
"""Tests for script/minhash_lsh.py (EXEC-084)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from minhash_lsh import (
    LSHIndex,
    MinHashSignature,
    build_candidate_index,
    query_candidate_index,
)


def _exact_jaccard(left: set[str], right: set[str]) -> float:
    union_size = len(left | right)
    if union_size == 0:
        return 0.0
    return len(left & right) / union_size


class TestMinHashSignatureDeterminism(unittest.TestCase):
    def test_from_features_is_deterministic_with_same_seed(self) -> None:
        features = {"code:foo", "code:bar", "resource:baz", "metadata:package_name:x"}
        sig_first = MinHashSignature.from_features(features, num_perm=64, seed=123)
        sig_second = MinHashSignature.from_features(features, num_perm=64, seed=123)
        self.assertEqual(sig_first.slots, sig_second.slots)

    def test_different_seeds_yield_different_signatures(self) -> None:
        features = {"code:foo", "code:bar", "resource:baz"}
        sig_first = MinHashSignature.from_features(features, num_perm=64, seed=1)
        sig_second = MinHashSignature.from_features(features, num_perm=64, seed=2)
        self.assertNotEqual(sig_first.slots, sig_second.slots)


class TestMinHashJaccardApproximation(unittest.TestCase):
    def test_jaccard_approximates_exact_within_tolerance(self) -> None:
        # Two moderately overlapping sets (overlap 20 of 40).
        left = {"feat_{}".format(i) for i in range(40)}
        right = {"feat_{}".format(i) for i in range(20, 60)}
        exact = _exact_jaccard(left, right)
        sig_left = MinHashSignature.from_features(left, num_perm=256, seed=42)
        sig_right = MinHashSignature.from_features(right, num_perm=256, seed=42)
        approx = sig_left.jaccard(sig_right)
        self.assertAlmostEqual(exact, approx, delta=0.2)

    def test_identical_sets_have_jaccard_one(self) -> None:
        features = {"a", "b", "c", "d", "e", "f"}
        sig_a = MinHashSignature.from_features(features, num_perm=128, seed=42)
        sig_b = MinHashSignature.from_features(features, num_perm=128, seed=42)
        self.assertEqual(sig_a.jaccard(sig_b), 1.0)


class TestLSHIndexQuery(unittest.TestCase):
    def test_query_returns_key_for_identical_set(self) -> None:
        features = {"x", "y", "z", "w", "q", "r"}
        signature = MinHashSignature.from_features(features, num_perm=128, seed=42)
        index = LSHIndex(num_perm=128, bands=32)
        index.add("APP-IDENT", signature)

        query_sig = MinHashSignature.from_features(features, num_perm=128, seed=42)
        result = index.query(query_sig)
        self.assertIn("APP-IDENT", result)

    def test_query_does_not_return_key_for_disjoint_sets(self) -> None:
        left = {"alpha_{}".format(i) for i in range(50)}
        right = {"omega_{}".format(i) for i in range(50)}
        sig_left = MinHashSignature.from_features(left, num_perm=128, seed=42)
        sig_right = MinHashSignature.from_features(right, num_perm=128, seed=42)
        index = LSHIndex(num_perm=128, bands=32)
        index.add("APP-LEFT", sig_left)

        result = index.query(sig_right)
        self.assertNotIn("APP-LEFT", result)

    def test_query_is_symmetric_for_identical_keys(self) -> None:
        features_a = {"x", "y", "z"}
        features_b = {"x", "y", "z"}
        sig_a = MinHashSignature.from_features(features_a, num_perm=128, seed=42)
        sig_b = MinHashSignature.from_features(features_b, num_perm=128, seed=42)
        index = LSHIndex(num_perm=128, bands=32)
        index.add("A", sig_a)
        index.add("B", sig_b)

        result_a = index.query(sig_a)
        result_b = index.query(sig_b)
        self.assertEqual(result_a, result_b)
        self.assertIn("A", result_a)
        self.assertIn("B", result_a)


class TestBuildAndQueryCandidateIndex(unittest.TestCase):
    def test_build_candidate_index_returns_lsh_index_with_all_keys(self) -> None:
        corpus = {
            "APP-1": {"a", "b", "c"},
            "APP-2": {"a", "b", "d"},
            "APP-3": {"x", "y", "z"},
        }
        index = build_candidate_index(corpus, num_perm=64, bands=32, seed=42)
        self.assertIsInstance(index, LSHIndex)
        self.assertEqual(len(index), 3)
        self.assertEqual(sorted(index.keys), ["APP-1", "APP-2", "APP-3"])

    def test_query_candidate_index_returns_self_for_identical_query(self) -> None:
        corpus = {
            "APP-1": {"a", "b", "c"},
            "APP-2": {"x", "y", "z"},
            "APP-3": {"p", "q", "r"},
        }
        index = build_candidate_index(corpus, num_perm=64, bands=32, seed=42)
        candidates = query_candidate_index(
            index, corpus["APP-1"], num_perm=64, seed=42
        )
        self.assertIn("APP-1", candidates)


class TestLSHIndexErrors(unittest.TestCase):
    def test_add_duplicate_key_raises(self) -> None:
        sig = MinHashSignature.from_features({"x"}, num_perm=64, seed=42)
        index = LSHIndex(num_perm=64, bands=32)
        index.add("A", sig)
        with self.assertRaises(ValueError):
            index.add("A", sig)

    def test_bands_must_divide_num_perm(self) -> None:
        with self.assertRaises(ValueError):
            LSHIndex(num_perm=128, bands=30)

    def test_query_with_mismatched_num_perm_raises(self) -> None:
        sig_index = MinHashSignature.from_features({"x"}, num_perm=64, seed=42)
        index = LSHIndex(num_perm=64, bands=32)
        index.add("A", sig_index)
        bad_sig = MinHashSignature.from_features({"x"}, num_perm=128, seed=42)
        with self.assertRaises(ValueError):
            index.query(bad_sig)


if __name__ == "__main__":
    unittest.main()

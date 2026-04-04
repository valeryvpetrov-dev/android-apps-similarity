#!/usr/bin/env python3
"""Tests for library_view.py (BOR-004).

Stdlib-only: uses unittest and tempfile to build synthetic smali trees.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from library_view import (
    LIBRARY_CATALOG,
    _build_prefix_index,
    _match_prefix,
    _package_from_smali_rel,
    _serialize_features,
    catalog_stats,
    compare_libraries,
    extract_library_features,
    library_explanation_hints,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_smali_file(base: Path, smali_root: str, package_parts: list, class_name: str) -> Path:
    """Create a minimal .smali file inside a synthetic unpacked APK."""
    dir_path = base / smali_root
    for part in package_parts:
        dir_path = dir_path / part
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / class_name
    file_path.write_text(
        textwrap.dedent("""\
            .class public L{pkg}/{cls};
            .super Ljava/lang/Object;
        """.format(pkg="/".join(package_parts), cls=class_name.replace(".smali", ""))),
        encoding="utf-8",
    )
    return file_path


def _build_synthetic_apk(spec: dict) -> str:
    """Build a temp dir with smali files according to spec.

    spec: {dotted_package: class_count}
    Returns path to the temp dir (caller should clean up via tempfile).
    """
    tmp = tempfile.mkdtemp(prefix="libview_test_")
    for dotted_pkg, count in spec.items():
        parts = dotted_pkg.split(".")
        for i in range(count):
            _make_smali_file(
                Path(tmp), "smali", parts, "Class{}.smali".format(i)
            )
    return tmp


# ---------------------------------------------------------------------------
# Tests: catalog integrity
# ---------------------------------------------------------------------------

class TestCatalogIntegrity(unittest.TestCase):

    def test_catalog_has_at_least_150_unique_prefixes(self):
        stats = catalog_stats()
        self.assertGreaterEqual(
            stats["total_unique_prefixes"], 150,
            "Catalog should contain >= 150 unique prefixes, got {}".format(
                stats["total_unique_prefixes"]
            ),
        )

    def test_all_categories_have_description(self):
        for cat, meta in LIBRARY_CATALOG.items():
            self.assertIn("description", meta, "Category {} missing description".format(cat))
            self.assertIn("prefixes", meta, "Category {} missing prefixes".format(cat))

    def test_prefix_index_sorted_longest_first(self):
        index = _build_prefix_index()
        lengths = [len(entry[0]) for entry in index]
        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_no_empty_prefix_lists(self):
        for cat, meta in LIBRARY_CATALOG.items():
            self.assertTrue(
                len(meta["prefixes"]) > 0,
                "Category {} has empty prefix list".format(cat),
            )


# ---------------------------------------------------------------------------
# Tests: prefix matching
# ---------------------------------------------------------------------------

class TestPrefixMatching(unittest.TestCase):

    def test_exact_prefix_matches(self):
        result = _match_prefix("okhttp3")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "okhttp3")
        self.assertEqual(result[2], "networking")

    def test_subpackage_matches(self):
        result = _match_prefix("com.google.gson.internal")
        self.assertIsNotNone(result)
        # Should match the more specific com.google.gson rather than com.google
        self.assertEqual(result[1], "com.google.gson")

    def test_no_match_for_app_package(self):
        result = _match_prefix("com.mycompany.myapp.feature")
        self.assertIsNone(result)

    def test_longer_prefix_wins(self):
        # com.google.firebase should win over com.google
        result = _match_prefix("com.google.firebase.auth")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "com.google.firebase")

    def test_android_platform(self):
        result = _match_prefix("androidx.core.app")
        self.assertIsNotNone(result)
        self.assertEqual(result[2], "android_platform")

    def test_kotlin_platform(self):
        result = _match_prefix("kotlin.collections")
        self.assertIsNotNone(result)
        self.assertEqual(result[2], "kotlin_platform")


# ---------------------------------------------------------------------------
# Tests: package extraction from smali paths
# ---------------------------------------------------------------------------

class TestPackageExtraction(unittest.TestCase):

    def test_standard_smali_path(self):
        parts = ["smali", "com", "google", "gson", "Gson.smali"]
        self.assertEqual(_package_from_smali_rel(parts), "com.google.gson")

    def test_smali_classes2_path(self):
        parts = ["smali_classes2", "okhttp3", "internal", "Cache.smali"]
        self.assertEqual(_package_from_smali_rel(parts), "okhttp3.internal")

    def test_too_short_path(self):
        parts = ["smali", "Foo.smali"]
        self.assertIsNone(_package_from_smali_rel(parts))

    def test_non_smali_root(self):
        parts = ["res", "layout", "activity_main.xml"]
        self.assertIsNone(_package_from_smali_rel(parts))


# ---------------------------------------------------------------------------
# Tests: feature extraction on synthetic APK
# ---------------------------------------------------------------------------

class TestFeatureExtraction(unittest.TestCase):

    def setUp(self):
        self.apk_dir = _build_synthetic_apk({
            "com.google.gson": 5,
            "okhttp3.internal": 3,
            "com.myapp.feature": 10,
            "com.myapp.util": 2,
        })

    def tearDown(self):
        import shutil
        shutil.rmtree(self.apk_dir, ignore_errors=True)

    def test_total_classes(self):
        features = extract_library_features(self.apk_dir)
        self.assertEqual(features["total_classes"], 20)

    def test_library_detected(self):
        features = extract_library_features(self.apk_dir)
        self.assertIn("com.google.gson", features["libraries"])
        self.assertIn("okhttp3", features["libraries"])

    def test_library_class_counts(self):
        features = extract_library_features(self.apk_dir)
        self.assertEqual(features["libraries"]["com.google.gson"]["class_count"], 5)
        self.assertEqual(features["libraries"]["okhttp3"]["class_count"], 3)

    def test_library_categories(self):
        features = extract_library_features(self.apk_dir)
        self.assertEqual(features["libraries"]["com.google.gson"]["category"], "serialization")
        self.assertEqual(features["libraries"]["okhttp3"]["category"], "networking")

    def test_app_packages_detected(self):
        features = extract_library_features(self.apk_dir)
        self.assertIn("com.myapp.feature", features["app_packages"])
        self.assertIn("com.myapp.util", features["app_packages"])

    def test_library_ratio(self):
        features = extract_library_features(self.apk_dir)
        # 8 library classes out of 20 total
        self.assertAlmostEqual(features["library_ratio"], 8 / 20, places=4)

    def test_nonexistent_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            extract_library_features("/tmp/nonexistent_apk_dir_xyz_12345")


# ---------------------------------------------------------------------------
# Tests: comparison
# ---------------------------------------------------------------------------

class TestComparison(unittest.TestCase):

    def setUp(self):
        self.apk_a_dir = _build_synthetic_apk({
            "com.google.gson": 5,
            "okhttp3.internal": 3,
            "retrofit2": 2,
            "com.myapp.core": 10,
        })
        self.apk_b_dir = _build_synthetic_apk({
            "com.google.gson": 8,
            "okhttp3.internal": 3,
            "com.squareup.picasso": 4,
            "com.otherapp.ui": 12,
        })

    def tearDown(self):
        import shutil
        shutil.rmtree(self.apk_a_dir, ignore_errors=True)
        shutil.rmtree(self.apk_b_dir, ignore_errors=True)

    def test_jaccard_range(self):
        fa = extract_library_features(self.apk_a_dir)
        fb = extract_library_features(self.apk_b_dir)
        comp = compare_libraries(fa, fb)
        self.assertGreaterEqual(comp["library_jaccard_score"], 0.0)
        self.assertLessEqual(comp["library_jaccard_score"], 1.0)

    def test_shared_libraries(self):
        fa = extract_library_features(self.apk_a_dir)
        fb = extract_library_features(self.apk_b_dir)
        comp = compare_libraries(fa, fb)
        self.assertIn("com.google.gson", comp["shared"])
        self.assertIn("okhttp3", comp["shared"])

    def test_a_only_libraries(self):
        fa = extract_library_features(self.apk_a_dir)
        fb = extract_library_features(self.apk_b_dir)
        comp = compare_libraries(fa, fb)
        self.assertIn("retrofit2", comp["a_only"])

    def test_b_only_libraries(self):
        fa = extract_library_features(self.apk_a_dir)
        fb = extract_library_features(self.apk_b_dir)
        comp = compare_libraries(fa, fb)
        self.assertIn("com.squareup.picasso", comp["b_only"])

    def test_weighted_score_range(self):
        fa = extract_library_features(self.apk_a_dir)
        fb = extract_library_features(self.apk_b_dir)
        comp = compare_libraries(fa, fb)
        self.assertGreaterEqual(comp["weighted_library_score"], 0.0)
        self.assertLessEqual(comp["weighted_library_score"], 1.0)

    def test_identical_apks_give_perfect_scores(self):
        fa = extract_library_features(self.apk_a_dir)
        comp = compare_libraries(fa, fa)
        self.assertAlmostEqual(comp["library_jaccard_score"], 1.0, places=4)
        self.assertAlmostEqual(comp["weighted_library_score"], 1.0, places=4)
        self.assertEqual(comp["a_only"], [])
        self.assertEqual(comp["b_only"], [])


# ---------------------------------------------------------------------------
# Tests: empty APK edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_empty_apk(self):
        tmp = tempfile.mkdtemp(prefix="libview_empty_")
        try:
            features = extract_library_features(tmp)
            self.assertEqual(features["total_classes"], 0)
            self.assertEqual(features["library_ratio"], 0.0)
            self.assertEqual(len(features["libraries"]), 0)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_compare_two_empty_apks(self):
        tmp_a = tempfile.mkdtemp(prefix="libview_empty_a_")
        tmp_b = tempfile.mkdtemp(prefix="libview_empty_b_")
        try:
            fa = extract_library_features(tmp_a)
            fb = extract_library_features(tmp_b)
            comp = compare_libraries(fa, fb)
            self.assertAlmostEqual(comp["library_jaccard_score"], 1.0, places=4)
        finally:
            import shutil
            shutil.rmtree(tmp_a, ignore_errors=True)
            shutil.rmtree(tmp_b, ignore_errors=True)

    def test_all_library_apk(self):
        """APK that contains only library code."""
        tmp = _build_synthetic_apk({
            "com.google.gson": 10,
            "okhttp3": 5,
        })
        try:
            features = extract_library_features(tmp)
            self.assertAlmostEqual(features["library_ratio"], 1.0, places=4)
            self.assertEqual(len(features["app_packages"]), 0)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_smali_classes2_detection(self):
        """Ensure classes in smali_classes2/ are also picked up."""
        tmp = tempfile.mkdtemp(prefix="libview_multi_")
        try:
            _make_smali_file(Path(tmp), "smali", ["com", "myapp"], "A.smali")
            _make_smali_file(Path(tmp), "smali_classes2", ["okhttp3"], "B.smali")
            features = extract_library_features(tmp)
            self.assertEqual(features["total_classes"], 2)
            self.assertIn("okhttp3", features["libraries"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests: explanation hints
# ---------------------------------------------------------------------------

class TestExplanationHints(unittest.TestCase):

    def test_low_overlap_hint(self):
        comp = {
            "library_jaccard_score": 0.2,
            "weighted_library_score": 0.15,
            "shared": [],
            "a_only": ["retrofit2"],
            "b_only": ["com.squareup.picasso"],
            "library_ratio_a": 0.3,
            "library_ratio_b": 0.4,
        }
        hints = library_explanation_hints(comp)
        actions = [h["action"] for h in hints]
        self.assertIn("low_overlap", actions)

    def test_high_overlap_hint(self):
        comp = {
            "library_jaccard_score": 0.95,
            "weighted_library_score": 0.93,
            "shared": ["okhttp3", "com.google.gson"],
            "a_only": [],
            "b_only": [],
            "library_ratio_a": 0.5,
            "library_ratio_b": 0.5,
        }
        hints = library_explanation_hints(comp)
        actions = [h["action"] for h in hints]
        self.assertIn("high_overlap", actions)

    def test_library_dominated_hint(self):
        comp = {
            "library_jaccard_score": 0.8,
            "weighted_library_score": 0.75,
            "shared": ["okhttp3"],
            "a_only": [],
            "b_only": [],
            "library_ratio_a": 0.85,
            "library_ratio_b": 0.4,
        }
        hints = library_explanation_hints(comp)
        actions = [h["action"] for h in hints]
        self.assertIn("library_dominated", actions)

    def test_a_only_and_b_only_hints(self):
        comp = {
            "library_jaccard_score": 0.6,
            "weighted_library_score": 0.55,
            "shared": ["okhttp3"],
            "a_only": ["retrofit2"],
            "b_only": ["com.squareup.picasso"],
            "library_ratio_a": 0.3,
            "library_ratio_b": 0.3,
        }
        hints = library_explanation_hints(comp)
        a_only_hints = [h for h in hints if h["action"] == "a_only"]
        b_only_hints = [h for h in hints if h["action"] == "b_only"]
        self.assertEqual(len(a_only_hints), 1)
        self.assertEqual(a_only_hints[0]["library"], "retrofit2")
        self.assertEqual(len(b_only_hints), 1)
        self.assertEqual(b_only_hints[0]["library"], "com.squareup.picasso")

    def test_all_hints_have_type(self):
        comp = {
            "library_jaccard_score": 0.3,
            "weighted_library_score": 0.1,
            "shared": [],
            "a_only": ["x"],
            "b_only": ["y"],
            "library_ratio_a": 0.8,
            "library_ratio_b": 0.9,
        }
        hints = library_explanation_hints(comp)
        for h in hints:
            self.assertEqual(h["type"], "LibraryImpact")


# ---------------------------------------------------------------------------
# Tests: serialization
# ---------------------------------------------------------------------------

class TestSerialization(unittest.TestCase):

    def test_serialize_features_is_json_safe(self):
        features = {
            "libraries": {"okhttp3": {"prefix": "okhttp3", "package_count": 1, "class_count": 3, "category": "networking"}},
            "app_packages": {"com.myapp.feature", "com.myapp.util"},
            "library_ratio": 0.4,
            "total_classes": 10,
        }
        serialized = _serialize_features(features)
        # Must be JSON-serializable
        payload = json.dumps(serialized)
        self.assertIsInstance(payload, str)
        # Must have app_package_count instead of raw set
        self.assertEqual(serialized["app_package_count"], 2)
        self.assertNotIn("app_packages", serialized)


if __name__ == "__main__":
    unittest.main()

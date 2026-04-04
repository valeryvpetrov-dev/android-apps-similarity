#!/usr/bin/env python3
"""Unit tests for resource_view.py (BOR-002 RPlugin)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from resource_view import (
    compare_resources,
    extract_resource_features,
    resource_explanation_hints,
)


class TestExtractResourceFeatures(unittest.TestCase):
    """Tests for extract_resource_features."""

    def test_empty_apk_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            features = extract_resource_features(tmpdir)
            self.assertEqual(features["file_count"], 0)
            self.assertEqual(features["total_size"], 0)
            self.assertEqual(features["resource_digests"], set())

    def test_res_dir_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = Path(tmpdir) / "res" / "drawable"
            res_dir.mkdir(parents=True)
            (res_dir / "icon.png").write_bytes(b"\x89PNG_fake_icon")

            features = extract_resource_features(tmpdir)
            self.assertEqual(features["file_count"], 1)
            self.assertGreater(features["total_size"], 0)
            paths = {p for p, _ in features["resource_digests"]}
            self.assertIn("res/drawable/icon.png", paths)

    def test_assets_dir_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "assets"
            assets_dir.mkdir()
            (assets_dir / "data.json").write_text('{"key": "value"}', encoding="utf-8")

            features = extract_resource_features(tmpdir)
            self.assertEqual(features["file_count"], 1)
            paths = {p for p, _ in features["resource_digests"]}
            self.assertIn("assets/data.json", paths)

    def test_both_res_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "res" / "layout").mkdir(parents=True)
            (Path(tmpdir) / "res" / "layout" / "main.xml").write_text("<LinearLayout/>")
            (Path(tmpdir) / "assets").mkdir()
            (Path(tmpdir) / "assets" / "font.ttf").write_bytes(b"\x00\x01\x00\x00")

            features = extract_resource_features(tmpdir)
            self.assertEqual(features["file_count"], 2)

    def test_ignores_non_resource_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            smali_dir = Path(tmpdir) / "smali" / "com" / "example"
            smali_dir.mkdir(parents=True)
            (smali_dir / "Main.smali").write_text(".class public Lcom/example/Main;")
            (Path(tmpdir) / "AndroidManifest.xml").write_text("<manifest/>")

            features = extract_resource_features(tmpdir)
            self.assertEqual(features["file_count"], 0)

    def test_digest_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = Path(tmpdir) / "res" / "raw"
            res_dir.mkdir(parents=True)
            (res_dir / "data.bin").write_bytes(b"deterministic_content")

            f1 = extract_resource_features(tmpdir)
            f2 = extract_resource_features(tmpdir)
            self.assertEqual(f1["resource_digests"], f2["resource_digests"])

    def test_nonexistent_dir_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            extract_resource_features("/nonexistent/path/apk_dir")

    def test_file_instead_of_dir_raises(self) -> None:
        with tempfile.NamedTemporaryFile() as tmp:
            with self.assertRaises(NotADirectoryError):
                extract_resource_features(tmp.name)


class TestCompareResources(unittest.TestCase):
    """Tests for compare_resources."""

    def _make_features(self, digests: set) -> dict:
        return {
            "resource_digests": digests,
            "file_count": len(digests),
            "total_size": 0,
        }

    def test_identical_sets(self) -> None:
        s = {("res/drawable/icon.png", "abc123"), ("assets/data.json", "def456")}
        result = compare_resources(self._make_features(s), self._make_features(s))
        self.assertEqual(result["resource_jaccard_score"], 1.0)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["unchanged_count"], 2)

    def test_completely_different_sets(self) -> None:
        a = {("res/a.png", "aaa")}
        b = {("res/b.png", "bbb")}
        result = compare_resources(self._make_features(a), self._make_features(b))
        self.assertEqual(result["resource_jaccard_score"], 0.0)
        self.assertEqual(result["added"], ["res/b.png"])
        self.assertEqual(result["removed"], ["res/a.png"])
        self.assertEqual(result["modified"], [])

    def test_both_empty(self) -> None:
        result = compare_resources(
            self._make_features(set()), self._make_features(set())
        )
        self.assertEqual(result["resource_jaccard_score"], 1.0)
        self.assertEqual(result["unchanged_count"], 0)

    def test_modified_file(self) -> None:
        a = {("res/values/strings.xml", "digest_v1")}
        b = {("res/values/strings.xml", "digest_v2")}
        result = compare_resources(self._make_features(a), self._make_features(b))
        self.assertAlmostEqual(result["resource_jaccard_score"], 0.0)
        self.assertEqual(result["modified"], ["res/values/strings.xml"])
        self.assertEqual(result["unchanged_count"], 0)

    def test_mixed_changes(self) -> None:
        a = {
            ("res/drawable/icon.png", "same_digest"),
            ("res/layout/main.xml", "old_digest"),
            ("res/raw/removed.bin", "removed_digest"),
        }
        b = {
            ("res/drawable/icon.png", "same_digest"),
            ("res/layout/main.xml", "new_digest"),
            ("assets/added.json", "added_digest"),
        }
        result = compare_resources(self._make_features(a), self._make_features(b))

        self.assertEqual(result["unchanged_count"], 1)
        self.assertEqual(result["modified"], ["res/layout/main.xml"])
        self.assertEqual(result["added"], ["assets/added.json"])
        self.assertEqual(result["removed"], ["res/raw/removed.bin"])
        self.assertGreater(result["resource_jaccard_score"], 0.0)
        self.assertLess(result["resource_jaccard_score"], 1.0)

    def test_jaccard_value_correctness(self) -> None:
        a = {("a", "1"), ("b", "2"), ("c", "3")}
        b = {("a", "1"), ("b", "2"), ("d", "4")}
        result = compare_resources(self._make_features(a), self._make_features(b))
        # intersection = {(a,1),(b,2)} = 2, union = {(a,1),(b,2),(c,3),(d,4)} = 4
        self.assertAlmostEqual(result["resource_jaccard_score"], 2.0 / 4.0)


class TestResourceExplanationHints(unittest.TestCase):
    """Tests for resource_explanation_hints."""

    def test_empty_comparison(self) -> None:
        comparison = {
            "resource_jaccard_score": 1.0,
            "added": [],
            "removed": [],
            "modified": [],
            "unchanged_count": 5,
        }
        hints = resource_explanation_hints(comparison)
        self.assertEqual(hints, [])

    def test_all_change_types(self) -> None:
        comparison = {
            "resource_jaccard_score": 0.5,
            "added": ["assets/new.json"],
            "removed": ["res/old.png"],
            "modified": ["res/values/strings.xml"],
            "unchanged_count": 1,
        }
        hints = resource_explanation_hints(comparison)
        self.assertEqual(len(hints), 3)

        types_actions = {(h["type"], h["action"]) for h in hints}
        self.assertIn(("ResourceChange", "modified"), types_actions)
        self.assertIn(("ResourceChange", "added"), types_actions)
        self.assertIn(("ResourceChange", "removed"), types_actions)

    def test_hint_structure(self) -> None:
        comparison = {
            "added": ["assets/a.json"],
            "removed": [],
            "modified": [],
        }
        hints = resource_explanation_hints(comparison)
        self.assertEqual(len(hints), 1)
        hint = hints[0]
        self.assertIn("type", hint)
        self.assertIn("action", hint)
        self.assertIn("path", hint)
        self.assertIn("detail", hint)
        self.assertEqual(hint["path"], "assets/a.json")

    def test_hint_order_modified_first(self) -> None:
        comparison = {
            "added": ["assets/b.json"],
            "removed": ["res/c.png"],
            "modified": ["res/a.xml"],
        }
        hints = resource_explanation_hints(comparison)
        self.assertEqual(hints[0]["action"], "modified")
        self.assertEqual(hints[1]["action"], "added")
        self.assertEqual(hints[2]["action"], "removed")


class TestEndToEnd(unittest.TestCase):
    """End-to-end test: extract -> compare -> hints."""

    def test_full_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as dir_a, \
             tempfile.TemporaryDirectory() as dir_b:
            # APK A: two resource files
            (Path(dir_a) / "res" / "drawable").mkdir(parents=True)
            (Path(dir_a) / "res" / "drawable" / "icon.png").write_bytes(b"icon_v1")
            (Path(dir_a) / "res" / "drawable" / "bg.png").write_bytes(b"background")
            (Path(dir_a) / "assets").mkdir()
            (Path(dir_a) / "assets" / "config.json").write_text('{"v":1}')

            # APK B: icon changed, bg same, config removed, new font added
            (Path(dir_b) / "res" / "drawable").mkdir(parents=True)
            (Path(dir_b) / "res" / "drawable" / "icon.png").write_bytes(b"icon_v2")
            (Path(dir_b) / "res" / "drawable" / "bg.png").write_bytes(b"background")
            (Path(dir_b) / "assets").mkdir()
            (Path(dir_b) / "assets" / "font.ttf").write_bytes(b"font_data")

            fa = extract_resource_features(dir_a)
            fb = extract_resource_features(dir_b)

            self.assertEqual(fa["file_count"], 3)
            self.assertEqual(fb["file_count"], 3)

            cmp = compare_resources(fa, fb)

            self.assertEqual(cmp["unchanged_count"], 1)  # bg.png
            self.assertIn("res/drawable/icon.png", cmp["modified"])
            self.assertIn("assets/config.json", cmp["removed"])
            self.assertIn("assets/font.ttf", cmp["added"])
            self.assertGreater(cmp["resource_jaccard_score"], 0.0)
            self.assertLess(cmp["resource_jaccard_score"], 1.0)

            hints = resource_explanation_hints(cmp)
            self.assertEqual(len(hints), 3)
            actions = {h["action"] for h in hints}
            self.assertEqual(actions, {"modified", "added", "removed"})


if __name__ == "__main__":
    unittest.main()

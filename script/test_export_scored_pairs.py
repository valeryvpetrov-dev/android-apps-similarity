#!/usr/bin/env python3
"""Tests for export_scored_pairs.py."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from export_scored_pairs import (
    build_pair_id,
    export_to_csv,
    extract_label,
    extract_score,
    load_screening_results,
    validate_entry,
)


def _write_json(directory: Path, filename: str, data) -> Path:
    path = directory / filename
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class TestBuildPairId(unittest.TestCase):
    def test_uses_app_a_app_b(self):
        entry = {"app_a": "com.foo", "app_b": "com.bar"}
        self.assertEqual(build_pair_id(entry), "com.foo__com.bar")

    def test_falls_back_to_query_candidate(self):
        entry = {"query_app_id": "app1", "candidate_app_id": "app2"}
        self.assertEqual(build_pair_id(entry), "app1__app2")

    def test_raises_on_missing_ids(self):
        with self.assertRaises(ValueError):
            build_pair_id({"retrieval_score": 0.5})

    def test_raises_on_empty_app_a(self):
        with self.assertRaises(ValueError):
            build_pair_id({"app_a": "", "app_b": "com.bar"})


class TestExtractScore(unittest.TestCase):
    def test_uses_final_score(self):
        self.assertAlmostEqual(extract_score({"final_score": 0.9}), 0.9)

    def test_uses_post_api_fix_score(self):
        self.assertAlmostEqual(extract_score({"post_api_fix_score": 0.75}), 0.75)

    def test_falls_back_to_retrieval_score(self):
        self.assertAlmostEqual(extract_score({"retrieval_score": 0.42}), 0.42)

    def test_final_score_takes_priority(self):
        entry = {"final_score": 0.8, "retrieval_score": 0.3}
        self.assertAlmostEqual(extract_score(entry), 0.8)

    def test_raises_on_missing_score(self):
        with self.assertRaises(ValueError):
            extract_score({"app_a": "foo", "app_b": "bar"})

    def test_raises_on_non_numeric_score(self):
        with self.assertRaises(ValueError):
            extract_score({"retrieval_score": "not-a-number"})

    def test_accepts_integer_score(self):
        self.assertAlmostEqual(extract_score({"retrieval_score": 1}), 1.0)


class TestExtractLabel(unittest.TestCase):
    def test_returns_unknown_when_absent(self):
        self.assertEqual(extract_label({}), "unknown")

    def test_returns_unknown_for_empty_string(self):
        self.assertEqual(extract_label({"label": ""}), "unknown")

    def test_returns_provided_label(self):
        self.assertEqual(extract_label({"label": "similar"}), "similar")

    def test_strips_whitespace(self):
        self.assertEqual(extract_label({"label": "  similar  "}), "similar")


class TestLoadScreeningResults(unittest.TestCase):
    def test_loads_list_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            data = [
                {"app_a": "a", "app_b": "b", "retrieval_score": 0.5},
                {"app_a": "c", "app_b": "d", "retrieval_score": 0.7},
            ]
            _write_json(d, "results.json", data)
            entries = load_screening_results(d)
        self.assertEqual(len(entries), 2)

    def test_loads_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d, "a.json", [{"app_a": "a", "app_b": "b", "retrieval_score": 0.5}])
            _write_json(d, "b.json", [{"app_a": "c", "app_b": "d", "retrieval_score": 0.6}])
            entries = load_screening_results(d)
        self.assertEqual(len(entries), 2)

    def test_wraps_single_object_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d, "single.json", {"app_a": "x", "app_b": "y", "retrieval_score": 0.3})
            entries = load_screening_results(d)
        self.assertEqual(len(entries), 1)

    def test_raises_on_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                load_screening_results(Path(tmpdir))

    def test_ignores_non_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "notes.txt").write_text("ignore me", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                load_screening_results(d)


class TestExportToCsv(unittest.TestCase):
    def _make_entries(self):
        return [
            {"app_a": "com.foo", "app_b": "com.bar", "retrieval_score": 0.85, "label": "similar"},
            {"app_a": "com.baz", "app_b": "com.qux", "retrieval_score": 0.32},
        ]

    def test_creates_csv_with_correct_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.csv"
            export_to_csv(self._make_entries(), output)
            with output.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["pair_id"], "com.foo__com.bar")
        self.assertIn("post_api_fix_score", rows[0])
        self.assertIn("label", rows[0])

    def test_label_unknown_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.csv"
            export_to_csv(self._make_entries(), output)
            with output.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[1]["label"], "unknown")

    def test_label_preserved_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.csv"
            export_to_csv(self._make_entries(), output)
            with output.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["label"], "similar")

    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.csv"
            summary = export_to_csv(self._make_entries(), output)
        self.assertEqual(summary["total_pairs"], 2)
        self.assertEqual(summary["labeled"], 1)
        self.assertEqual(summary["unlabeled"], 1)

    def test_summary_score_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.csv"
            summary = export_to_csv(self._make_entries(), output)
        self.assertAlmostEqual(summary["score_min"], 0.32)
        self.assertAlmostEqual(summary["score_max"], 0.85)

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "nested" / "deep" / "output.csv"
            export_to_csv(self._make_entries(), output)
            self.assertTrue(output.exists())

    def test_uses_final_score_field(self):
        entries = [
            {"app_a": "a", "app_b": "b", "final_score": 0.99, "retrieval_score": 0.1},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "out.csv"
            export_to_csv(entries, output)
            with output.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertAlmostEqual(float(rows[0]["post_api_fix_score"]), 0.99)

    def test_screening_runner_output_format(self):
        """Verify compatibility with real screening_runner.py candidate_list output."""
        entries = [
            {
                "app_a": "base_app",
                "app_b": "candidate_app",
                "query_app_id": "base_app",
                "candidate_app_id": "candidate_app",
                "retrieval_score": 0.73,
                "retrieval_rank": 1,
                "features_used": ["code", "component"],
                "retrieval_features_used": ["code", "component"],
                "screening_warnings": [],
                "screening_explanation": None,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "out.csv"
            summary = export_to_csv(entries, output)
            with output.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["pair_id"], "base_app__candidate_app")
        self.assertAlmostEqual(float(rows[0]["post_api_fix_score"]), 0.73)
        self.assertEqual(rows[0]["label"], "unknown")
        self.assertEqual(summary["total_pairs"], 1)


class TestValidateEntry(unittest.TestCase):
    def test_valid_entry(self):
        entry = {"app_a": "foo", "app_b": "bar", "retrieval_score": 0.5}
        pair_id, score, label = validate_entry(entry)
        self.assertEqual(pair_id, "foo__bar")
        self.assertAlmostEqual(score, 0.5)
        self.assertEqual(label, "unknown")

    def test_raises_on_invalid_entry(self):
        with self.assertRaises(ValueError):
            validate_entry({"retrieval_score": 0.5})  # missing pair ids


if __name__ == "__main__":
    unittest.main()

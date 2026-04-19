#!/usr/bin/env python3
"""DEEP-004: export_pairwise_detailed_json — машинно-читаемый JSON-отчёт
из результатов `run_pairwise`.

Контракт (schema_version = "deep-004-v1"):
  - Top-level object: {"schema_version", "total_pairs", "generated_at", "pairs"}.
  - Каждый item в "pairs" содержит обязательные поля pair_id, app_a, app_b, status.
  - Timeout-строки сохраняют analysis_failed_reason и timeout_info.
  - Успешные строки сохраняют signature_match и evidence (если есть).
  - Любые дополнительные поля из pair_row сохраняются без потерь.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


SCHEMA_VERSION = "deep-004-v1"

ISO8601_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def _sample_success_row() -> dict:
    return {
        "app_a": "A",
        "app_b": "B",
        "full_similarity_score": 0.91,
        "library_reduced_score": 0.87,
        "status": "success",
        "views_used": ["component", "resource", "library"],
        "signature_match": {"score": 1.0, "status": "match"},
        "evidence": [
            {"kind": "per_layer", "layer": "component", "score": 0.91},
            {"kind": "signature_match", "score": 1.0, "status": "match"},
        ],
    }


def _sample_low_similarity_row() -> dict:
    return {
        "app_a": "C",
        "app_b": "D",
        "full_similarity_score": 0.12,
        "library_reduced_score": 0.05,
        "status": "low_similarity",
        "views_used": ["component"],
        "signature_match": {"score": 0.0, "status": "mismatch"},
        "evidence": [],
    }


def _sample_timeout_row() -> dict:
    return {
        "app_a": "E",
        "app_b": "F",
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "analysis_failed_reason": "budget_exceeded",
        "views_used": ["component", "resource", "library"],
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "timeout_info": {"pair_timeout_sec": 30, "stage": "pairwise"},
    }


class TestExportPairwiseDetailedJsonTopLevel(unittest.TestCase):
    """Top-level JSON: ключи pairs, total_pairs, schema_version, generated_at."""

    def test_export_creates_valid_json_with_required_top_level_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[_sample_success_row()],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("pairs", payload)
        self.assertIn("total_pairs", payload)
        self.assertIn("schema_version", payload)
        self.assertIn("generated_at", payload)
        self.assertIsInstance(payload["pairs"], list)
        self.assertIsInstance(payload["total_pairs"], int)

    def test_generated_at_is_iso8601_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[_sample_success_row()],
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertRegex(payload["generated_at"], ISO8601_UTC_PATTERN)


class TestExportPairwiseDetailedJsonItemShape(unittest.TestCase):
    """Каждый item в pairs содержит обязательные поля pair_id, app_a, app_b, status."""

    def test_each_item_contains_required_fields(self) -> None:
        results = [_sample_success_row(), _sample_low_similarity_row(), _sample_timeout_row()]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload["pairs"]), 3)
        for item in payload["pairs"]:
            self.assertIn("pair_id", item)
            self.assertIn("app_a", item)
            self.assertIn("app_b", item)
            self.assertIn("status", item)
            self.assertIsInstance(item["pair_id"], str)
            self.assertTrue(item["pair_id"].startswith("PAIR-"))

    def test_pair_id_is_sequential_six_digit_format(self) -> None:
        results = [_sample_success_row(), _sample_low_similarity_row()]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["pairs"][0]["pair_id"], "PAIR-000001")
        self.assertEqual(payload["pairs"][1]["pair_id"], "PAIR-000002")


class TestExportPairwiseDetailedJsonSchemaVersion(unittest.TestCase):
    """schema_version ровно равен "deep-004-v1"."""

    def test_schema_version_is_deep_004_v1(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[_sample_success_row()],
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        # И дублируется в каждом item.
        for item in payload["pairs"]:
            self.assertEqual(item["schema_version"], SCHEMA_VERSION)


class TestExportPairwiseDetailedJsonTimeoutRow(unittest.TestCase):
    """Timeout-строка: поля timeout_info и analysis_failed_reason сохраняются."""

    def test_timeout_row_preserves_timeout_info_and_reason(self) -> None:
        results = [_sample_timeout_row()]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        item = payload["pairs"][0]
        self.assertEqual(item["status"], "analysis_failed")
        self.assertEqual(item["analysis_failed_reason"], "budget_exceeded")
        self.assertIsNotNone(item["timeout_info"])
        self.assertEqual(item["timeout_info"]["pair_timeout_sec"], 30)
        self.assertEqual(item["timeout_info"]["stage"], "pairwise")
        self.assertIsNone(item["full_similarity_score"])
        self.assertIsNone(item["library_reduced_score"])

    def test_successful_row_has_null_timeout_info_and_reason(self) -> None:
        results = [_sample_success_row()]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        item = payload["pairs"][0]
        self.assertIn("timeout_info", item)
        self.assertIsNone(item["timeout_info"])
        self.assertIn("analysis_failed_reason", item)
        self.assertIsNone(item["analysis_failed_reason"])


class TestExportPairwiseDetailedJsonSuccessRow(unittest.TestCase):
    """Успешная строка сохраняет signature_match и evidence."""

    def test_success_row_preserves_signature_match_and_evidence(self) -> None:
        results = [_sample_success_row()]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        item = payload["pairs"][0]
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["signature_match"], {"score": 1.0, "status": "match"})
        self.assertIsInstance(item["evidence"], list)
        self.assertEqual(len(item["evidence"]), 2)
        self.assertEqual(item["evidence"][0]["kind"], "per_layer")
        self.assertEqual(item["evidence"][1]["kind"], "signature_match")
        self.assertEqual(item["views_used"], ["component", "resource", "library"])
        self.assertEqual(item["full_similarity_score"], 0.91)
        self.assertEqual(item["library_reduced_score"], 0.87)


class TestExportPairwiseDetailedJsonTotalPairs(unittest.TestCase):
    """total_pairs == len(results)."""

    def test_total_pairs_equals_results_length_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[],
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["total_pairs"], 0)
        self.assertEqual(len(payload["pairs"]), 0)

    def test_total_pairs_equals_results_length_many(self) -> None:
        results = [
            _sample_success_row(),
            _sample_low_similarity_row(),
            _sample_timeout_row(),
            _sample_success_row(),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=results,
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["total_pairs"], 4)
        self.assertEqual(len(payload["pairs"]), 4)


class TestExportPairwiseDetailedJsonFieldPreservation(unittest.TestCase):
    """Дополнительные поля из pair_row сохраняются без потерь (forward-compat)."""

    def test_unknown_fields_are_preserved(self) -> None:
        row = _sample_success_row()
        row["future_field_x"] = {"nested": [1, 2, 3]}
        row["future_field_y"] = "custom-value"
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[row],
                output_path=output_path,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        item = payload["pairs"][0]
        self.assertEqual(item["future_field_x"], {"nested": [1, 2, 3]})
        self.assertEqual(item["future_field_y"], "custom-value")

    def test_output_parent_directory_is_created_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "nested" / "subdir" / "detailed.json"
            pairwise_runner.export_pairwise_detailed_json(
                results=[_sample_success_row()],
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

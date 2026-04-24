#!/usr/bin/env python3
"""Тесты для screening_reader.py (SCREENING-17-APP-KEYS-DEPRECATION-WARNING)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from screening_reader import (
    load_candidate_list_json,
    read_candidate_list,
    read_candidate_row,
)
from screening_writer import write_candidate_row


class TestReadCandidateRowNewFormat(unittest.TestCase):
    """Чтение записей с новыми canonical-полями — без предупреждения."""

    def test_new_format_no_warning(self) -> None:
        """Запись с query_app_id/candidate_app_id — без DeprecationWarning."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "screening_status": "preliminary_positive",
            "retrieval_score": 0.7,
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = read_candidate_row(row)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(
            len(deprecation_warnings), 0,
            "Не ожидался DeprecationWarning для записи с canonical-полями",
        )
        self.assertEqual(result["query_app_id"], "APP-A")
        self.assertEqual(result["candidate_app_id"], "APP-B")

    def test_new_format_with_mismatched_alias_raises(self) -> None:
        """Смешанная запись с рассинхроном полей должна падать явно."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "screening_status": "preliminary_positive",
            "app_a": "WRONG",
            "app_b": "APP-B",
            "retrieval_score": 0.7,
        }
        with self.assertRaises(ValueError) as ctx:
            read_candidate_row(row)
        self.assertIn("screening-contract-v1", str(ctx.exception))

    def test_new_format_returns_same_row(self) -> None:
        """Запись с canonical-полями возвращается без изменений."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "screening_status": "preliminary_positive",
            "retrieval_score": 0.5,
        }
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = read_candidate_row(row)
        self.assertIs(result, row)

    def test_writer_reader_roundtrip_preserves_canonical_contract(self) -> None:
        """writer -> reader roundtrip остаётся canonical-only."""
        row = write_candidate_row(
            "APP-A",
            "APP-B",
            {"retrieval_score": 0.8, "retrieval_rank": 1},
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = read_candidate_row(row)

        self.assertFalse(caught)
        self.assertEqual(result["query_app_id"], "APP-A")
        self.assertEqual(result["candidate_app_id"], "APP-B")
        self.assertEqual(result["screening_status"], "preliminary_positive")
        self.assertNotIn("app_a", result)
        self.assertNotIn("app_b", result)


class TestReadCandidateRowLegacyFormat(unittest.TestCase):
    """Чтение legacy-записей (только app_a/app_b) — с DeprecationWarning."""

    def test_legacy_format_emits_warning(self) -> None:
        """Запись только с app_a/app_b выдаёт DeprecationWarning."""
        row = {
            "app_a": "APP-A",
            "app_b": "APP-B",
            "retrieval_score": 0.6,
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            read_candidate_row(row)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertGreater(
            len(deprecation_warnings), 0,
            "Ожидался DeprecationWarning для legacy-записи с только app_a/app_b",
        )

    def test_legacy_format_normalized(self) -> None:
        """Legacy-запись нормализуется в canonical-only формат."""
        row = {"app_a": "APP-A", "app_b": "APP-B", "retrieval_score": 0.6}
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = read_candidate_row(row)

        self.assertEqual(result["query_app_id"], "APP-A")
        self.assertEqual(result["candidate_app_id"], "APP-B")
        self.assertEqual(result["screening_status"], "preliminary_positive")
        self.assertNotIn("app_a", result)
        self.assertNotIn("app_b", result)

    def test_legacy_format_preserves_extra_fields(self) -> None:
        """Дополнительные поля legacy-записи сохраняются."""
        row = {
            "app_a": "APP-A",
            "app_b": "APP-B",
            "retrieval_score": 0.6,
            "retrieval_rank": 2,
        }
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = read_candidate_row(row)

        self.assertEqual(result["retrieval_score"], 0.6)
        self.assertEqual(result["retrieval_rank"], 2)

    def test_missing_both_formats_raises(self) -> None:
        """При отсутствии и canonical, и legacy-полей — KeyError."""
        row = {"retrieval_score": 0.5}
        with self.assertRaises(KeyError):
            read_candidate_row(row)


class TestReadCandidateList(unittest.TestCase):
    """Тесты нормализации списка записей."""

    def test_mixed_list(self) -> None:
        """Список с legacy и новыми записями нормализуется корректно."""
        records = [
            # Новый формат — без warning.
            {
                "query_app_id": "APP-A",
                "candidate_app_id": "APP-B",
                "screening_status": "preliminary_positive",
                "retrieval_score": 0.9,
            },
            # Legacy формат — с warning.
            {"app_a": "APP-C", "app_b": "APP-D", "retrieval_score": 0.6},
        ]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = read_candidate_list(records)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["query_app_id"], "APP-A")
        self.assertEqual(result[0]["candidate_app_id"], "APP-B")
        self.assertEqual(result[1]["query_app_id"], "APP-C")
        self.assertEqual(result[1]["candidate_app_id"], "APP-D")
        self.assertNotIn("app_a", result[1])
        self.assertNotIn("app_b", result[1])

        # Только одна legacy-запись — ровно одно DeprecationWarning.
        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(deprecation_warnings), 1)


class TestLoadCandidateListJson(unittest.TestCase):
    """Тесты загрузки из JSON-файла."""

    def test_load_new_format_no_warning(self) -> None:
        """JSON с canonical-полями загружается без DeprecationWarning."""
        data = [
            {
                "query_app_id": "APP-A",
                "candidate_app_id": "APP-B",
                "screening_status": "preliminary_positive",
                "retrieval_score": 0.8,
            },
        ]
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", encoding="utf-8", delete=False
        ) as f:
            json.dump(data, f)
            tmp_path = Path(f.name)

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = load_candidate_list_json(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(deprecation_warnings), 0)
        self.assertEqual(result[0]["query_app_id"], "APP-A")

    def test_load_legacy_format_warns(self) -> None:
        """JSON только с app_a/app_b выдаёт DeprecationWarning и нормализуется."""
        data = [
            {"app_a": "APP-A", "app_b": "APP-B", "retrieval_score": 0.6},
        ]
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", encoding="utf-8", delete=False
        ) as f:
            json.dump(data, f)
            tmp_path = Path(f.name)

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = load_candidate_list_json(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertGreater(len(deprecation_warnings), 0)
        self.assertEqual(result[0]["query_app_id"], "APP-A")
        self.assertEqual(result[0]["candidate_app_id"], "APP-B")
        self.assertEqual(result[0]["screening_status"], "preliminary_positive")
        self.assertNotIn("app_a", result[0])
        self.assertNotIn("app_b", result[0])


if __name__ == "__main__":
    unittest.main()

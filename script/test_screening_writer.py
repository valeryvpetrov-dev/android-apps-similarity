#!/usr/bin/env python3
"""Тесты для screening_writer.py (SCREENING-17-APP-KEYS-DEPRECATION-WARNING)."""
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

from screening_writer import (
    validate_candidate_row,
    write_candidate_list_json,
    write_candidate_row,
)


class TestWriteCandidateRow(unittest.TestCase):
    """Тесты функции write_candidate_row."""

    def test_canonical_fields_present(self) -> None:
        """Запись содержит query_app_id и candidate_app_id как primary поля."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            row = write_candidate_row("APP-A", "APP-B")

        self.assertEqual(row["query_app_id"], "APP-A")
        self.assertEqual(row["candidate_app_id"], "APP-B")

    def test_deprecated_alias_filled(self) -> None:
        """app_a/app_b заполняются для обратной совместимости."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            row = write_candidate_row("APP-A", "APP-B")

        self.assertEqual(row["app_a"], "APP-A")
        self.assertEqual(row["app_b"], "APP-B")

    def test_deprecated_alias_matches_canonical(self) -> None:
        """app_a == query_app_id и app_b == candidate_app_id."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            row = write_candidate_row("Q-X", "CORPUS-1")

        self.assertEqual(row["app_a"], row["query_app_id"])
        self.assertEqual(row["app_b"], row["candidate_app_id"])

    def test_emits_deprecation_warning(self) -> None:
        """write_candidate_row выдаёт DeprecationWarning при вызове."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            write_candidate_row("APP-A", "APP-B")

        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "Ожидался DeprecationWarning при вызове write_candidate_row",
        )

    def test_extra_fields_included(self) -> None:
        """Дополнительные поля попадают в запись."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            row = write_candidate_row(
                "APP-A", "APP-B",
                extra_fields={"retrieval_score": 0.75, "retrieval_rank": 1},
            )

        self.assertEqual(row["retrieval_score"], 0.75)
        self.assertEqual(row["retrieval_rank"], 1)

    def test_no_lexicographic_sort_invariant(self) -> None:
        """Порядок query/candidate не зависит от лексикографии идентификаторов.

        По контракту v2 инвариант app_a < app_b снят. Запрос может иметь
        идентификатор, лексикографически больший кандидата.
        """
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            row = write_candidate_row("Z-QUERY", "A-CANDIDATE")

        # Z-QUERY > A-CANDIDATE лексикографически, но это допустимо.
        self.assertEqual(row["query_app_id"], "Z-QUERY")
        self.assertEqual(row["candidate_app_id"], "A-CANDIDATE")
        self.assertEqual(row["app_a"], "Z-QUERY")
        self.assertEqual(row["app_b"], "A-CANDIDATE")


class TestValidateCandidateRow(unittest.TestCase):
    """Тесты валидатора записи кандидата."""

    def test_valid_row_no_exception(self) -> None:
        """Корректная запись проходит валидацию без исключений."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "app_a": "APP-A",
            "app_b": "APP-B",
        }
        validate_candidate_row(row)  # не должно поднять исключение

    def test_mismatch_app_a_raises(self) -> None:
        """При расхождении app_a != query_app_id — AssertionError."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "app_a": "WRONG",  # нарушение инварианта
            "app_b": "APP-B",
        }
        with self.assertRaises(AssertionError) as ctx:
            validate_candidate_row(row)
        self.assertIn("screening-handoff-contract-v2", str(ctx.exception))

    def test_mismatch_app_b_raises(self) -> None:
        """При расхождении app_b != candidate_app_id — AssertionError."""
        row = {
            "query_app_id": "APP-A",
            "candidate_app_id": "APP-B",
            "app_a": "APP-A",
            "app_b": "WRONG",  # нарушение инварианта
        }
        with self.assertRaises(AssertionError) as ctx:
            validate_candidate_row(row)
        self.assertIn("screening-handoff-contract-v2", str(ctx.exception))

    def test_missing_canonical_field_raises(self) -> None:
        """При отсутствии query_app_id — KeyError."""
        row = {"app_a": "APP-A", "app_b": "APP-B"}
        with self.assertRaises(KeyError):
            validate_candidate_row(row)

    def test_no_alias_fields_ok(self) -> None:
        """Запись только с canonical-полями без alias — валидна."""
        row = {"query_app_id": "APP-A", "candidate_app_id": "APP-B"}
        validate_candidate_row(row)  # не должно поднять исключение


class TestWriteCandidateListJson(unittest.TestCase):
    """Тесты записи candidate_list в JSON-файл."""

    def test_writes_and_reads_json(self) -> None:
        """Записанный JSON содержит canonical поля."""
        rows = []
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            rows.append(write_candidate_row("APP-A", "APP-B", {"retrieval_score": 0.8}))
            rows.append(write_candidate_row("APP-A", "APP-C", {"retrieval_score": 0.5}))

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            write_candidate_list_json(rows, tmp_path)
            loaded = json.loads(tmp_path.read_text(encoding="utf-8"))
        finally:
            tmp_path.unlink(missing_ok=True)

        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["query_app_id"], "APP-A")
        self.assertEqual(loaded[0]["candidate_app_id"], "APP-B")
        self.assertEqual(loaded[1]["query_app_id"], "APP-A")
        self.assertEqual(loaded[1]["candidate_app_id"], "APP-C")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for screening_runner apk_path handoff (EXEC-SCREENING-APK-PATH).

Первичный отбор обязан класть путь к APK обоих приложений прямо в запись
кандидата. Это закрывает пробел сцепления screening -> deepening, найденный
в E-E2E-SMOKE-001: раньше оркестратору приходилось вручную дополнять
candidate_list полями ``app_a_apk_path`` / ``app_b_apk_path`` перед
передачей в deepening_runner. Поля совместимы с ``A_SIDE_CANDIDATE_APK_KEYS``
и ``B_SIDE_CANDIDATE_APK_KEYS`` из ``deepening_runner`` — deepening читает
путь напрямую из записи кандидата.

Покрытие:
  1. ``retrieval`` через exact Jaccard без candidate_index — apk_path в записи;
  2. то же через MinHash-LSH candidate_index — apk_path в записи;
  3. metric=ged + selected_layers=["code"] — apk_path в записи;
  4. обратная совместимость: если у app_record поля ``apk_path`` нет,
     в записи кандидата стоит None (а не пустая строка и не KeyError).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import build_candidate_list


def _make_app(app_id: str, layers: dict[str, set[str]], apk_path: str | None) -> dict:
    record: dict = {"app_id": app_id, "layers": layers}
    if apk_path is not None:
        record["apk_path"] = apk_path
    return record


def _make_full_layers(code: set[str]) -> dict[str, set[str]]:
    return {
        "code": set(code),
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": set(),
    }


class TestBuildCandidateListWritesApkPathJaccard(unittest.TestCase):
    """Exact Jaccard (no candidate_index) — apk_path обеих сторон в записи."""

    def test_apk_path_written_for_both_sides_on_jaccard(self) -> None:
        app_a = _make_app(
            "APP-A",
            _make_full_layers({"f1", "f2", "f3"}),
            apk_path="/abs/path/to/app_a.apk",
        )
        app_b = _make_app(
            "APP-B",
            _make_full_layers({"f2", "f3", "f4"}),
            apk_path="/abs/path/to/app_b.apk",
        )

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("app_a_apk_path", row)
        self.assertIn("app_b_apk_path", row)
        self.assertEqual(row["app_a_apk_path"], "/abs/path/to/app_a.apk")
        self.assertEqual(row["app_b_apk_path"], "/abs/path/to/app_b.apk")

    def test_apk_path_is_none_when_app_record_has_no_path(self) -> None:
        """Обратная совместимость: None, если путь не был передан."""
        app_a = _make_app(
            "APP-A",
            _make_full_layers({"f1", "f2"}),
            apk_path=None,
        )
        app_b = _make_app(
            "APP-B",
            _make_full_layers({"f2", "f3"}),
            apk_path=None,
        )

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        # Поля присутствуют и равны None — не пустая строка и не отсутствуют.
        self.assertIn("app_a_apk_path", row)
        self.assertIn("app_b_apk_path", row)
        self.assertIsNone(row["app_a_apk_path"])
        self.assertIsNone(row["app_b_apk_path"])


class TestBuildCandidateListWritesApkPathMinHashLSH(unittest.TestCase):
    """MinHash-LSH candidate_index — apk_path должен сохраняться."""

    def test_apk_path_written_with_minhash_lsh_candidate_index(self) -> None:
        # Два одинаковых набора фич, чтобы LSH гарантированно отнёс их
        # в одну банду и сформировал кандидатную пару.
        features = {"shared_f_{}".format(i) for i in range(64)}
        app_a = _make_app(
            "APP-A",
            _make_full_layers(set(features)),
            apk_path="/tmp/minhash_a.apk",
        )
        app_b = _make_app(
            "APP-B",
            _make_full_layers(set(features)),
            apk_path="/tmp/minhash_b.apk",
        )

        candidate_index_params = {
            "type": "minhash_lsh",
            "num_perm": 128,
            "bands": 32,
            "seed": 42,
            "features": ["code"],
        }

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params=candidate_index_params,
        )

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("app_a_apk_path", row)
        self.assertIn("app_b_apk_path", row)
        self.assertEqual(row["app_a_apk_path"], "/tmp/minhash_a.apk")
        self.assertEqual(row["app_b_apk_path"], "/tmp/minhash_b.apk")


class TestBuildCandidateListWritesApkPathGed(unittest.TestCase):
    """Метрика ged на code-слое — apk_path тоже обязан присутствовать."""

    def test_apk_path_written_for_ged_metric(self) -> None:
        app_a = {"app_id": "APP-A", "apk_path": "/ged/app_a.apk"}
        app_b = {"app_id": "APP-B", "apk_path": "/ged/app_b.apk"}

        # Патчим calculate_pair_score, чтобы не запускать реальный GED.
        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.75  # type: ignore[assignment]
            candidate_list = build_candidate_list(
                app_records=[app_a, app_b],
                selected_layers=["code"],
                metric="ged",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        finally:
            screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("app_a_apk_path", row)
        self.assertIn("app_b_apk_path", row)
        self.assertEqual(row["app_a_apk_path"], "/ged/app_a.apk")
        self.assertEqual(row["app_b_apk_path"], "/ged/app_b.apk")
        self.assertIn("per_view_scores", row)
        self.assertEqual(
            row["per_view_scores"],
            {
                "code": {
                    "jaccard": 0.0,
                    "tversky_a": 0.0,
                    "tversky_b": 0.0,
                    "overlap_min": 0.0,
                }
            },
        )


if __name__ == "__main__":
    unittest.main()

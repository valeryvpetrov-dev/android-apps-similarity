#!/usr/bin/env python3
"""EXEC-090-INCIDENTS: тесты реестра инцидентов жёсткого таймаута.

Политика D-2026-04-094: каждое срабатывание pair_timeout_sec — инцидент,
а не штатный режим, и должно попадать в отдельный JSON Lines журнал.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import timeout_incident_registry as reg


def _sample_pair_row() -> dict:
    return {
        "app_a": "AppA",
        "app_b": "AppB",
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "analysis_failed_reason": "budget_exceeded",
        "views_used": ["component", "resource", "library"],
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "timeout_info": {
            "pair_timeout_sec": 42,
            "stage": "pairwise",
        },
    }


class TestDefaultIncidentLogPath(unittest.TestCase):
    """Default путь журнала указывает в experiments/artifacts/E-EXEC-090-..."""

    def test_returns_path_instance(self) -> None:
        self.assertIsInstance(reg.default_incident_log_path(), Path)

    def test_default_path_points_to_experiments_artifacts(self) -> None:
        path = reg.default_incident_log_path()
        parts = path.parts
        # Проверяем хвост пути: .../experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/timeout-incidents.jsonl
        self.assertEqual(parts[-1], "timeout-incidents.jsonl")
        self.assertEqual(parts[-2], "E-EXEC-090-TIMEOUT-INCIDENTS")
        self.assertEqual(parts[-3], "artifacts")
        self.assertEqual(parts[-4], "experiments")


class TestRecordTimeoutIncident(unittest.TestCase):
    """record_timeout_incident пишет одну строку JSON в лог."""

    def test_writes_single_line_to_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "nested" / "timeout-incidents.jsonl"
            reg.record_timeout_incident(_sample_pair_row(), log_path=log_path)
            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            # Строго одна строка с терминальным \n
            self.assertEqual(content.count("\n"), 1)

    def test_written_record_is_valid_json_with_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"
            reg.record_timeout_incident(_sample_pair_row(), log_path=log_path)

            line = log_path.read_text(encoding="utf-8").strip()
            record = json.loads(line)

            required = {
                "schema_version",
                "recorded_at",
                "app_a",
                "app_b",
                "pair_timeout_sec",
                "stage",
                "views_used",
            }
            self.assertTrue(required.issubset(set(record.keys())))
            self.assertEqual(record["app_a"], "AppA")
            self.assertEqual(record["app_b"], "AppB")
            self.assertEqual(record["pair_timeout_sec"], 42)
            self.assertEqual(record["stage"], "pairwise")
            self.assertEqual(
                record["views_used"], ["component", "resource", "library"]
            )

    def test_schema_version_is_timeout_incident_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"
            record = reg.record_timeout_incident(
                _sample_pair_row(), log_path=log_path
            )
            self.assertEqual(record["schema_version"], "timeout-incident-v2")
            # И константа модуля должна совпадать.
            self.assertEqual(
                reg.INCIDENT_LOG_SCHEMA_VERSION, "timeout-incident-v2"
            )

    def test_recorded_at_is_valid_iso8601_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"
            record = reg.record_timeout_incident(
                _sample_pair_row(), log_path=log_path
            )
            recorded_at = record["recorded_at"]
            # ISO-8601 UTC: должен заканчиваться на +00:00 (datetime.isoformat с timezone.utc)
            # или на Z — оба варианта считаются валидными.
            self.assertTrue(
                recorded_at.endswith("+00:00") or recorded_at.endswith("Z"),
                msg=f"recorded_at не UTC: {recorded_at!r}",
            )
            # И должен парситься datetime.fromisoformat (для +00:00 — точно;
            # для 'Z' нормализуем).
            normalized = recorded_at.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            self.assertIsNotNone(parsed.tzinfo)


class TestReadTimeoutIncidents(unittest.TestCase):
    """read_timeout_incidents возвращает list[dict]."""

    def test_read_returns_list_of_length_two_after_two_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"
            reg.record_timeout_incident(_sample_pair_row(), log_path=log_path)
            reg.record_timeout_incident(_sample_pair_row(), log_path=log_path)

            records = reg.read_timeout_incidents(log_path=log_path)
            self.assertIsInstance(records, list)
            self.assertEqual(len(records), 2)
            for rec in records:
                self.assertIsInstance(rec, dict)
                self.assertEqual(rec["app_a"], "AppA")

    def test_read_skips_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = (
                json.dumps({"schema_version": "timeout-incident-v1", "x": 1})
                + "\n\n\n"
                + json.dumps({"schema_version": "timeout-incident-v1", "x": 2})
                + "\n\n"
            )
            log_path.write_text(payload, encoding="utf-8")

            records = reg.read_timeout_incidents(log_path=log_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["x"], 1)
            self.assertEqual(records[1]["x"], 2)

    def test_read_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "does-not-exist.jsonl"
            records = reg.read_timeout_incidents(log_path=log_path)
            self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

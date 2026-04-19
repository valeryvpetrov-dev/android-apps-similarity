"""Tests for EXEC-083-APKID-SMOKE smoke runner.

Тесты не запускают реальный APKiD: мокают `apkid_available`,
`detect_classifiers`, либо подменяют их результаты напрямую.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from script import run_apkid_smoke


class DefaultOutputDirTests(unittest.TestCase):
    """Каталог по умолчанию должен указывать на артефакты эксперимента."""

    def test_default_output_dir_points_to_experiments_artifacts(self) -> None:
        default_dir = run_apkid_smoke._default_output_dir()
        parts = default_dir.parts
        # Последние три элемента — experiments/artifacts/E-EXEC-083-APKID-SMOKE.
        self.assertEqual(parts[-3], "experiments")
        self.assertEqual(parts[-2], "artifacts")
        self.assertEqual(parts[-1], "E-EXEC-083-APKID-SMOKE")


class RunSmokeWritesJsonTests(unittest.TestCase):
    """run_smoke должен записывать JSON-файл в указанную директорию."""

    def test_run_smoke_writes_file_to_output_dir(self) -> None:
        fake_classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
            "apkid_version": "3.1.0",
            "rules_sha256": "e3b0c4",
            "status": "ok",
            "elapsed_sec": 0.1,
            "raw_stdout": "{}",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            with mock.patch.object(run_apkid_smoke, "apkid_available", return_value=True):
                with mock.patch.object(
                    run_apkid_smoke, "detect_classifiers", return_value=fake_classification
                ):
                    result = run_apkid_smoke.run_smoke("/tmp/fake.apk", out)
            files = list(out.glob("smoke-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(result["apk_path"], "/tmp/fake.apk")


class RunSmokeReportFieldsTests(unittest.TestCase):
    """JSON должен содержать все обязательные поля."""

    def test_run_smoke_report_has_required_fields(self) -> None:
        fake_classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
            "apkid_version": "3.1.0",
            "rules_sha256": "hash",
            "status": "ok",
            "elapsed_sec": 0.1,
            "raw_stdout": "{}",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            with mock.patch.object(run_apkid_smoke, "apkid_available", return_value=True):
                with mock.patch.object(
                    run_apkid_smoke, "detect_classifiers", return_value=fake_classification
                ):
                    report = run_apkid_smoke.run_smoke("/tmp/any.apk", out)

            written = list(out.glob("smoke-*.json"))[0]
            with written.open(encoding="utf-8") as handle:
                payload = json.load(handle)

        for field in ("apkid_version", "apk_path", "classification", "gate", "timestamp"):
            self.assertIn(field, payload)
        self.assertEqual(payload["apkid_version"], "3.1.0")
        self.assertEqual(payload["classification"]["status"], "ok")
        self.assertEqual(payload["gate"]["gate_status"], "clean")
        # Возвращаемый словарь тоже содержит все ключи.
        for field in ("apkid_version", "apk_path", "classification", "gate", "timestamp"):
            self.assertIn(field, report)


class RunSmokeNotAvailableTests(unittest.TestCase):
    """При apkid_available()=False — status=not_available, но файл пишется."""

    def test_run_smoke_not_available_still_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            with mock.patch.object(run_apkid_smoke, "apkid_available", return_value=False):
                report = run_apkid_smoke.run_smoke("/tmp/absent.apk", out)

            files = list(out.glob("smoke-*.json"))
            self.assertEqual(len(files), 1)
            with files[0].open(encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(report["classification"]["status"], "not_available")
        self.assertEqual(payload["classification"]["status"], "not_available")
        # Без apkid — пустой классификатор, gate падает в clean.
        self.assertEqual(report["gate"]["gate_status"], "clean")


class RunSmokeCliHelpTests(unittest.TestCase):
    """CLI --help не должен падать."""

    def test_cli_help_exits_zero(self) -> None:
        script_path = Path(run_apkid_smoke.__file__).resolve()
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("apk", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()

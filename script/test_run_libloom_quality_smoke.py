"""Тесты для run_libloom_quality_smoke (EXEC-LIBLOOM-QUALITY-SMOKE).

Все тесты не требуют реального LIBLOOM.jar и реальных APK:
- поведение детектора мокается через monkeypatch libloom_adapter.*;
- входные APK-файлы — временные пустышки в tempfile;
- каталог LIBLOOM — временный каталог с одним подкаталогом-заглушкой.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from script import run_libloom_quality_smoke as rq


class ParseApkListTests(unittest.TestCase):
    """Контракт parse_apk_list: корректный парсинг и защита от мусора."""

    def test_parses_valid_list(self) -> None:
        """Валидный JSON-список парсится и канонизирует ground_truth."""
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "apk_list.json"
            list_path.write_text(
                json.dumps(
                    [
                        {
                            "apk_path": "a.apk",
                            "ground_truth": ["OkHttp", "Gson"],
                            "notes": "demo",
                        },
                        {
                            "apk_path": "b.apk",
                            "ground_truth": [],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            entries = rq.parse_apk_list(str(list_path))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["apk_path"], "a.apk")
        # Канонизация в нижний регистр.
        self.assertEqual(entries[0]["ground_truth"], ["okhttp", "gson"])
        self.assertEqual(entries[0]["notes"], "demo")
        self.assertEqual(entries[1]["ground_truth"], [])

    def test_rejects_missing_file(self) -> None:
        """Отсутствующий файл -> ValueError."""
        with self.assertRaises(ValueError):
            rq.parse_apk_list("/nonexistent/apks.json")

    def test_rejects_bad_json(self) -> None:
        """Невалидный JSON -> ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "bad.json"
            list_path.write_text("not a json", encoding="utf-8")
            with self.assertRaises(ValueError):
                rq.parse_apk_list(str(list_path))

    def test_rejects_non_array_root(self) -> None:
        """JSON-объект на корне -> ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "obj.json"
            list_path.write_text('{"apk_path": "a.apk"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                rq.parse_apk_list(str(list_path))

    def test_rejects_entry_without_apk_path(self) -> None:
        """Запись без apk_path -> ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "nopath.json"
            list_path.write_text(
                '[{"ground_truth": []}]', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                rq.parse_apk_list(str(list_path))


class ComputePrfTests(unittest.TestCase):
    """Контракт compute_prf/score_entry: precision/recall/F1."""

    def test_empty_counts_gives_perfect_score(self) -> None:
        """Всё по нулям (ничего не предсказано, ничего не ожидалось) -> 1,1,1."""
        p, r, f1 = rq.compute_prf(0, 0, 0)
        self.assertEqual((p, r, f1), (1.0, 1.0, 1.0))

    def test_perfect_match(self) -> None:
        """Полное совпадение предсказаний и ground-truth -> P=R=F1=1."""
        scored = rq.score_entry(["okhttp", "gson"], ["OkHttp", "Gson"])
        self.assertEqual(scored["tp"], 2)
        self.assertEqual(scored["fp"], 0)
        self.assertEqual(scored["fn"], 0)
        self.assertEqual(scored["precision"], 1.0)
        self.assertEqual(scored["recall"], 1.0)
        self.assertEqual(scored["f1"], 1.0)

    def test_half_precision_half_recall(self) -> None:
        """1 TP, 1 FP, 1 FN -> P=R=0.5, F1=0.5."""
        scored = rq.score_entry(["okhttp", "retrofit"], ["okhttp", "gson"])
        self.assertEqual(scored["tp"], 1)
        self.assertEqual(scored["fp"], 1)
        self.assertEqual(scored["fn"], 1)
        self.assertAlmostEqual(scored["precision"], 0.5)
        self.assertAlmostEqual(scored["recall"], 0.5)
        self.assertAlmostEqual(scored["f1"], 0.5)

    def test_empty_ground_truth_with_predictions_hurts_precision(self) -> None:
        """Предсказания есть, ground-truth пуст -> precision=0, recall=1."""
        scored = rq.score_entry(["okhttp"], [])
        self.assertEqual(scored["tp"], 0)
        self.assertEqual(scored["fp"], 1)
        self.assertEqual(scored["fn"], 0)
        self.assertEqual(scored["precision"], 0.0)
        # Ground-truth пуст -> recall по соглашению 1.0.
        self.assertEqual(scored["recall"], 1.0)

    def test_canonicalization_strips_platform_suffixes(self) -> None:
        """-android/-jvm/-runtime суффиксы не мешают совпадению."""
        scored = rq.score_entry(
            ["lifecycle-runtime-android"], ["lifecycle"]
        )
        self.assertEqual(scored["tp"], 1)
        self.assertEqual(scored["fp"], 0)
        self.assertEqual(scored["fn"], 0)


class RunCorpusEmptyCatalogTests(unittest.TestCase):
    """Ранний выход run_corpus при отсутствии каталога/jar/java."""

    def test_partial_when_catalog_empty(self) -> None:
        """Пустой каталог -> status=partial, reason=catalog_missing_or_empty."""
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.NamedTemporaryFile(suffix=".jar") as jar_tmp:
            empty_catalog = Path(tmp) / "libs"
            empty_catalog.mkdir()
            report = rq.run_corpus(
                entries=[{"apk_path": "x.apk", "ground_truth": []}],
                catalog_dir=str(empty_catalog),
                jar_path=jar_tmp.name,
                timeout_sec=10,
                java_heap_mb=128,
            )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["reason"], "catalog_missing_or_empty")
        self.assertEqual(report["per_apk"], [])

    def test_partial_when_libloom_unavailable(self) -> None:
        """Нет JAR -> status=partial, reason=libloom_jar_or_java_missing."""
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "libs"
            cat.mkdir()
            (cat / "okhttp").mkdir()
            report = rq.run_corpus(
                entries=[{"apk_path": "x.apk", "ground_truth": []}],
                catalog_dir=str(cat),
                jar_path="/nonexistent/LIBLOOM.jar",
                timeout_sec=10,
                java_heap_mb=128,
            )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["reason"], "libloom_jar_or_java_missing")


class RunCorpusMockedTests(unittest.TestCase):
    """Прогон run_corpus с замокнутым detect_libraries."""

    def _prep_env(self, tmp_root: Path) -> tuple[Path, Path, Path]:
        """Создать jar-заглушку, каталог, APK-файл. Вернуть пути."""
        jar_path = tmp_root / "LIBLOOM.jar"
        jar_path.write_bytes(b"fake jar")
        catalog = tmp_root / "libs"
        catalog.mkdir()
        (catalog / "okhttp").mkdir()
        apk_dir = tmp_root / "apk"
        apk_dir.mkdir()
        apk_path = apk_dir / "sample.apk"
        apk_path.write_bytes(b"fake apk")
        return jar_path, catalog, apk_path

    def test_done_when_detect_ok_and_matches_ground_truth(self) -> None:
        """detect_ok + верные предсказания -> status=done, P=R=F1=1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            jar_path, catalog, apk_path = self._prep_env(tmp_root)
            detect_fake = {
                "status": "ok",
                "libraries": [
                    {"name": "okhttp", "version": ["4.12.0"], "similarity": 0.95},
                    {"name": "gson", "version": ["2.11.0"], "similarity": 0.9},
                ],
                "unknown_packages": [],
                "elapsed_sec": 0.42,
                "raw_stdout": None,
                "raw_stderr": None,
                "appname": "com.example.app",
                "error_reason": None,
            }
            with mock.patch.object(
                rq.libloom_adapter, "libloom_available", return_value=True
            ), mock.patch.object(
                rq.libloom_adapter, "detect_libraries", return_value=detect_fake
            ):
                report = rq.run_corpus(
                    entries=[
                        {
                            "apk_path": str(apk_path),
                            "ground_truth": ["okhttp", "gson"],
                            "notes": "",
                        }
                    ],
                    catalog_dir=str(catalog),
                    jar_path=str(jar_path),
                    timeout_sec=10,
                    java_heap_mb=128,
                )
        self.assertEqual(report["status"], "done")
        self.assertEqual(len(report["per_apk"]), 1)
        self.assertEqual(report["per_apk"][0]["detect_status"], "ok")
        self.assertEqual(report["per_apk"][0]["precision"], 1.0)
        self.assertEqual(report["per_apk"][0]["recall"], 1.0)
        self.assertEqual(report["per_apk"][0]["f1"], 1.0)
        self.assertEqual(report["corpus"]["tp"], 2)
        self.assertEqual(report["corpus"]["fp"], 0)
        self.assertEqual(report["corpus"]["fn"], 0)
        self.assertEqual(report["corpus"]["precision"], 1.0)

    def test_partial_when_all_apks_fail(self) -> None:
        """Ни один APK не прогнался -> status=partial, no_apk_scored_successfully."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            jar_path, catalog, apk_path = self._prep_env(tmp_root)
            detect_err = {
                "status": "subprocess_error",
                "libraries": [],
                "unknown_packages": [],
                "elapsed_sec": 0.0,
                "raw_stdout": None,
                "raw_stderr": None,
                "appname": None,
                "error_reason": "jvm_nonzero_exit:1",
            }
            with mock.patch.object(
                rq.libloom_adapter, "libloom_available", return_value=True
            ), mock.patch.object(
                rq.libloom_adapter, "detect_libraries", return_value=detect_err
            ):
                report = rq.run_corpus(
                    entries=[
                        {
                            "apk_path": str(apk_path),
                            "ground_truth": ["okhttp"],
                            "notes": "",
                        }
                    ],
                    catalog_dir=str(catalog),
                    jar_path=str(jar_path),
                    timeout_sec=10,
                    java_heap_mb=128,
                )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["reason"], "no_apk_scored_successfully")
        self.assertEqual(report["per_apk"][0]["detect_status"], "subprocess_error")


class WriteReportTests(unittest.TestCase):
    """Сохранение отчёта: создание папки и валидный JSON на диске."""

    def test_write_report_creates_parent_and_valid_json(self) -> None:
        """Отчёт сохраняется в JSON, родительская папка создаётся."""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "sub" / "report.json"
            report = {
                "status": "partial",
                "reason": "libloom_jar_or_java_missing",
                "per_apk": [],
                "corpus": {"tp": 0, "fp": 0, "fn": 0,
                           "precision": 0.0, "recall": 0.0, "f1": 0.0},
                "meta": {"catalog_dir": "x", "jar_path": "y",
                         "corpus_size": 0},
            }
            rq.write_report(report, str(out_path))
            self.assertTrue(out_path.is_file())
            with out_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        self.assertEqual(loaded["status"], "partial")
        self.assertEqual(loaded["reason"], "libloom_jar_or_java_missing")
        self.assertIn("corpus", loaded)


class MainCliTests(unittest.TestCase):
    """Интеграционный тест CLI main(): нет LIBLOOM -> rc=2, JSON на диске."""

    def test_main_writes_partial_report_when_libloom_missing(self) -> None:
        """Полный проход CLI с невалидным JAR -> rc=2 и файл partial-отчёта."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            apk_list = tmp_root / "apks.json"
            apk_list.write_text(
                json.dumps(
                    [{"apk_path": str(tmp_root / "fake.apk"),
                      "ground_truth": ["okhttp"]}]
                ),
                encoding="utf-8",
            )
            (tmp_root / "fake.apk").write_bytes(b"")
            catalog = tmp_root / "libs"
            catalog.mkdir()
            (catalog / "okhttp").mkdir()
            out_path = tmp_root / "report.json"
            rc = rq.main(
                [
                    "--apk-list", str(apk_list),
                    "--catalog-dir", str(catalog),
                    "--jar-path", "/nonexistent/LIBLOOM.jar",
                    "--output", str(out_path),
                ]
            )
            self.assertEqual(rc, 2)
            self.assertTrue(out_path.is_file())
            with out_path.open("r", encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["reason"], "libloom_jar_or_java_missing")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""EXEC-091: тесты сокращённого пути при высоком доверии на первичном отборе.

Если пара кандидатов получает очень высокую оценку сходства на первичном
отборе и при этом подпись APK совпадает (``signature_match.status == "match"``),
кандидат помечается флагом ``shortcut_applied=True`` и ``shortcut_reason =
"high_confidence_signature_match"``. Решение о пропуске углублённого сравнения
принимает downstream — этот модуль только размечает кандидатов.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import (
    HIGH_CONFIDENCE_SCORE_THRESHOLD,
    SHORTCUT_REQUIRES_SIGNATURE_MATCH,
    SHORTCUT_STATUS,
    build_candidate_list,
    collect_signature_match,
)


def _write_apk_with_cert(tmpdir: Path, name: str, cert_bytes: bytes) -> Path:
    """Создаёт минимальный APK-подобный ZIP с META-INF/CERT.RSA-файлом."""
    apk_path = tmpdir / name
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", b"<manifest/>")
        archive.writestr("classes.dex", b"dex")
        archive.writestr("META-INF/CERT.RSA", cert_bytes)
    return apk_path


def _run_build_with_fake_score(
    app_records: list[dict],
    score: float,
) -> list[dict]:
    """Обёртка над ``build_candidate_list`` с мок-подменой calculate_pair_score."""
    original_score = screening_runner.calculate_pair_score
    try:
        screening_runner.calculate_pair_score = lambda **kwargs: score  # type: ignore[assignment]
        return build_candidate_list(
            app_records=app_records,
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.10,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )
    finally:
        screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]


class TestShortcutConstants(unittest.TestCase):
    """T1: политика сокращённого пути задана явными константами."""

    def test_policy_constants_are_set(self) -> None:
        self.assertEqual(HIGH_CONFIDENCE_SCORE_THRESHOLD, 0.95)
        self.assertTrue(SHORTCUT_REQUIRES_SIGNATURE_MATCH)
        self.assertEqual(SHORTCUT_STATUS, "success_shortcut")


class TestShortcutHighConfidenceSignatureMatch(unittest.TestCase):
    """T2: высокая оценка + match подписи => shortcut_applied=True."""

    def test_high_score_and_signature_match_triggers_shortcut(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": "/fake/a.apk"},
            {"app_id": "APP-B", "apk_path": "/fake/b.apk"},
        ]

        original_sig = screening_runner.collect_signature_match
        try:
            screening_runner.collect_signature_match = (  # type: ignore[assignment]
                lambda a, b: {"score": 1.0, "status": "match"}
            )
            candidates = _run_build_with_fake_score(app_records, 0.96)
        finally:
            screening_runner.collect_signature_match = original_sig  # type: ignore[assignment]

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertTrue(row["shortcut_applied"])
        self.assertEqual(row["shortcut_reason"], "high_confidence_signature_match")
        self.assertEqual(row["shortcut_status"], SHORTCUT_STATUS)
        self.assertEqual(row["signature_match"], {"score": 1.0, "status": "match"})


class TestShortcutSignatureMismatch(unittest.TestCase):
    """T3: высокая оценка, но подпись не совпала => shortcut_applied=False."""

    def test_high_score_but_signature_mismatch_no_shortcut(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": "/fake/a.apk"},
            {"app_id": "APP-B", "apk_path": "/fake/b.apk"},
        ]

        original_sig = screening_runner.collect_signature_match
        try:
            screening_runner.collect_signature_match = (  # type: ignore[assignment]
                lambda a, b: {"score": 0.0, "status": "mismatch"}
            )
            candidates = _run_build_with_fake_score(app_records, 0.96)
        finally:
            screening_runner.collect_signature_match = original_sig  # type: ignore[assignment]

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertFalse(row["shortcut_applied"])
        self.assertIsNone(row["shortcut_reason"])
        self.assertIsNone(row["shortcut_status"])
        self.assertEqual(row["signature_match"]["status"], "mismatch")


class TestShortcutLowScoreEvenWithMatch(unittest.TestCase):
    """T4: подпись совпала, но оценка ниже порога => shortcut_applied=False."""

    def test_low_score_disables_shortcut_even_on_match(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": "/fake/a.apk"},
            {"app_id": "APP-B", "apk_path": "/fake/b.apk"},
        ]

        original_sig = screening_runner.collect_signature_match
        try:
            screening_runner.collect_signature_match = (  # type: ignore[assignment]
                lambda a, b: {"score": 1.0, "status": "match"}
            )
            candidates = _run_build_with_fake_score(app_records, 0.94)
        finally:
            screening_runner.collect_signature_match = original_sig  # type: ignore[assignment]

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertFalse(row["shortcut_applied"])
        self.assertIsNone(row["shortcut_reason"])
        # score 0.94 < 0.95 => shortcut не применён, но signature_match посчитан как обычно
        self.assertEqual(row["signature_match"]["status"], "match")


class TestShortcutMissingApkPaths(unittest.TestCase):
    """T5: нет APK-путей => signature_match.status='missing', shortcut_applied=False."""

    def test_missing_apk_paths_yield_missing_signature_and_no_shortcut(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": None},
            {"app_id": "APP-B", "apk_path": None},
        ]

        candidates = _run_build_with_fake_score(app_records, 0.99)

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertFalse(row["shortcut_applied"])
        self.assertIsNone(row["shortcut_reason"])
        self.assertEqual(row["signature_match"], {"score": 0.0, "status": "missing"})

    def test_one_apk_path_missing_yields_missing_signature(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": "/fake/a.apk"},
            {"app_id": "APP-B", "apk_path": None},
        ]

        candidates = _run_build_with_fake_score(app_records, 0.99)
        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertFalse(row["shortcut_applied"])
        self.assertEqual(row["signature_match"]["status"], "missing")


class TestShortcutBackwardCompatGed(unittest.TestCase):
    """T6: backward-compat — ged-метрика без per_view_scores не ломается.

    В текущей версии submodule screening_runner оперирует одной агрегированной
    оценкой (``retrieval_score``). Для metric='ged' поведение должно остаться
    стабильным: поля shortcut_* присутствуют, но при отсутствии APK-путей они
    False/None — никаких исключений.
    """

    def test_ged_metric_without_apk_paths_does_not_break(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": None},
            {"app_id": "APP-B", "apk_path": None},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            # Имитация metric="ged" — возвращает скаляр, не per_view_scores.
            screening_runner.calculate_pair_score = lambda **kw: 0.33  # type: ignore[assignment]
            candidates = build_candidate_list(
                app_records=app_records,
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

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertIn("shortcut_applied", row)
        self.assertFalse(row["shortcut_applied"])
        self.assertIsNone(row["shortcut_reason"])


class TestShortcutContractFieldsAlwaysPresent(unittest.TestCase):
    """T7: стабильный контракт — поля shortcut_applied и shortcut_reason всегда есть."""

    def test_shortcut_fields_present_even_when_not_applied(self) -> None:
        app_records = [
            {"app_id": "APP-A", "apk_path": None},
            {"app_id": "APP-B", "apk_path": None},
        ]

        candidates = _run_build_with_fake_score(app_records, 0.42)

        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertIn("shortcut_applied", row)
        self.assertIn("shortcut_reason", row)
        self.assertIn("signature_match", row)
        self.assertIs(row["shortcut_applied"], False)
        self.assertIsNone(row["shortcut_reason"])


class TestCollectSignatureMatchIntegration(unittest.TestCase):
    """Дополнительный интеграционный тест: реальное сравнение подписей двух APK."""

    def test_collect_signature_match_detects_match_on_same_cert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cert = b"FAKE-CERT-BYTES-SHARED-BETWEEN-APKS"
            apk_a = _write_apk_with_cert(Path(tmpdir), "a.apk", cert)
            apk_b = _write_apk_with_cert(Path(tmpdir), "b.apk", cert)
            result = collect_signature_match(apk_a, apk_b)
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["score"], 1.0)

    def test_collect_signature_match_detects_mismatch_on_different_cert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_a = _write_apk_with_cert(Path(tmpdir), "a.apk", b"CERT-ALPHA")
            apk_b = _write_apk_with_cert(Path(tmpdir), "b.apk", b"CERT-BETA")
            result = collect_signature_match(apk_a, apk_b)
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["score"], 0.0)

    def test_collect_signature_match_missing_when_path_is_none(self) -> None:
        self.assertEqual(
            collect_signature_match(None, "/fake.apk"),
            {"score": 0.0, "status": "missing"},
        )
        self.assertEqual(
            collect_signature_match("/fake.apk", None),
            {"score": 0.0, "status": "missing"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

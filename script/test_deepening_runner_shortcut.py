#!/usr/bin/env python3
"""EXEC-SHORTCUT-CASCADE-FIX: unit-тесты копирования shortcut-флагов в enrich_candidate.

Проверяем, что `deepening_runner.enrich_candidate` корректно пробрасывает
поля `shortcut_applied`, `shortcut_reason`, `signature_match` из входного
кандидата в результат, чтобы pairwise_runner мог активировать ветку
EXEC-091-EXEC в полном каскаде screening → deepening → pairwise.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from deepening_runner import enrich_candidate


def _empty_caches() -> tuple[dict, dict, dict]:
    return {}, {}, {}


def _base_candidate(apk_a: str, apk_b: str) -> dict:
    return {
        "app_a": {"app_id": "A", "apk_path": apk_a},
        "app_b": {"app_id": "B", "apk_path": apk_b},
    }


class TestEnrichCandidateShortcutPropagation(unittest.TestCase):
    """≥4 теста на корректный проброс shortcut-полей."""

    def _run(self, candidate: dict) -> dict:
        code_cache, decoded_cache, feature_cache = _empty_caches()
        # Не обогащаем никаких слоёв (layers_to_enrich=[]),
        # чтобы тест был быстрым и не зависел от файловой системы.
        return enrich_candidate(
            candidate=candidate,
            layers_to_enrich=[],
            code_cache=code_cache,
            decoded_cache=decoded_cache,
            feature_cache=feature_cache,
        )

    def test_shortcut_true_and_match_copied(self) -> None:
        """T1: shortcut_applied=True + signature_match копируются в result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk = str(Path(tmpdir) / "fake.apk")
            candidate = _base_candidate(apk, apk)
            candidate["shortcut_applied"] = True
            candidate["shortcut_reason"] = "high_confidence_signature_match"
            candidate["signature_match"] = {"status": "match", "score": 1.0}

            result = self._run(candidate)

            self.assertTrue(result["shortcut_applied"])
            self.assertEqual(result["shortcut_reason"], "high_confidence_signature_match")
            self.assertEqual(result["signature_match"]["status"], "match")

    def test_shortcut_false_not_blocking_copy(self) -> None:
        """T2: shortcut_applied=False тоже копируется (ключ присутствует = False)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk = str(Path(tmpdir) / "fake.apk")
            candidate = _base_candidate(apk, apk)
            candidate["shortcut_applied"] = False
            candidate["shortcut_reason"] = None
            candidate["signature_match"] = {"status": "mismatch", "score": 0.0}

            result = self._run(candidate)

            self.assertFalse(result["shortcut_applied"])
            self.assertIsNone(result["shortcut_reason"])
            self.assertEqual(result["signature_match"]["status"], "mismatch")

    def test_shortcut_reason_propagated(self) -> None:
        """T3: shortcut_reason конкретная строка сохраняется без изменений."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk = str(Path(tmpdir) / "fake.apk")
            candidate = _base_candidate(apk, apk)
            candidate["shortcut_applied"] = True
            candidate["shortcut_reason"] = "high_confidence_signature_match"

            result = self._run(candidate)

            self.assertEqual(result["shortcut_reason"], "high_confidence_signature_match")

    def test_signature_match_dict_propagated_intact(self) -> None:
        """T4: signature_match dict сохраняется целиком (score + status + hash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk = str(Path(tmpdir) / "fake.apk")
            candidate = _base_candidate(apk, apk)
            candidate["shortcut_applied"] = True
            candidate["shortcut_reason"] = "high_confidence_signature_match"
            candidate["signature_match"] = {
                "status": "match",
                "score": 1.0,
                "cert_hash": "abc123",
            }

            result = self._run(candidate)

            self.assertEqual(result["signature_match"]["cert_hash"], "abc123")
            self.assertEqual(result["signature_match"]["score"], 1.0)
            self.assertEqual(result["signature_match"]["status"], "match")

    def test_no_shortcut_keys_in_candidate_not_added(self) -> None:
        """T5: если shortcut-ключей нет в candidate — они не появляются в result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk = str(Path(tmpdir) / "fake.apk")
            candidate = _base_candidate(apk, apk)
            # Не добавляем никаких shortcut-полей

            result = self._run(candidate)

            self.assertNotIn("shortcut_applied", result)
            self.assertNotIn("shortcut_reason", result)
            self.assertNotIn("signature_match", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

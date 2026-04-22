#!/usr/bin/env python3
"""EXEC-SHORTCUT-CASCADE-FIX: integration-тест каскада screening → deepening → pairwise.

Проверяет, что shortcut-флаги, выставленные screening-слоем, не теряются
после прохода через deepening_runner.enrich_candidate, и pairwise_runner
корректно активирует ветку EXEC-091-EXEC (экономия «0 мс vs 110 мс»).

Синтетический сценарий:
  1. Кандидат с shortcut_applied=True выходит из screening.
  2. Проходит через deepening_runner.enrich_candidate (обогащение слоёв).
  3. Попадает в pairwise_runner._compute_pair_row_with_caches.
  4. pairwise возвращает verdict="likely_clone_by_signature",
     deep_verification_status="skipped_shortcut", elapsed_ms_deep=0.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from deepening_runner import enrich_candidate
from pairwise_runner import (
    DEEP_VERIFICATION_STATUS_SKIPPED,
    SHORTCUT_REASON_HIGH_CONFIDENCE,
    SHORTCUT_STATUS_SUCCESS,
    SHORTCUT_VERDICT_LIKELY_CLONE,
    _compute_pair_row_with_caches,
)


def _make_screening_candidate(apk_a: str, apk_b: str) -> dict:
    """Кандидат, как если бы его сформировал screening с shortcut-политикой."""
    return {
        "app_a": {"app_id": "com.example.A", "apk_path": apk_a},
        "app_b": {"app_id": "com.example.B", "apk_path": apk_b},
        "shortcut_applied": True,
        "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
        "signature_match": {"status": "match", "score": 1.0, "cert_hash": "deadbeef"},
        "screening_score": 0.97,
    }


class TestE2EPipelineShortcutCascade(unittest.TestCase):
    """Integration-тест: полный каскад screening → deepening → pairwise."""

    def test_shortcut_flags_survive_deepening_and_trigger_pairwise_skip(self) -> None:
        """Главный сценарий: shortcut-кандидат проходит через deepening без потери флагов
        и в pairwise активирует ветку early-return EXEC-091-EXEC."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_a = str(Path(tmpdir) / "a.apk")
            apk_b = str(Path(tmpdir) / "b.apk")
            Path(apk_a).write_bytes(b"fake")
            Path(apk_b).write_bytes(b"fake")

            # Шаг 1: screening выдаёт кандидата с shortcut-флагами.
            screening_candidate = _make_screening_candidate(apk_a, apk_b)

            # Шаг 2: deepening обогащает (без слоёв — только проброс флагов).
            deepened = enrich_candidate(
                candidate=screening_candidate,
                layers_to_enrich=[],
                code_cache={},
                decoded_cache={},
                feature_cache={},
            )

            # Убеждаемся, что флаги не потерялись после deepening.
            self.assertTrue(deepened.get("shortcut_applied"), "shortcut_applied потерян в deepening")
            self.assertEqual(deepened.get("shortcut_reason"), SHORTCUT_REASON_HIGH_CONFIDENCE)
            self.assertEqual(deepened.get("signature_match", {}).get("status"), "match")

            # Шаг 3: deepened кандидат идёт в pairwise.
            pair_row = _compute_pair_row_with_caches(
                candidate=deepened,
                selected_layers=["component", "resource", "library"],
                metric="cosine",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
                layer_cache={},
                code_cache={},
                apk_discovery_cache={},
            )

            # Шаг 4: проверяем контракт EXEC-091-EXEC.
            self.assertEqual(
                pair_row["verdict"],
                SHORTCUT_VERDICT_LIKELY_CLONE,
                "pairwise должен вернуть verdict=likely_clone_by_signature",
            )
            self.assertEqual(
                pair_row["deep_verification_status"],
                DEEP_VERIFICATION_STATUS_SKIPPED,
                "deep_verification_status должен быть skipped_shortcut",
            )
            self.assertEqual(
                pair_row["shortcut_status"],
                SHORTCUT_STATUS_SUCCESS,
                "shortcut_status должен быть success_shortcut",
            )
            self.assertLessEqual(
                pair_row["elapsed_ms_deep"],
                10,
                "elapsed_ms_deep должен быть ≤10 мс при shortcut",
            )
            self.assertIsNone(pair_row["analysis_failed_reason"])

    def test_no_shortcut_in_screening_leads_to_regular_path(self) -> None:
        """Обратный сценарий: кандидат без shortcut проходит deepening → pairwise обычным путём."""
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_a = str(Path(tmpdir) / "a.apk")
            apk_b = str(Path(tmpdir) / "b.apk")
            Path(apk_a).write_bytes(b"fake")
            Path(apk_b).write_bytes(b"fake")

            regular_candidate = {
                "app_a": {"app_id": "com.example.A", "apk_path": apk_a},
                "app_b": {"app_id": "com.example.B", "apk_path": apk_b},
                "shortcut_applied": False,
                "shortcut_reason": None,
                "signature_match": {"status": "mismatch", "score": 0.0},
            }

            deepened = enrich_candidate(
                candidate=regular_candidate,
                layers_to_enrich=[],
                code_cache={},
                decoded_cache={},
                feature_cache={},
            )

            # shortcut_applied=False → должен передаться в pairwise
            self.assertFalse(deepened.get("shortcut_applied"))

            # pairwise не должен активировать shortcut-ветку
            # (пойдёт по обычному пути, что упадёт из-за отсутствия реальных APK,
            # но проверим, что deep_verification_status ≠ skipped_shortcut)
            pair_row = _compute_pair_row_with_caches(
                candidate=deepened,
                selected_layers=["component"],
                metric="cosine",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=5,
                processes_count=1,
                threads_count=1,
                layer_cache={},
                code_cache={},
                apk_discovery_cache={},
            )
            self.assertNotEqual(
                pair_row.get("deep_verification_status"),
                DEEP_VERIFICATION_STATUS_SKIPPED,
                "обычный кандидат не должен попасть в ветку skipped_shortcut",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

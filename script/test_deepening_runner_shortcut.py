#!/usr/bin/env python3
"""EXEC-091-EXEC: тесты реального сокращения углублённого сравнения.

Если запись кандидата уже помечена на первичном отборе флагом
``shortcut_applied=True`` и ``shortcut_reason="high_confidence_signature_match"``
при совпадении подписи APK — углублённый слой обязан пропустить тяжёлые
функции и вернуть готовый ``pair_row`` с:

  - ``verdict = "likely_clone_by_signature"``;
  - ``deep_verification_status = "skipped_shortcut"``;
  - ``shortcut_status = "success_shortcut"``;
  - ``elapsed_ms_deep = 0`` (реально близко к нулю);
  - ``analysis_failed_reason = None``.

Контракт экономии: сокращённые пары возвращаются за ≤10 мс
(тяжёлые функции не вызывались), несокращённые — за десятки мс и больше.

Место реализации: ``script/pairwise_runner.py`` (early-return
в ``_compute_pair_row_with_caches``). Это disjoint-зона от команды D,
которая правит ``deepening_runner.py``.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
from pairwise_runner import (
    DEEP_VERIFICATION_STATUS_SKIPPED,
    SHORTCUT_REASON_HIGH_CONFIDENCE,
    SHORTCUT_STATUS_SUCCESS,
    SHORTCUT_VERDICT_LIKELY_CLONE,
    _compute_pair_row_with_caches,
    _should_skip_deep_verification,
)


def _touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


def _make_feature_bundle() -> dict:
    return {
        "mode": "enhanced",
        "code": {"feat-1", "feat-2"},
        "metadata": set(),
        "component": {
            "activities": [{"name": ".MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": set(),
            "features": set(),
        },
        "resource": {"resource_digests": set()},
        "library": {"libraries": {}},
        "signing": {"hash": None},
    }


def _build_shortcut_candidate(apk_a: Path, apk_b: Path) -> dict:
    """Кандидат с уже проставленными флагами короткого пути."""
    return {
        "app_a": {"app_id": "A", "apk_path": str(apk_a), "decoded_dir": "/tmp/decoded-a"},
        "app_b": {"app_id": "B", "apk_path": str(apk_b), "decoded_dir": "/tmp/decoded-b"},
        "shortcut_applied": True,
        "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
        "signature_match": {"score": 1.0, "status": "match"},
    }


def _build_regular_candidate(apk_a: Path, apk_b: Path) -> dict:
    """Кандидат без shortcut-флагов — обычный путь."""
    return {
        "app_a": {"app_id": "A", "apk_path": str(apk_a), "decoded_dir": "/tmp/decoded-a"},
        "app_b": {"app_id": "B", "apk_path": str(apk_b), "decoded_dir": "/tmp/decoded-b"},
        "shortcut_applied": False,
        "shortcut_reason": None,
        "signature_match": {"score": 1.0, "status": "match"},
    }


class TestShouldSkipDeepVerification(unittest.TestCase):
    """T0: внутренний предикат принятия решения о сокращении."""

    def test_all_conditions_match_returns_true(self) -> None:
        candidate = {
            "shortcut_applied": True,
            "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
            "signature_match": {"status": "match"},
        }
        self.assertTrue(_should_skip_deep_verification(candidate))

    def test_shortcut_applied_false_returns_false(self) -> None:
        candidate = {
            "shortcut_applied": False,
            "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
            "signature_match": {"status": "match"},
        }
        self.assertFalse(_should_skip_deep_verification(candidate))

    def test_signature_mismatch_returns_false(self) -> None:
        """Страховка от рассинхрона: если подпись больше не match — не пропускать."""
        candidate = {
            "shortcut_applied": True,
            "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
            "signature_match": {"status": "mismatch"},
        }
        self.assertFalse(_should_skip_deep_verification(candidate))

    def test_wrong_reason_returns_false(self) -> None:
        candidate = {
            "shortcut_applied": True,
            "shortcut_reason": "some_other_reason",
            "signature_match": {"status": "match"},
        }
        self.assertFalse(_should_skip_deep_verification(candidate))

    def test_missing_signature_match_returns_false(self) -> None:
        candidate = {
            "shortcut_applied": True,
            "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
        }
        self.assertFalse(_should_skip_deep_verification(candidate))


class TestShortcutEarlyReturn(unittest.TestCase):
    """T1: сокращённый путь — тяжёлые функции не вызывались."""

    def _run_compute_pair(self, candidate: dict) -> dict:
        return _compute_pair_row_with_caches(
            candidate=candidate,
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

    def test_shortcut_applied_returns_likely_clone_verdict(self) -> None:
        """T1.1: shortcut_applied=True + match => verdict=likely_clone_by_signature.

        Одновременно проверяем, что тяжёлые функции НЕ были вызваны.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)
            candidate = _build_shortcut_candidate(apk_a, apk_b)

            # Мокаем все тяжёлые функции, чтобы утверждать:
            # НИ ОДНА из них не должна быть вызвана.
            with mock.patch.object(
                pairwise_runner, "extract_all_features"
            ) as extract_mock, mock.patch.object(
                pairwise_runner, "calculate_pair_scores"
            ) as calc_mock, mock.patch.object(
                pairwise_runner, "load_layers_for_pairwise"
            ) as load_mock, mock.patch.object(
                pairwise_runner, "extract_apk_signature_hash"
            ) as sig_mock:
                row = self._run_compute_pair(candidate)

            self.assertEqual(row["verdict"], SHORTCUT_VERDICT_LIKELY_CLONE)
            self.assertEqual(
                row["deep_verification_status"], DEEP_VERIFICATION_STATUS_SKIPPED
            )
            self.assertEqual(row["shortcut_status"], SHORTCUT_STATUS_SUCCESS)
            self.assertIsNone(row["analysis_failed_reason"])

            # Тяжёлые функции реально НЕ вызывались.
            extract_mock.assert_not_called()
            calc_mock.assert_not_called()
            load_mock.assert_not_called()
            sig_mock.assert_not_called()

    def test_shortcut_applied_elapsed_ms_deep_is_zero_or_tiny(self) -> None:
        """T1.2: для сокращённых пар elapsed_ms_deep ≤10 мс (реально пропуск).

        Поскольку тяжёлые функции не вызываются, время углублённой стадии
        должно быть пренебрежимо малым.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)
            candidate = _build_shortcut_candidate(apk_a, apk_b)

            row = self._run_compute_pair(candidate)

            self.assertIn("elapsed_ms_deep", row)
            self.assertLessEqual(row["elapsed_ms_deep"], 10)

    def test_shortcut_applied_preserves_signature_match(self) -> None:
        """T1.3: signature_match из candidate переносится в pair_row без изменений."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)
            candidate = _build_shortcut_candidate(apk_a, apk_b)

            row = self._run_compute_pair(candidate)

            self.assertEqual(row["signature_match"]["status"], "match")
            self.assertEqual(row["signature_match"]["score"], 1.0)
            self.assertTrue(row["shortcut_applied"])
            self.assertEqual(row["shortcut_reason"], SHORTCUT_REASON_HIGH_CONFIDENCE)


class TestRegularPathWhenNoShortcut(unittest.TestCase):
    """T2: при shortcut_applied=False — обычный путь, тяжёлые функции вызывались."""

    def test_no_shortcut_calls_heavy_functions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)
            candidate = _build_regular_candidate(apk_a, apk_b)

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[_make_feature_bundle(), _make_feature_bundle()],
            ) as extract_mock, mock.patch.object(
                pairwise_runner,
                "extract_apk_signature_hash",
                return_value="hash-fake",
            ):
                row = _compute_pair_row_with_caches(
                    candidate=candidate,
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

            # Обычный путь — verdict=likely_clone_by_signature не появляется.
            self.assertNotIn("verdict", row)
            self.assertNotEqual(
                row.get("deep_verification_status"), DEEP_VERIFICATION_STATUS_SKIPPED
            )
            self.assertNotEqual(row.get("status"), "success_shortcut")

            # Тяжёлые функции ДОЛЖНЫ были быть вызваны.
            self.assertGreaterEqual(extract_mock.call_count, 2)

    def test_shortcut_applied_but_signature_mismatch_falls_back(self) -> None:
        """T2.2: shortcut_applied=True, но signature_match.status != match => обычный путь.

        Страховка от рассинхрона: если между screening и pairwise подпись
        обновилась и больше не match — сокращать нельзя.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)
            candidate = {
                "app_a": {
                    "app_id": "A",
                    "apk_path": str(apk_a),
                    "decoded_dir": "/tmp/decoded-a",
                },
                "app_b": {
                    "app_id": "B",
                    "apk_path": str(apk_b),
                    "decoded_dir": "/tmp/decoded-b",
                },
                "shortcut_applied": True,
                "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
                "signature_match": {"score": 0.0, "status": "mismatch"},
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[_make_feature_bundle(), _make_feature_bundle()],
            ) as extract_mock, mock.patch.object(
                pairwise_runner,
                "extract_apk_signature_hash",
                return_value="hash-fake",
            ):
                row = _compute_pair_row_with_caches(
                    candidate=candidate,
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

            # Пошли обычным путём несмотря на shortcut_applied=True.
            self.assertNotIn("verdict", row)
            self.assertNotEqual(row.get("status"), "success_shortcut")
            self.assertGreaterEqual(extract_mock.call_count, 2)


class TestShortcutSpeedupVsRegular(unittest.TestCase):
    """T3: количественная проверка экономии.

    Сокращённая пара — десятки микросекунд; обычная пара с мокнутыми
    тяжёлыми функциями, которые добавляют искусственную задержку —
    заметно дольше. Проверяем, что elapsed_ms_deep сокращённой пары
    пренебрежимо мал.
    """

    def test_shortcut_pair_elapsed_ms_deep_strictly_less_than_regular(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            _touch_apk(apk_a)
            _touch_apk(apk_b)

            shortcut_cand = _build_shortcut_candidate(apk_a, apk_b)
            regular_cand = _build_regular_candidate(apk_a, apk_b)

            # 1) Сокращённый прогон: тяжёлые функции не мокаем, но и не
            # должны быть вызваны. Замеряем реальное время внутри функции.
            shortcut_row = _compute_pair_row_with_caches(
                candidate=shortcut_cand,
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

            # 2) Обычный прогон с искусственной задержкой в тяжёлой функции.
            def slow_extract(apk_path, unpacked_dir=None):
                time.sleep(0.05)  # 50 мс
                return _make_feature_bundle()

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=slow_extract,
            ), mock.patch.object(
                pairwise_runner,
                "extract_apk_signature_hash",
                return_value="hash-fake",
            ):
                regular_row = _compute_pair_row_with_caches(
                    candidate=regular_cand,
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

            shortcut_ms = shortcut_row["elapsed_ms_deep"]
            regular_ms = regular_row["elapsed_ms_deep"]

            # Контракт: сокращённая пара — ≤10 мс.
            self.assertLessEqual(shortcut_ms, 10)
            # Обычная пара — заметно дольше (мы заставили её ждать >=100 мс
            # через две задержки по 50 мс в extract_all_features).
            self.assertGreater(regular_ms, shortcut_ms)
            self.assertGreaterEqual(regular_ms, 80)


if __name__ == "__main__":
    unittest.main(verbosity=2)

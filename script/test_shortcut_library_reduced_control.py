#!/usr/bin/env python3
"""DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL: tests for shortcut control sampling.

Контекст (рекомендация №3 критика волны 18,
``inbox/critics/deep-verification-2026-04-24.md`` раздел 6):
shortcut-пары на стадии screening (``shortcut_applied=True``) сейчас
пропускают углублённое сравнение и не вычисляют ``library_reduced_score``.
Возможен false positive: screening мог признать пару клоном по подписи,
а full path дал бы дисквалификацию через library_reduced_score (например,
для multi-app developer вроде VK/Yandex/Google, где все приложения
подписаны одним ключом).

Тесты падают до реализации ``script.shortcut_control.run_shortcut_control``
и проверяют:
  T1 — корректный размер контрольной выборки (≈10% или fallback);
  T2 — false_positive отмечается, когда library_reduced_score < threshold
       при shortcut_applied=True;
  T3 — control_ratio=0.0 не падает, выдаёт пустой результат с warning;
  T4 — детерминированность по rng_seed (две одинаковые выборки);
  T5 — false_positive_rate считается корректно на смешанной синтетике.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from shortcut_control import run_shortcut_control


def _make_shortcut_pair(app_a: str, app_b: str) -> dict:
    """Минимальная shortcut-пара (как из pairwise_runner._build_shortcut_pair_row)."""
    return {
        "app_a": app_a,
        "app_b": app_b,
        "shortcut_applied": True,
        "shortcut_reason": "high_confidence_signature_match",
        "signature_match": {"status": "match", "score": 1.0},
        "full_similarity_score": None,
        "library_reduced_score": None,
        "verdict": "likely_clone_by_signature",
    }


class TestRunShortcutControlSampleSize(unittest.TestCase):
    """T1: корректный размер контрольной выборки."""

    def test_sample_size_is_ten_percent_of_50_pairs(self) -> None:
        """Из 50 shortcut-пар при control_ratio=0.1 берётся 5 пар."""
        pairs = [_make_shortcut_pair(f"app_a_{i}", f"app_b_{i}") for i in range(50)]
        scorer = lambda pair: 0.9  # noqa: E731 — заведомо высокий score, не false positive

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=0.1,
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        self.assertEqual(report["shortcut_pairs_total"], 50)
        self.assertEqual(report["control_size"], 5)
        self.assertEqual(report["false_positive_count"], 0)
        self.assertEqual(report["false_positive_rate"], 0.0)
        self.assertEqual(report["threshold"], 0.5)

    def test_min_sample_size_one_for_small_pool(self) -> None:
        """Из 5 пар при control_ratio=0.1 берётся минимум 1 пара (не 0)."""
        pairs = [_make_shortcut_pair(f"app_a_{i}", f"app_b_{i}") for i in range(5)]
        scorer = lambda pair: 0.9  # noqa: E731

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=0.1,
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        # 0.1 * 5 = 0.5 → округляем вверх до 1, чтобы не было нулевой выборки
        # на маленьком пуле, иначе контроль теряет смысл.
        self.assertGreaterEqual(report["control_size"], 1)


class TestRunShortcutControlFalsePositiveDetection(unittest.TestCase):
    """T2: shortcut_applied=True + library_reduced_score < threshold → false_positive."""

    def test_low_library_reduced_score_marks_false_positive(self) -> None:
        """library_reduced_score=0.3 при threshold=0.5 → пара отмечается как false_positive."""
        pairs = [_make_shortcut_pair("app_clone_a", "app_clone_b")]
        scorer = lambda pair: 0.3  # noqa: E731 — ниже threshold=0.5

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=1.0,  # берём всю выборку, чтобы попасть на эту пару
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        self.assertEqual(report["control_size"], 1)
        self.assertEqual(report["false_positive_count"], 1)
        self.assertEqual(report["false_positive_rate"], 1.0)
        self.assertGreaterEqual(len(report["examples"]), 1)
        first_example = report["examples"][0]
        self.assertTrue(first_example["false_positive"])
        self.assertEqual(first_example["app_a"], "app_clone_a")
        self.assertEqual(first_example["app_b"], "app_clone_b")
        self.assertAlmostEqual(first_example["library_reduced_score"], 0.3)

    def test_high_library_reduced_score_does_not_mark_false_positive(self) -> None:
        """library_reduced_score=0.8 при threshold=0.5 → пара НЕ false_positive."""
        pairs = [_make_shortcut_pair("app_real_clone_a", "app_real_clone_b")]
        scorer = lambda pair: 0.8  # noqa: E731 — выше threshold

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=1.0,
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        self.assertEqual(report["false_positive_count"], 0)
        self.assertEqual(report["false_positive_rate"], 0.0)


class TestRunShortcutControlZeroRatio(unittest.TestCase):
    """T3: control_ratio=0.0 → пустой результат с warning, без падения."""

    def test_zero_ratio_returns_empty_report_with_warning(self) -> None:
        """При control_ratio=0.0 функция не падает, отдаёт warning и пустой control_size."""
        pairs = [_make_shortcut_pair(f"a_{i}", f"b_{i}") for i in range(10)]
        scorer_calls = []

        def scorer(pair: dict) -> float:
            scorer_calls.append(pair)
            return 0.5

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=0.0,
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        self.assertEqual(report["shortcut_pairs_total"], 10)
        self.assertEqual(report["control_size"], 0)
        self.assertEqual(report["false_positive_count"], 0)
        self.assertEqual(report["false_positive_rate"], 0.0)
        self.assertEqual(len(scorer_calls), 0)  # scorer не вызывался
        self.assertIn("warnings", report)
        self.assertGreaterEqual(len(report["warnings"]), 1)
        # В warning должно быть про control_ratio=0
        joined = " ".join(report["warnings"]).lower()
        self.assertIn("control_ratio", joined)


class TestRunShortcutControlDeterminism(unittest.TestCase):
    """T4: детерминированность по rng_seed."""

    def test_same_seed_gives_same_sample(self) -> None:
        """Два запуска с одинаковым seed выбирают одинаковые пары."""
        pairs = [_make_shortcut_pair(f"a_{i}", f"b_{i}") for i in range(50)]

        def scorer(pair: dict) -> float:
            return 0.5

        r1 = run_shortcut_control(
            pairs=pairs, control_ratio=0.1, threshold=0.4, scorer=scorer, rng_seed=42,
        )
        r2 = run_shortcut_control(
            pairs=pairs, control_ratio=0.1, threshold=0.4, scorer=scorer, rng_seed=42,
        )

        self.assertEqual(
            [(e["app_a"], e["app_b"]) for e in r1["examples"]],
            [(e["app_a"], e["app_b"]) for e in r2["examples"]],
        )


class TestRunShortcutControlMixedFalsePositiveRate(unittest.TestCase):
    """T5: false_positive_rate на смешанной синтетике."""

    def test_three_low_seven_high_gives_30_percent_fpr(self) -> None:
        """3 пары с low score (0.3), 7 пар с high score (0.8) → fpr = 0.3."""
        # 10 пар: первые 3 — false positive, последние 7 — true positive
        pairs = []
        scores = {}
        for i in range(10):
            pair = _make_shortcut_pair(f"low_a_{i}" if i < 3 else f"hi_a_{i}",
                                        f"low_b_{i}" if i < 3 else f"hi_b_{i}")
            pairs.append(pair)
            scores[(pair["app_a"], pair["app_b"])] = 0.3 if i < 3 else 0.8

        def scorer(pair: dict) -> float:
            return scores[(pair["app_a"], pair["app_b"])]

        report = run_shortcut_control(
            pairs=pairs,
            control_ratio=1.0,  # вся выборка
            threshold=0.5,
            scorer=scorer,
            rng_seed=42,
        )

        self.assertEqual(report["control_size"], 10)
        self.assertEqual(report["false_positive_count"], 3)
        self.assertAlmostEqual(report["false_positive_rate"], 0.3, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

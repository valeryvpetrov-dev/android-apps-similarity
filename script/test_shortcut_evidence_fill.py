#!/usr/bin/env python3
"""DEEP-26-SHORTCUT-EVIDENCE-FILL: failing-тесты на non-None поля shortcut-pair_row.

Контекст (рекомендация №2 критика DEEP волны 23, коммит 707b4bf,
``inbox/critics/deep-verification-2026-04-26.md``):

shortcut-пара (``shortcut_applied=True``) сейчас получает
``full_similarity_score=None``, ``library_reduced_score=None``, evidence —
только подпись APK, hint severity=low с fallback по
``library_profile_jaccard``, которого в pair_row нет. Это структурный
mismatch DEEP↔HINT: DEEP пишет None, HINT не умеет это переварить.

Цель волны 26 — закрыть mismatch:
  - shortcut-пары получают конкретные значения ``full_similarity_score`` и
    ``library_reduced_score`` (не ``None``);
  - ``evidence`` для shortcut-пары содержит как минимум одну запись
    (signature_match или layer_score), не пуст;
  - ``pairwise_explainer.generate_hint`` для shortcut-пары не падает и
    возвращает осмысленную строку (не пустую и не fallback).

Тесты падают до реализации заполнения полей в
``pairwise_runner._build_shortcut_pair_row``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
import pairwise_explainer


def _make_shortcut_candidate(
    *,
    retrieval_score: float = 0.95,
    per_view_scores: dict | None = None,
    signature_score: float = 1.0,
) -> dict:
    """Минимальный candidate, имитирующий screening row + deepening passthrough.

    Соответствует контракту screening_runner.build_candidate_list /
    deepening_runner.enrich_candidate: shortcut_applied=True означает
    high_confidence_signature_match, retrieval_score высокий, есть
    per_view_scores и signature_match.
    """
    if per_view_scores is None:
        per_view_scores = {
            "metadata": 0.92,
            "component": 0.88,
            "library": 0.97,
        }
    return {
        "app_a": {"app_id": "com.example.a", "apk_path": "/tmp/a.apk"},
        "app_b": {"app_id": "com.example.b", "apk_path": "/tmp/b.apk"},
        "shortcut_applied": True,
        "shortcut_reason": pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
        "shortcut_status": "preliminary_shortcut",
        "signature_match": {
            "status": "match",
            "score": signature_score,
            "cert_hash": "shared-cert",
        },
        "retrieval_score": float(retrieval_score),
        "per_view_scores": dict(per_view_scores),
    }


class TestShortcutFullSimilarityScoreNonNone(unittest.TestCase):
    """T1 (a): full_similarity_score у shortcut-пары не None."""

    def test_shortcut_pair_row_has_non_none_full_similarity_score(self) -> None:
        candidate = _make_shortcut_candidate(retrieval_score=0.93)
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        self.assertIsNotNone(
            pair_row.get("full_similarity_score"),
            msg="shortcut pair_row must carry full_similarity_score from screening signal",
        )
        self.assertIsInstance(pair_row["full_similarity_score"], float)
        self.assertGreaterEqual(pair_row["full_similarity_score"], 0.0)
        self.assertLessEqual(pair_row["full_similarity_score"], 1.0)

    def test_full_similarity_score_reflects_retrieval_score(self) -> None:
        """full_similarity_score должен быть >= retrieval_score (или равен ему).

        Источник истины — screening signal. shortcut применяется только при
        высоком retrieval_score, поэтому корректно перенести его в pair_row.
        """
        candidate = _make_shortcut_candidate(retrieval_score=0.91)
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        # Проверяем что значение разумное (не нулевое и согласовано с screening).
        self.assertGreaterEqual(pair_row["full_similarity_score"], 0.5)


class TestShortcutLibraryReducedScoreNonNone(unittest.TestCase):
    """T2 (b): library_reduced_score у shortcut-пары не None."""

    def test_shortcut_pair_row_has_non_none_library_reduced_score(self) -> None:
        candidate = _make_shortcut_candidate()
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        self.assertIsNotNone(
            pair_row.get("library_reduced_score"),
            msg="shortcut pair_row must carry library_reduced_score (no None).",
        )
        self.assertIsInstance(pair_row["library_reduced_score"], float)
        self.assertGreaterEqual(pair_row["library_reduced_score"], 0.0)
        self.assertLessEqual(pair_row["library_reduced_score"], 1.0)

    def test_library_reduced_falls_back_to_full_similarity_when_no_features(self) -> None:
        """Без feature-bundle library_reduced_score = full_similarity_score.

        Shortcut path не вызывает heavy feature extraction (это его суть —
        экономия). Если library-data отсутствует, library_reduced_score
        берётся равным full_similarity_score (single-source-of-truth: screening
        retrieval_score). Это корректное безопасное приближение: при высоком
        retrieval_score и signature_match=match шансы на library_reduced
        близки к full.
        """
        candidate = _make_shortcut_candidate(retrieval_score=0.88)
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        # full и reduced — оба не None и оба в [0,1].
        full = pair_row["full_similarity_score"]
        reduced = pair_row["library_reduced_score"]
        self.assertIsNotNone(full)
        self.assertIsNotNone(reduced)
        # Без feature-bundle reduced должен быть равен full (fallback).
        self.assertAlmostEqual(reduced, full, places=4)


class TestShortcutEvidenceNonEmpty(unittest.TestCase):
    """T3 (c): evidence у shortcut-пары не пуст."""

    def test_evidence_list_is_not_empty(self) -> None:
        candidate = _make_shortcut_candidate()
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        evidence = pair_row.get("evidence")
        self.assertIsInstance(evidence, list)
        self.assertGreater(
            len(evidence), 0,
            msg="shortcut pair_row evidence must be non-empty (signature_match + layer_score)",
        )

    def test_evidence_contains_signature_match(self) -> None:
        candidate = _make_shortcut_candidate(signature_score=1.0)
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        evidence = pair_row["evidence"]
        signature_items = [
            item for item in evidence
            if isinstance(item, dict) and item.get("signal_type") == "signature_match"
        ]
        self.assertEqual(
            len(signature_items), 1,
            msg="shortcut pair_row must have exactly one signature_match Evidence entry",
        )
        self.assertEqual(signature_items[0].get("ref"), "apk_signature")

    def test_evidence_contains_layer_scores_per_view(self) -> None:
        """Per-view evidence строится из per_view_scores screening-этапа."""
        candidate = _make_shortcut_candidate(
            per_view_scores={"metadata": 0.9, "component": 0.85, "library": 0.95},
        )
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        evidence = pair_row["evidence"]
        layer_refs = {
            item["ref"] for item in evidence
            if isinstance(item, dict) and item.get("signal_type") == "layer_score"
        }
        self.assertEqual(
            layer_refs, {"metadata", "component", "library"},
            msg="layer_score Evidence must cover all views_used",
        )


class TestShortcutGenerateHintNoFallback(unittest.TestCase):
    """T4 (d): generate_hint(pair_row) для shortcut-пары не пуст и не fallback."""

    def test_generate_hint_does_not_raise(self) -> None:
        candidate = _make_shortcut_candidate()
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        # Не должно бросать исключений.
        hint = pairwise_explainer.generate_hint(pair_row)
        self.assertIsInstance(hint, str)

    def test_generate_hint_returns_non_empty_string(self) -> None:
        candidate = _make_shortcut_candidate()
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        hint = pairwise_explainer.generate_hint(pair_row)
        # Hint не должен быть пустым (у нас есть signature_match + per-view scores).
        self.assertNotEqual(hint, "", msg="hint must be non-empty for shortcut pair")
        # Hint не должен быть fallback'ом про library_profile_jaccard.
        self.assertNotIn(
            "library_profile_jaccard", hint,
            msg="hint must not fall back to library_profile_jaccard (which doesn't exist in pair_row)",
        )

    def test_generate_hint_mentions_signature(self) -> None:
        """Hint должен упоминать signature_match — это главный сигнал shortcut."""
        candidate = _make_shortcut_candidate(signature_score=1.0)
        pair_row = pairwise_runner._build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=["metadata", "component", "library"],
            elapsed_ms_deep=2,
        )
        hint = pairwise_explainer.generate_hint(pair_row)
        # Подпись APK должна быть представлена как "apk_signature" (ref evidence).
        self.assertIn(
            "apk_signature", hint,
            msg="hint must reference apk_signature (the dominant shortcut signal)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

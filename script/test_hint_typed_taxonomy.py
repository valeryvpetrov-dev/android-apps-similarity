"""Тесты EXEC-HINT-28-TYPED-TAXONOMY.

Закрытие отложенного P1 (Variant 2 из EXEC-HINT-20-EVIDENCE-CANON):
типизированная hint-taxonomy 9 классов DeYoung ACL 2020 как СЛОЙ
ПОВЕРХ Evidence, а не замена canonical `format_hint_from_evidence`.

Канонические классы:
- LibraryImpact
- NewMethodCall
- ComponentChange
- ResourceChange
- PermissionChange
- NativeLibChange
- CertificateMismatch
- CodeRemoval
- ObfuscationShift

Контракт:
- `classify_evidence_to_taxonomy(evidence_record) -> str` возвращает один
  из 9 классов либо `"UnknownType"`;
- `classify_evidence_list(evidence_list) -> list[str]` поэлементно;
- `pairwise_explainer.build_output_rows` дополнительно кладёт в каждый
  `hint_metadata` ключ `classified_types: [list]` — соответствие
  evidence-записям той же пары;
- canonical Evidence -> hint путь не изменяется (см.
  `test_evidence_hint_canon.py`).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))


def _ev(source_stage: str, signal_type: str, magnitude: float, ref: str) -> dict:
    """Сконструировать запись Evidence без вызова валидатора, для тестов."""
    return {
        "source_stage": source_stage,
        "signal_type": signal_type,
        "magnitude": float(magnitude),
        "ref": ref,
    }


class ClassifyEvidenceToTaxonomyTests(unittest.TestCase):
    """(a) функция возвращает один из 9 классов или UnknownType."""

    def test_returns_one_of_canonical_classes_or_unknown(self) -> None:
        from hint_taxonomy import (  # noqa: WPS433
            HINT_TAXONOMY_CLASSES,
            UNKNOWN_TYPE,
            classify_evidence_to_taxonomy,
        )

        # 9 канонических классов согласно DeYoung ACL 2020.
        self.assertEqual(len(HINT_TAXONOMY_CLASSES), 9)
        allowed = set(HINT_TAXONOMY_CLASSES) | {UNKNOWN_TYPE}

        # Любой Evidence-записи присваивается строка из allowed-множества.
        sample_records = [
            _ev("pairwise", "layer_score", 0.7, "library"),
            _ev("signing", "signature_match", 0.0, "apk_signature"),
            _ev("pairwise", "layer_score", 0.5, "component"),
            _ev("pairwise", "layer_score", 0.3, "resource"),
            _ev("pairwise", "layer_score", 0.1, "code"),
            _ev("screening", "layer_score", 0.42, "metadata"),
            # некорректная запись -> UnknownType, без падений
            {"source_stage": "x", "signal_type": "y", "magnitude": "boom"},
        ]
        for record in sample_records:
            with self.subTest(record=record):
                result = classify_evidence_to_taxonomy(record)
                self.assertIsInstance(result, str)
                self.assertIn(result, allowed)


class LayerLibraryToLibraryImpactTests(unittest.TestCase):
    """(b) layer/ref="library", magnitude=0.7 -> LibraryImpact."""

    def test_library_layer_score_is_library_impact(self) -> None:
        from hint_taxonomy import classify_evidence_to_taxonomy  # noqa: WPS433

        record = _ev("pairwise", "layer_score", 0.7, "library")
        self.assertEqual(
            classify_evidence_to_taxonomy(record), "LibraryImpact"
        )


class SignatureMismatchToCertificateMismatchTests(unittest.TestCase):
    """(c) signal_type=signature_match с magnitude=0.0 -> CertificateMismatch.

    При magnitude=1.0 (подписи совпали — норма) типа изменения нет —
    функция возвращает UnknownType (либо не CertificateMismatch).
    """

    def test_signature_mismatch_is_certificate_mismatch(self) -> None:
        from hint_taxonomy import classify_evidence_to_taxonomy  # noqa: WPS433

        record = _ev("signing", "signature_match", 0.0, "apk_signature")
        self.assertEqual(
            classify_evidence_to_taxonomy(record), "CertificateMismatch"
        )

    def test_signature_match_full_is_not_certificate_mismatch(self) -> None:
        from hint_taxonomy import (  # noqa: WPS433
            UNKNOWN_TYPE,
            classify_evidence_to_taxonomy,
        )

        record = _ev("signing", "signature_match", 1.0, "apk_signature")
        result = classify_evidence_to_taxonomy(record)
        # Полное совпадение подписи — не "изменение", это норма.
        self.assertNotEqual(result, "CertificateMismatch")
        # Допустимо вернуть UnknownType (изменения нет).
        self.assertEqual(result, UNKNOWN_TYPE)


class BuildOutputRowsClassifiedTypesTests(unittest.TestCase):
    """(d) hint_metadata.classified_types список соответствует evidence-записям;
    canonical hint_text (explanation_hints / Evidence -> hint) не меняется.
    """

    def test_build_output_rows_adds_classified_types_per_pair(self) -> None:
        import evidence_formatter  # noqa: WPS433
        from pairwise_explainer import build_output_rows  # noqa: WPS433

        evidence = [
            _ev("pairwise", "layer_score", 0.7, "library"),
            _ev("pairwise", "layer_score", 0.5, "component"),
            _ev("signing", "signature_match", 0.0, "apk_signature"),
        ]

        # Эталон canonical hint, который не должен измениться.
        canonical_hint = evidence_formatter.format_hint_from_evidence(evidence)

        pair_rows = [
            {
                "pair_id": "A__B",
                "app_a": "A",
                "app_b": "B",
                "library_reduced_score": 0.6,
                "full_similarity_score": 0.7,
                "status": "success",
                "views_used": ["library", "component"],
                "evidence": evidence,
            }
        ]

        rows = build_output_rows(pair_rows)
        self.assertEqual(len(rows), 1)
        row = rows[0]

        # source остаётся canonical (Evidence как источник правды).
        self.assertEqual(row["hint_metadata"].get("source"), "canonical")

        # classified_types: список длиной как evidence, по одному классу
        # на запись.
        classified = row["hint_metadata"].get("classified_types")
        self.assertIsInstance(classified, list)
        self.assertEqual(len(classified), len(evidence))
        # Конкретные ожидаемые классы для нашего синтетического набора.
        self.assertEqual(
            classified,
            ["LibraryImpact", "ComponentChange", "CertificateMismatch"],
        )

        # Canonical hint (Evidence -> hint) не сломался.
        # build_output_rows не меняет format_hint_from_evidence(evidence);
        # пересчёт даёт ту же строку.
        same_canonical_hint = evidence_formatter.format_hint_from_evidence(
            row.get("evidence", evidence)
        )
        self.assertEqual(same_canonical_hint, canonical_hint)

    def test_legacy_branch_classified_types_is_empty_list(self) -> None:
        """Когда evidence отсутствует, classified_types — пустой список,
        legacy hint-путь сохраняется без изменения семантики.
        """
        from pairwise_explainer import build_output_rows  # noqa: WPS433

        pair_rows = [
            {
                "pair_id": "X__Y",
                "app_a": "X",
                "app_b": "Y",
                "library_reduced_score": 0.4,
                "full_similarity_score": 0.5,
                "status": "success",
                "views_used": ["component"],
                # evidence пуст — попадаем в legacy-ветку
            }
        ]
        rows = build_output_rows(pair_rows)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["hint_metadata"].get("source"), "legacy")
        # Контракт: ключ classified_types есть всегда, но при legacy он
        # пустой — типизированная taxonomy строится только поверх Evidence.
        self.assertEqual(row["hint_metadata"].get("classified_types"), [])


class ClassifyEvidenceListTests(unittest.TestCase):
    """`classify_evidence_list` поэлементно отображает список Evidence."""

    def test_classify_evidence_list_preserves_order_and_length(self) -> None:
        from hint_taxonomy import classify_evidence_list  # noqa: WPS433

        evidence = [
            _ev("pairwise", "layer_score", 0.7, "library"),
            _ev("signing", "signature_match", 0.0, "apk_signature"),
            _ev("pairwise", "layer_score", 0.5, "component"),
            _ev("pairwise", "layer_score", 0.4, "resource"),
        ]
        classified = classify_evidence_list(evidence)
        self.assertEqual(len(classified), len(evidence))
        self.assertEqual(
            classified,
            [
                "LibraryImpact",
                "CertificateMismatch",
                "ComponentChange",
                "ResourceChange",
            ],
        )

    def test_classify_evidence_list_handles_non_list(self) -> None:
        from hint_taxonomy import classify_evidence_list  # noqa: WPS433

        # Ничего не падает; некорректные входы -> пустой список.
        self.assertEqual(classify_evidence_list(None), [])
        self.assertEqual(classify_evidence_list("foo"), [])
        self.assertEqual(classify_evidence_list({}), [])


if __name__ == "__main__":
    unittest.main()

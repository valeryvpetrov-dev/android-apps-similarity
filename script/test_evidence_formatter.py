#!/usr/bin/env python3
"""EXEC-088: unit-тесты evidence_formatter.

Проверяем единый формат записей Evidence и helper'ы для сборки
списка Evidence из pair_row pairwise и из mapping per-layer score
первичного отбора (screening).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_formatter import (  # noqa: E402
    collect_evidence_from_pairwise,
    collect_evidence_from_screening_layers,
    describe_pair_evidence,
    evidence_to_markdown_block,
    format_evidence_as_text,
    format_evidence_summary,
    make_evidence,
    render_single_evidence,
)


class TestMakeEvidence(unittest.TestCase):

    def test_returns_dict_with_required_keys(self) -> None:
        record = make_evidence(
            source_stage="pairwise",
            signal_type="layer_score",
            magnitude=0.5,
            ref="component",
        )
        self.assertIsInstance(record, dict)
        self.assertEqual(
            set(record.keys()),
            {"source_stage", "signal_type", "magnitude", "ref"},
        )
        self.assertEqual(record["source_stage"], "pairwise")
        self.assertEqual(record["signal_type"], "layer_score")
        self.assertEqual(record["magnitude"], 0.5)
        self.assertEqual(record["ref"], "component")

    def test_magnitude_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_evidence(
                source_stage="pairwise",
                signal_type="layer_score",
                magnitude=-0.01,
                ref="component",
            )
        with self.assertRaises(ValueError):
            make_evidence(
                source_stage="pairwise",
                signal_type="layer_score",
                magnitude=1.01,
                ref="component",
            )

    def test_empty_source_stage_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_evidence(
                source_stage="",
                signal_type="layer_score",
                magnitude=0.5,
                ref="component",
            )

    def test_empty_signal_type_or_ref_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_evidence(
                source_stage="pairwise",
                signal_type="",
                magnitude=0.5,
                ref="component",
            )
        with self.assertRaises(ValueError):
            make_evidence(
                source_stage="pairwise",
                signal_type="layer_score",
                magnitude=0.5,
                ref="",
            )

    def test_magnitude_boundaries_accepted(self) -> None:
        low = make_evidence("screening", "layer_score", 0.0, "code")
        high = make_evidence("screening", "layer_score", 1.0, "code")
        self.assertEqual(low["magnitude"], 0.0)
        self.assertEqual(high["magnitude"], 1.0)


class TestCollectEvidenceFromPairwise(unittest.TestCase):

    def test_returns_empty_when_analysis_failed(self) -> None:
        pair_row = {
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": None,
            "library_reduced_score": None,
            "status": "analysis_failed",
            "views_used": ["component", "resource"],
            "signature_match": {"score": 0.0, "status": "missing"},
        }
        self.assertEqual(collect_evidence_from_pairwise(pair_row), [])

    def test_adds_layer_score_per_view(self) -> None:
        pair_row = {
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": 0.9,
            "library_reduced_score": 0.7,
            "status": "success",
            "views_used": ["component", "resource", "library"],
            "signature_match": {"score": 0.0, "status": "missing"},
        }
        evidence = collect_evidence_from_pairwise(pair_row)
        layer_records = [
            item for item in evidence if item["signal_type"] == "layer_score"
        ]
        self.assertEqual(len(layer_records), 3)
        self.assertEqual([item["ref"] for item in layer_records], ["component", "resource", "library"])
        for item in layer_records:
            self.assertEqual(item["source_stage"], "pairwise")
            self.assertEqual(item["magnitude"], 0.7)

    def test_falls_back_to_full_similarity_when_reduced_is_none(self) -> None:
        pair_row = {
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": 0.8,
            "library_reduced_score": None,
            "status": "success",
            "views_used": ["component"],
            "signature_match": {"score": 0.0, "status": "missing"},
        }
        evidence = collect_evidence_from_pairwise(pair_row)
        layer_records = [
            item for item in evidence if item["signal_type"] == "layer_score"
        ]
        self.assertEqual(len(layer_records), 1)
        self.assertEqual(layer_records[0]["magnitude"], 0.8)

    def test_adds_signature_match_evidence_when_present_and_success(self) -> None:
        pair_row = {
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": 0.6,
            "library_reduced_score": 0.6,
            "status": "success",
            "views_used": ["component"],
            "signature_match": {"score": 1.0, "status": "match"},
        }
        evidence = collect_evidence_from_pairwise(pair_row)
        sig_records = [
            item for item in evidence if item["signal_type"] == "signature_match"
        ]
        self.assertEqual(len(sig_records), 1)
        self.assertEqual(sig_records[0]["source_stage"], "signing")
        self.assertEqual(sig_records[0]["magnitude"], 1.0)
        self.assertEqual(sig_records[0]["ref"], "apk_signature")

    def test_no_signature_match_evidence_when_analysis_failed(self) -> None:
        pair_row = {
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": None,
            "library_reduced_score": None,
            "status": "analysis_failed",
            "views_used": ["component"],
            "signature_match": {"score": 1.0, "status": "match"},
        }
        evidence = collect_evidence_from_pairwise(pair_row)
        self.assertEqual(evidence, [])


class TestCollectEvidenceFromScreeningLayers(unittest.TestCase):

    def test_returns_list_of_dicts_one_per_layer(self) -> None:
        layers = {"component": 0.3, "resource": 0.5, "library": 0.8}
        evidence = collect_evidence_from_screening_layers(layers)
        self.assertEqual(len(evidence), 3)
        for item in evidence:
            self.assertIsInstance(item, dict)
            self.assertEqual(item["source_stage"], "screening")
            self.assertEqual(item["signal_type"], "layer_score")
        refs = {item["ref"] for item in evidence}
        self.assertEqual(refs, {"component", "resource", "library"})

    def test_clamps_magnitude_above_one(self) -> None:
        layers = {"code": 1.5, "component": 0.5}
        evidence = collect_evidence_from_screening_layers(layers)
        code_item = next(item for item in evidence if item["ref"] == "code")
        component_item = next(item for item in evidence if item["ref"] == "component")
        self.assertEqual(code_item["magnitude"], 1.0)
        self.assertEqual(component_item["magnitude"], 0.5)

    def test_clamps_magnitude_below_zero(self) -> None:
        layers = {"code": -0.3}
        evidence = collect_evidence_from_screening_layers(layers)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["magnitude"], 0.0)

    def test_custom_stage_name(self) -> None:
        layers = {"code": 0.25}
        evidence = collect_evidence_from_screening_layers(layers, stage_name="screening_v2")
        self.assertEqual(evidence[0]["source_stage"], "screening_v2")


class TestFormatEvidenceAsText(unittest.TestCase):

    def test_empty_list_returns_placeholder(self) -> None:
        result = format_evidence_as_text([])
        self.assertEqual(result, ["Нет доказательств для этой пары."])

    def test_maps_source_stage_to_russian_labels(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.4, "code"),
            make_evidence("pairwise", "layer_score", 0.7, "component"),
            make_evidence("signing", "signature_match", 1.0, "apk_signature"),
        ]
        lines = format_evidence_as_text(records)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("Первичный отбор:"))
        self.assertTrue(lines[1].startswith("Углублённое сравнение:"))
        self.assertTrue(lines[2].startswith("Подпись APK:"))
        self.assertIn("сходство по слою code", lines[0])
        self.assertIn("совпадение подписи APK", lines[2])
        self.assertIn("источник: apk_signature", lines[2])

    def test_limits_output_by_max_items(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.1 * (i + 1), "layer_{}".format(i))
            for i in range(5)
        ]
        lines = format_evidence_as_text(records, max_items=2)
        self.assertEqual(len(lines), 2)

    def test_sorts_by_stage_then_magnitude_descending(self) -> None:
        records = [
            make_evidence("signing", "signature_match", 0.9, "apk_signature"),
            make_evidence("screening", "layer_score", 0.2, "code"),
            make_evidence("pairwise", "layer_score", 0.8, "component"),
            make_evidence("screening", "layer_score", 0.6, "resource"),
        ]
        lines = format_evidence_as_text(records)
        # screening first (by magnitude desc: 0.6 then 0.2), then pairwise, then signing.
        self.assertTrue(lines[0].startswith("Первичный отбор:"))
        self.assertIn("resource", lines[0])
        self.assertTrue(lines[1].startswith("Первичный отбор:"))
        self.assertIn("code", lines[1])
        self.assertTrue(lines[2].startswith("Углублённое сравнение:"))
        self.assertTrue(lines[3].startswith("Подпись APK:"))

    def test_unknown_signal_type_uses_fallback_label(self) -> None:
        records = [make_evidence("pairwise", "library_match", 0.55, "okhttp")]
        lines = format_evidence_as_text(records)
        self.assertIn("совпадение набора библиотек", lines[0])

        records2 = [make_evidence("pairwise", "mystery_signal", 0.5, "x")]
        lines2 = format_evidence_as_text(records2)
        self.assertIn("сигнал mystery_signal", lines2[0])


class TestFormatEvidenceSummary(unittest.TestCase):

    def test_empty_summary_has_none_average_and_zero_total(self) -> None:
        summary = format_evidence_summary([])
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["average_magnitude"])
        self.assertIsNone(summary["max_magnitude_signal"])
        self.assertEqual(summary["top_signals"], [])
        self.assertEqual(
            summary["by_stage"],
            {"screening": 0, "pairwise": 0, "signing": 0},
        )

    def test_by_stage_counts_are_correct(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.1, "a"),
            make_evidence("screening", "layer_score", 0.2, "b"),
            make_evidence("pairwise", "layer_score", 0.9, "c"),
            make_evidence("signing", "signature_match", 1.0, "apk_signature"),
        ]
        summary = format_evidence_summary(records)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["by_stage"]["screening"], 2)
        self.assertEqual(summary["by_stage"]["pairwise"], 1)
        self.assertEqual(summary["by_stage"]["signing"], 1)
        self.assertAlmostEqual(summary["average_magnitude"], (0.1 + 0.2 + 0.9 + 1.0) / 4)
        self.assertEqual(summary["max_magnitude_signal"]["ref"], "apk_signature")

    def test_top_signals_capped_at_five(self) -> None:
        records = [
            make_evidence("screening", "layer_score", (i + 1) / 10.0, "layer_{}".format(i))
            for i in range(8)
        ]
        summary = format_evidence_summary(records)
        self.assertEqual(len(summary["top_signals"]), 5)
        magnitudes = [entry["magnitude"] for entry in summary["top_signals"]]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))


class TestDescribePairEvidence(unittest.TestCase):

    def _success_pair_row(self) -> dict:
        return {
            "pair_id": "PAIR-000001",
            "app_a": "A",
            "app_b": "B",
            "full_similarity_score": 0.7,
            "library_reduced_score": 0.65,
            "status": "success",
            "views_used": ["component", "resource"],
            "signature_match": {"score": 1.0, "status": "match"},
            "shortcut_applied": False,
            "timeout_info": None,
            "analysis_failed_reason": None,
            "evidence": [
                make_evidence("pairwise", "layer_score", 0.65, "component"),
                make_evidence("pairwise", "layer_score", 0.65, "resource"),
                make_evidence("signing", "signature_match", 1.0, "apk_signature"),
            ],
        }

    def test_success_pair_row_has_verdict_score_and_lines(self) -> None:
        pair_row = self._success_pair_row()
        description = describe_pair_evidence(pair_row)
        self.assertEqual(description["pair_id"], "PAIR-000001")
        self.assertEqual(description["verdict"], "success")
        self.assertEqual(description["similarity_score"], 0.65)
        self.assertIsInstance(description["evidence_lines"], list)
        self.assertTrue(len(description["evidence_lines"]) >= 3)
        self.assertEqual(description["summary"]["total"], 3)
        self.assertEqual(description["notes"], [])

    def test_budget_exceeded_adds_incident_note(self) -> None:
        pair_row = {
            "pair_id": "PAIR-000002",
            "status": "analysis_failed",
            "analysis_failed_reason": "budget_exceeded",
            "timeout_info": {"pair_timeout_sec": 30, "stage": "pairwise"},
            "evidence": [],
        }
        description = describe_pair_evidence(pair_row)
        self.assertEqual(description["verdict"], "analysis_failed")
        self.assertIn(
            "Пара прервана по жёсткому лимиту времени (инцидент).",
            description["notes"],
        )
        # Ensure timeout note is also present with correct format.
        timeout_notes = [
            note for note in description["notes"] if note.startswith("Таймаут:")
        ]
        self.assertEqual(len(timeout_notes), 1)
        self.assertIn("30", timeout_notes[0])
        self.assertIn("pairwise", timeout_notes[0])
        # Empty evidence list produces the placeholder.
        self.assertEqual(
            description["evidence_lines"],
            ["Нет доказательств для этой пары."],
        )

    def test_shortcut_applied_adds_note(self) -> None:
        pair_row = {
            "pair_id": "PAIR-000003",
            "status": "success",
            "library_reduced_score": 0.9,
            "full_similarity_score": 0.95,
            "views_used": ["component"],
            "signature_match": {"score": 1.0, "status": "match"},
            "shortcut_applied": True,
            "evidence": [
                make_evidence("pairwise", "layer_score", 0.9, "component"),
                make_evidence("signing", "signature_match", 1.0, "apk_signature"),
            ],
        }
        description = describe_pair_evidence(pair_row)
        self.assertIn(
            "Применён сокращённый путь: высокое доверие + совпадение подписи.",
            description["notes"],
        )

    def test_signature_mismatch_adds_note(self) -> None:
        pair_row = {
            "pair_id": "PAIR-000004",
            "status": "success",
            "library_reduced_score": 0.3,
            "full_similarity_score": 0.3,
            "views_used": ["component"],
            "signature_match": {"score": 0.0, "status": "mismatch"},
            "evidence": [
                make_evidence("pairwise", "layer_score", 0.3, "component"),
            ],
        }
        description = describe_pair_evidence(pair_row)
        self.assertIn(
            "Внимание: подписи APK не совпадают.",
            description["notes"],
        )

    def test_merges_screening_and_pairwise_evidence_with_dedup(self) -> None:
        screening_result = {
            "evidence": [
                make_evidence("screening", "layer_score", 0.4, "code"),
                make_evidence("screening", "layer_score", 0.5, "component"),
            ]
        }
        pair_row = {
            "pair_id": "PAIR-000005",
            "status": "success",
            "library_reduced_score": 0.6,
            "full_similarity_score": 0.6,
            "views_used": ["component"],
            "signature_match": {"score": 1.0, "status": "match"},
            "evidence": [
                make_evidence("pairwise", "layer_score", 0.6, "component"),
                make_evidence("signing", "signature_match", 1.0, "apk_signature"),
            ],
        }
        description = describe_pair_evidence(pair_row, screening_result)
        # 2 screening + 1 pairwise + 1 signing = 4 after dedup.
        self.assertEqual(description["summary"]["total"], 4)
        self.assertEqual(description["summary"]["by_stage"]["screening"], 2)
        self.assertEqual(description["summary"]["by_stage"]["pairwise"], 1)
        self.assertEqual(description["summary"]["by_stage"]["signing"], 1)


class TestEvidenceToMarkdownBlock(unittest.TestCase):

    def test_empty_list_ru_returns_placeholder_block(self) -> None:
        block = evidence_to_markdown_block([])
        self.assertIn("## Доказательства", block)
        self.assertIn("Всего сигналов: 0", block)
        self.assertIn("Доказательств не найдено.", block)
        # Пустой список не даёт таблицу.
        self.assertNotIn("| Этап |", block)
        self.assertNotIn("<", block)

    def test_three_evidence_renders_table_with_three_rows(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.4, "code"),
            make_evidence("pairwise", "layer_score", 0.72, "component"),
            make_evidence("signing", "signature_match", 1.0, "apk_signature"),
        ]
        block = evidence_to_markdown_block(records)
        self.assertIn("## Доказательства", block)
        self.assertIn("Всего сигналов: 3", block)
        self.assertIn("| Этап | Тип сигнала | Сила | Источник |", block)
        self.assertIn("| --- | --- | --- | --- |", block)
        table_lines = [
            line
            for line in block.splitlines()
            if line.startswith("|")
            and not line.startswith("| ---")
            and not line.startswith("| Этап")
        ]
        self.assertEqual(len(table_lines), 3)

    def test_layer_score_label_ru(self) -> None:
        records = [make_evidence("screening", "layer_score", 0.55, "code")]
        block = evidence_to_markdown_block(records, locale="ru")
        self.assertIn("Оценка по слою", block)
        self.assertNotIn("Layer score", block)

    def test_layer_score_label_en(self) -> None:
        records = [make_evidence("screening", "layer_score", 0.55, "code")]
        block = evidence_to_markdown_block(records, locale="en")
        self.assertIn("## Evidence", block)
        self.assertIn("Total signals: 1", block)
        self.assertIn("Layer score", block)
        self.assertNotIn("Оценка по слою", block)

    def test_summary_groups_by_source_stage(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.6, "a"),
            make_evidence("screening", "layer_score", 0.8, "b"),
            make_evidence("screening", "layer_score", 0.76, "c"),
            make_evidence("pairwise", "layer_score", 0.9, "component"),
            make_evidence("signing", "signature_match", 1.0, "apk_signature"),
        ]
        block = evidence_to_markdown_block(records)
        self.assertIn("### Сводка по этапам", block)
        # screening: 3 сигнала, средняя сила (0.6+0.8+0.76)/3 = 0.72
        self.assertIn("- screening: 3 сигнала, средняя сила 0.72", block)
        self.assertIn("- pairwise: 1 сигнал, средняя сила 0.90", block)
        self.assertIn("- signing: 1 сигнал, средняя сила 1.00", block)

    def test_render_single_evidence_signature_row(self) -> None:
        record = make_evidence(
            "pairwise", "signature_match", 0.95, "apk_signature"
        )
        row_ru = render_single_evidence(record, locale="ru")
        self.assertEqual(
            row_ru,
            "| pairwise | Совпадение подписи APK | 0.95 | apk_signature |",
        )
        row_en = render_single_evidence(record, locale="en")
        self.assertEqual(
            row_en,
            "| pairwise | APK signature match | 0.95 | apk_signature |",
        )

    def test_unknown_signal_type_uses_signal_type_as_label(self) -> None:
        records = [make_evidence("pairwise", "library_match", 0.5, "okhttp")]
        block = evidence_to_markdown_block(records)
        # Неизвестный signal_type -> отдаём его сырьём.
        self.assertIn("library_match", block)

    def test_output_contains_no_html_tags(self) -> None:
        records = [
            make_evidence("screening", "layer_score", 0.4, "code"),
            make_evidence("pairwise", "layer_score", 0.7, "component"),
        ]
        block = evidence_to_markdown_block(records)
        self.assertNotIn("<", block)
        self.assertNotIn(">", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)

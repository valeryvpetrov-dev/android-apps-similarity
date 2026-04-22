#!/usr/bin/env python3
"""Tests for EXEC-088-WRITERS: screening_runner evidence writer.

Закрепляем контракт писателя единого формата Evidence в первичном
отборе: build_candidate_list записывает поле ``evidence`` рядом с
``per_view_scores`` для каждого кандидата, одна запись на слой со
``source_stage='screening'``; при ged-метрике ``evidence`` не
добавляется (сохраняется обратная совместимость).

Дополнительно проверяем reader collect_all_evidence (объединение
Evidence обоих этапов с дедупликацией).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from evidence_formatter import collect_all_evidence, make_evidence
from screening_runner import build_candidate_list


def _make_app(app_id: str, layers: dict[str, set[str]]) -> dict:
    return {"app_id": app_id, "layers": layers}


class TestScreeningRunnerEvidenceWriter(unittest.TestCase):
    def test_build_candidate_list_writes_evidence_when_per_view_scores_present(
        self,
    ) -> None:
        app_a = _make_app(
            "APP-A",
            {
                "code": {"m1", "m2", "m3"},
                "component": {"activity:Main"},
                "resource": {"res_type:layout", "res_ext:xml"},
                "metadata": {"package_name:com.example.a"},
                "library": {"lib_abi:arm64-v8a"},
            },
        )
        app_b = _make_app(
            "APP-B",
            {
                "code": {"m2", "m3", "m4"},
                "component": {"activity:Main", "activity:Detail"},
                "resource": {"res_type:layout"},
                "metadata": {"package_name:com.example.b"},
                "library": {"lib_abi:arm64-v8a", "lib_abi:x86_64"},
            },
        )
        selected_layers = ["code", "component", "resource", "metadata", "library"]

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=selected_layers,
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )
        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertIn("evidence", row)
        self.assertIn("per_view_scores", row)
        self.assertIsInstance(row["evidence"], list)
        self.assertGreater(len(row["evidence"]), 0)

    def test_evidence_has_one_record_per_layer_with_screening_source_stage(
        self,
    ) -> None:
        app_a = _make_app(
            "APP-A",
            {
                "code": {"m1", "m2"},
                "component": {"activity:Main"},
                "resource": set(),
                "metadata": set(),
                "library": set(),
            },
        )
        app_b = _make_app(
            "APP-B",
            {
                "code": {"m1", "m3"},
                "component": {"activity:Main"},
                "resource": set(),
                "metadata": set(),
                "library": set(),
            },
        )
        selected_layers = ["code", "component", "resource", "metadata", "library"]

        candidate_list = build_candidate_list(
            app_records=[app_a, app_b],
            selected_layers=selected_layers,
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
        )
        self.assertEqual(len(candidate_list), 1)
        evidence = candidate_list[0]["evidence"]
        # По одной записи на слой.
        self.assertEqual(len(evidence), len(selected_layers))
        refs = [item["ref"] for item in evidence]
        self.assertEqual(set(refs), set(selected_layers))
        for item in evidence:
            self.assertEqual(item["source_stage"], "screening")
            self.assertEqual(item["signal_type"], "layer_score")
            self.assertGreaterEqual(item["magnitude"], 0.0)
            self.assertLessEqual(item["magnitude"], 1.0)
            # Согласованность с per_view_scores.
            self.assertAlmostEqual(
                item["magnitude"],
                candidate_list[0]["per_view_scores"][item["ref"]],
                places=9,
            )

    def test_evidence_present_for_ged_metric_uses_posthoc_per_view_scores(self) -> None:
        app_records = [
            {"app_id": "APP-A"},
            {"app_id": "APP-B"},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.42  # type: ignore[assignment]
            candidate_list = build_candidate_list(
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

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertEqual(row["per_view_scores"], {"code": 0.0})
        self.assertEqual(len(row["evidence"]), 1)
        self.assertEqual(row["evidence"][0]["source_stage"], "screening")
        self.assertEqual(row["evidence"][0]["signal_type"], "layer_score")
        self.assertEqual(row["evidence"][0]["ref"], "code")
        self.assertEqual(row["evidence"][0]["magnitude"], 0.0)


class TestCollectAllEvidence(unittest.TestCase):
    def test_combines_evidence_from_both_stages(self) -> None:
        screening_result = {
            "evidence": [
                make_evidence("screening", "layer_score", 0.3, "component"),
                make_evidence("screening", "layer_score", 0.5, "resource"),
            ]
        }
        pair_row = {
            "evidence": [
                make_evidence("pairwise", "layer_score", 0.7, "component"),
                make_evidence("signing", "signature_match", 1.0, "apk_signature"),
            ]
        }
        combined = collect_all_evidence(screening_result, pair_row)
        self.assertEqual(len(combined), 4)
        stages = {item["source_stage"] for item in combined}
        self.assertEqual(stages, {"screening", "pairwise", "signing"})

    def test_deduplicates_by_source_stage_signal_type_ref(self) -> None:
        repeated = make_evidence("screening", "layer_score", 0.3, "component")
        screening_result = {"evidence": [repeated]}
        pair_row = {
            "evidence": [
                # Дубликат по тройному ключу — отбрасывается.
                make_evidence("screening", "layer_score", 0.9, "component"),
                make_evidence("pairwise", "layer_score", 0.7, "component"),
            ]
        }
        combined = collect_all_evidence(screening_result, pair_row)
        keys = [(item["source_stage"], item["signal_type"], item["ref"]) for item in combined]
        self.assertEqual(
            keys,
            [
                ("screening", "layer_score", "component"),
                ("pairwise", "layer_score", "component"),
            ],
        )
        # Сохраняется первая встреченная запись.
        screening_component = next(
            item for item in combined if item["source_stage"] == "screening"
        )
        self.assertEqual(screening_component["magnitude"], 0.3)

    def test_returns_empty_list_when_both_inputs_are_none(self) -> None:
        self.assertEqual(collect_all_evidence(None, None), [])


if __name__ == "__main__":
    unittest.main()

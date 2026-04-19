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
    make_evidence,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

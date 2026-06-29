#!/usr/bin/env python3
"""Name-independent code core evidence."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from code_core_evidence import build_code_core_evidence_fields


class TestCodeCoreEvidence(unittest.TestCase):
    def test_finds_shared_method_body_fingerprints_with_disjoint_method_ids(self) -> None:
        layers_a = {
            "code_fingerprint": {
                "method_fp:Lcom/left/A;->a()V:fp:shared_1",
                "method_fp:Lcom/left/B;->b()V:fp:shared_2",
                "method_fp:Lcom/left/C;->c()V:fp:left_only",
            },
            "code_fingerprint_values": ["fp:shared_1", "fp:shared_2", "fp:left_only"],
        }
        layers_b = {
            "code_fingerprint": {
                "method_fp:Lx/y/A;->x()V:fp:shared_1",
                "method_fp:Lx/y/B;->y()V:fp:shared_2",
                "method_fp:Lx/y/C;->z()V:fp:right_only",
            },
            "code_fingerprint_values": ["fp:shared_1", "fp:shared_2", "fp:right_only"],
        }

        fields = build_code_core_evidence_fields(layers_a, layers_b, ["code"])

        self.assertTrue(fields["code_core_evidence_applied"])
        self.assertEqual(fields["code_core_evidence_role"], "evidence_only")
        self.assertEqual(fields["code_core_score_effect"], "none")
        self.assertFalse(fields["code_core_score_included"])
        self.assertEqual(fields["code_core_common_fingerprint_count"], 2)
        self.assertEqual(fields["left_code_core_fingerprint_count"], 3)
        self.assertEqual(fields["right_code_core_fingerprint_count"], 3)
        self.assertAlmostEqual(fields["code_core_counter_containment"], 2 / 3)
        self.assertEqual(
            fields["code_core_common_fingerprint_sample"],
            ["fp:shared_1", "fp:shared_2"],
        )

    def test_does_not_apply_without_shared_method_body_fingerprints(self) -> None:
        fields = build_code_core_evidence_fields(
            {"code_fingerprint_values": ["fp:left"]},
            {"code_fingerprint_values": ["fp:right"]},
            ["code"],
        )

        self.assertFalse(fields["code_core_evidence_applied"])
        self.assertEqual(fields["code_core_common_fingerprint_count"], 0)
        self.assertEqual(fields["code_core_evidence_score"], 0.0)

    def test_does_not_apply_when_code_layer_is_not_selected(self) -> None:
        fields = build_code_core_evidence_fields(
            {"code_fingerprint_values": ["fp:shared_1", "fp:shared_2"]},
            {"code_fingerprint_values": ["fp:shared_1", "fp:shared_2"]},
            ["resource"],
        )

        self.assertFalse(fields["code_core_evidence_applied"])
        self.assertEqual(fields["code_core_evidence_error"], "code_layer_not_selected")


if __name__ == "__main__":
    unittest.main(verbosity=2)

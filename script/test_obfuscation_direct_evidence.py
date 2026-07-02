#!/usr/bin/env python3
"""Direct evidence-only obfuscation name-shortening delta."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from obfuscation_direct_evidence import build_obfuscation_direct_evidence_fields


class TestObfuscationDirectEvidence(unittest.TestCase):
    def test_detects_short_names_with_same_package_and_same_fingerprints(self) -> None:
        fields = build_obfuscation_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lcom/example/app/Feature00;-><init>()V": "fp-init-0",
                    "Lcom/example/app/Feature00;->compute00()I": "fp-compute-0",
                    "Lcom/example/app/Feature01;-><init>()V": "fp-init-1",
                    "Lcom/example/app/Feature01;->compute01()I": "fp-compute-1",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lcom/example/app/a;-><init>()V": "fp-init-0",
                    "Lcom/example/app/a;->a()I": "fp-compute-0",
                    "Lcom/example/app/b;-><init>()V": "fp-init-1",
                    "Lcom/example/app/b;->b()I": "fp-compute-1",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["obfuscation_direct_evidence_applied"])
        self.assertEqual(fields["obfuscation_direct_evidence_role"], "evidence_only")
        self.assertEqual(fields["obfuscation_direct_score_effect"], "none")
        self.assertFalse(fields["obfuscation_direct_score_included"])
        self.assertTrue(fields["obfuscation_direct_same_package"])
        self.assertEqual(fields["obfuscation_direct_left_method_count"], 4)
        self.assertEqual(fields["obfuscation_direct_right_method_count"], 4)
        self.assertEqual(fields["obfuscation_direct_common_method_id_count"], 0)
        self.assertEqual(fields["obfuscation_direct_common_fingerprint_count"], 4)
        self.assertEqual(fields["obfuscation_direct_left_short_class_name_count"], 0)
        self.assertEqual(fields["obfuscation_direct_right_short_class_name_count"], 4)
        self.assertEqual(fields["obfuscation_direct_right_short_method_name_count"], 2)
        self.assertEqual(fields["obfuscation_direct_right_class_name_sample"], ["a", "b"])
        self.assertEqual(fields["obfuscation_direct_right_method_name_sample"], ["a", "b"])

    def test_does_not_apply_without_short_name_shift(self) -> None:
        fields = build_obfuscation_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lcom/example/app/Feature00;->compute00()I": "fp-compute-0",
                    "Lcom/example/app/Feature01;->compute01()I": "fp-compute-1",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lcom/example/app/Feature00;->compute00()I": "fp-compute-0",
                    "Lcom/example/app/Feature01;->compute01()I": "fp-compute-1",
                }
            },
            ["code"],
        )

        self.assertFalse(fields["obfuscation_direct_evidence_applied"])
        self.assertEqual(fields["obfuscation_direct_common_fingerprint_count"], 2)
        self.assertEqual(fields["obfuscation_direct_right_short_class_name_count"], 0)

    def test_does_not_apply_when_code_layer_is_not_selected(self) -> None:
        fields = build_obfuscation_direct_evidence_fields(
            {"code_method_fingerprints": {"Lcom/example/app/Feature;->run()V": "fp"}},
            {"code_method_fingerprints": {"Lcom/example/app/a;->a()V": "fp"}},
            ["resource"],
        )

        self.assertFalse(fields["obfuscation_direct_evidence_applied"])
        self.assertEqual(
            fields["obfuscation_direct_evidence_error"],
            "code_layer_not_selected",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

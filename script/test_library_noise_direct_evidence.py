#!/usr/bin/env python3
"""Direct evidence-only library-noise namespace delta."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from library_noise_direct_evidence import build_library_noise_direct_evidence_fields


class TestLibraryNoiseDirectEvidence(unittest.TestCase):
    def test_extracts_concentrated_right_only_namespace_group(self) -> None:
        fields = build_library_noise_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                    "Lcom/vendor/sdk/Ad;->load()V": "fp:ad-load",
                    "Lcom/vendor/sdk/Ad;->show()V": "fp:ad-show",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["library_noise_direct_evidence_applied"])
        self.assertEqual(fields["library_noise_direct_evidence_role"], "evidence_only")
        self.assertEqual(fields["library_noise_direct_score_effect"], "none")
        self.assertFalse(fields["library_noise_direct_score_included"])
        self.assertEqual(fields["library_noise_direct_left_method_count"], 2)
        self.assertEqual(fields["library_noise_direct_right_method_count"], 4)
        self.assertEqual(fields["library_noise_direct_common_method_id_count"], 2)
        self.assertEqual(fields["library_noise_direct_right_only_method_count"], 2)
        self.assertEqual(fields["library_noise_direct_left_only_method_count"], 0)
        self.assertEqual(
            fields["library_noise_direct_top_namespace_prefix"],
            "com.vendor.sdk",
        )
        self.assertEqual(fields["library_noise_direct_top_namespace_method_count"], 2)
        self.assertEqual(fields["library_noise_direct_top_namespace_method_ratio"], 1.0)
        self.assertEqual(
            fields["library_noise_direct_namespace_groups"],
            [{"prefix": "com.vendor.sdk", "count": 2}],
        )
        self.assertEqual(
            fields["library_noise_direct_method_sample"],
            [
                "Lcom/vendor/sdk/Ad;->load()V",
                "Lcom/vendor/sdk/Ad;->show()V",
            ],
        )

    def test_does_not_apply_for_scattered_right_only_methods(self) -> None:
        fields = build_library_noise_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                    "Lcom/vendor/sdk/Ad;->load()V": "fp:ad-load",
                    "Lorg/other/Tracker;->ping()V": "fp:other",
                }
            },
            ["code"],
        )

        self.assertFalse(fields["library_noise_direct_evidence_applied"])
        self.assertEqual(fields["library_noise_direct_top_namespace_method_count"], 1)
        self.assertEqual(fields["library_noise_direct_evidence_score"], 0.0)

    def test_does_not_apply_when_code_layer_is_not_selected(self) -> None:
        fields = build_library_noise_direct_evidence_fields(
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            {
                "code_method_fingerprints": {
                    "Lcom/vendor/sdk/Ad;->load()V": "fp:ad-load",
                    "Lcom/vendor/sdk/Ad;->show()V": "fp:ad-show",
                }
            },
            ["resource"],
        )

        self.assertFalse(fields["library_noise_direct_evidence_applied"])
        self.assertEqual(
            fields["library_noise_direct_evidence_error"],
            "code_layer_not_selected",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

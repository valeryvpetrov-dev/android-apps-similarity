#!/usr/bin/env python3
"""Direct evidence-only added-code method delta."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from added_code_direct_evidence import build_added_code_direct_evidence_fields


class TestAddedCodeDirectEvidence(unittest.TestCase):
    def test_extracts_right_only_methods_and_namespace_prefixes(self) -> None:
        fields = build_added_code_direct_evidence_fields(
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
                    "Lpayload/New;->x()V": "fp:x",
                    "Lpayload/New;->y()V": "fp:y",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_evidence_role"], "evidence_only")
        self.assertEqual(fields["added_code_direct_score_effect"], "none")
        self.assertFalse(fields["added_code_direct_score_included"])
        self.assertEqual(fields["added_code_direct_right_only_method_count"], 2)
        self.assertEqual(fields["added_code_direct_left_only_method_count"], 0)
        self.assertEqual(fields["added_code_direct_common_method_id_count"], 2)
        self.assertEqual(fields["added_code_direct_added_fingerprint_count"], 2)
        self.assertEqual(fields["added_code_direct_evidence_score"], 0.5)
        self.assertEqual(
            fields["added_code_direct_top_method_prefixes"],
            [{"prefix": "payload.New", "count": 2}],
        )
        self.assertEqual(
            fields["added_code_direct_method_sample"],
            ["Lpayload/New;->x()V", "Lpayload/New;->y()V"],
        )

    def test_does_not_apply_without_right_only_methods(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            ["code"],
        )

        self.assertFalse(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_right_only_method_count"], 0)

    def test_does_not_treat_renamed_methods_as_added_code(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lrenamed/X;->x()V": "fp:a",
                    "Lrenamed/Y;->y()V": "fp:b",
                }
            },
            ["code"],
        )

        self.assertEqual(
            fields["added_code_direct_evidence_policy_id"],
            "R_added_code_direct_evidence_policy_v2",
        )
        self.assertFalse(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_right_only_method_count"], 2)
        self.assertEqual(fields["added_code_direct_added_fingerprint_count"], 0)
        self.assertEqual(fields["added_code_direct_evidence_score"], 0.0)

    def test_scores_only_fingerprints_not_present_on_the_left(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lrenamed/X;->x()V": "fp:a",
                    "Lrenamed/Y;->y()V": "fp:b",
                    "Lpayload/New;->z()V": "fp:new",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_right_only_method_count"], 3)
        self.assertEqual(fields["added_code_direct_added_fingerprint_count"], 1)
        self.assertAlmostEqual(
            fields["added_code_direct_evidence_score"],
            1 / 3,
        )
        self.assertEqual(
            fields["added_code_direct_method_sample"],
            ["Lpayload/New;->z()V"],
        )
        self.assertEqual(
            fields["added_code_direct_top_method_prefixes"],
            [{"prefix": "payload.New", "count": 1}],
        )

    def test_common_method_body_change_does_not_confirm_renamed_method(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:changed",
                    "Lrenamed/Y;->y()V": "fp:b",
                }
            },
            ["code"],
        )

        self.assertFalse(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_right_only_method_count"], 1)
        self.assertEqual(fields["added_code_direct_added_fingerprint_count"], 0)
        self.assertEqual(fields["added_code_direct_evidence_score"], 0.0)

    def test_does_not_sample_ambiguous_duplicate_fingerprint_method(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lleft/Original;->a()V": "fp:shared",
                }
            },
            {
                "code_method_fingerprints": {
                    "La/PossiblyRenamed;->a()V": "fp:shared",
                    "Lz/PossiblyAdded;->a()V": "fp:shared",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["added_code_direct_evidence_applied"])
        self.assertEqual(fields["added_code_direct_added_fingerprint_count"], 1)
        self.assertEqual(fields["added_code_direct_method_sample"], [])
        self.assertEqual(fields["added_code_direct_top_method_prefixes"], [])

    def test_does_not_apply_when_fingerprint_map_is_unavailable(self) -> None:
        for layers_a, layers_b in (
            ({}, {"code_method_fingerprints": {"Lright/A;->a()V": "fp:a"}}),
            ({"code_method_fingerprints": {"Lleft/A;->a()V": "fp:a"}}, {}),
        ):
            with self.subTest(layers_a=layers_a, layers_b=layers_b):
                fields = build_added_code_direct_evidence_fields(
                    layers_a,
                    layers_b,
                    ["code"],
                )

                self.assertFalse(fields["added_code_direct_evidence_applied"])
                self.assertEqual(
                    fields["added_code_direct_evidence_error"],
                    "code_method_fingerprints_unavailable",
                )

    def test_does_not_apply_when_code_layer_is_not_selected(self) -> None:
        fields = build_added_code_direct_evidence_fields(
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            {"code_method_fingerprints": {"Lpayload/New;->x()V": "fp:x"}},
            ["resource"],
        )

        self.assertFalse(fields["added_code_direct_evidence_applied"])
        self.assertEqual(
            fields["added_code_direct_evidence_error"],
            "code_layer_not_selected",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

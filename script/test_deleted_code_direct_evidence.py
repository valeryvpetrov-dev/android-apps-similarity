#!/usr/bin/env python3
"""Direct evidence-only deleted-code method delta."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from deleted_code_direct_evidence import build_deleted_code_direct_evidence_fields


class TestDeletedCodeDirectEvidence(unittest.TestCase):
    def test_extracts_left_only_methods_and_namespace_prefixes(self) -> None:
        fields = build_deleted_code_direct_evidence_fields(
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                    "Llegacy/Removed;->x()V": "fp:x",
                    "Llegacy/Removed;->y()V": "fp:y",
                }
            },
            {
                "code_method_fingerprints": {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            },
            ["code"],
        )

        self.assertTrue(fields["deleted_code_direct_evidence_applied"])
        self.assertEqual(fields["deleted_code_direct_evidence_role"], "evidence_only")
        self.assertEqual(fields["deleted_code_direct_score_effect"], "none")
        self.assertFalse(fields["deleted_code_direct_score_included"])
        self.assertEqual(fields["deleted_code_direct_left_only_method_count"], 2)
        self.assertEqual(fields["deleted_code_direct_right_only_method_count"], 0)
        self.assertEqual(fields["deleted_code_direct_common_method_id_count"], 2)
        self.assertEqual(fields["deleted_code_direct_deleted_fingerprint_count"], 2)
        self.assertEqual(
            fields["deleted_code_direct_top_method_prefixes"],
            [{"prefix": "legacy.Removed", "count": 2}],
        )
        self.assertEqual(
            fields["deleted_code_direct_method_sample"],
            ["Llegacy/Removed;->x()V", "Llegacy/Removed;->y()V"],
        )

    def test_does_not_apply_without_left_only_methods(self) -> None:
        fields = build_deleted_code_direct_evidence_fields(
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            ["code"],
        )

        self.assertFalse(fields["deleted_code_direct_evidence_applied"])
        self.assertEqual(fields["deleted_code_direct_left_only_method_count"], 0)

    def test_does_not_apply_when_code_layer_is_not_selected(self) -> None:
        fields = build_deleted_code_direct_evidence_fields(
            {"code_method_fingerprints": {"Llegacy/Removed;->x()V": "fp:x"}},
            {"code_method_fingerprints": {"Lbase/A;->a()V": "fp:a"}},
            ["resource"],
        )

        self.assertFalse(fields["deleted_code_direct_evidence_applied"])
        self.assertEqual(
            fields["deleted_code_direct_evidence_error"],
            "code_layer_not_selected",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

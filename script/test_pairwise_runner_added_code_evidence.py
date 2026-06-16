#!/usr/bin/env python3
"""Tests for added-code evidence in the R_code_stats pairwise policy."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def touch_apk(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", "<manifest />")
        archive.writestr("classes.dex", b"dex\n035\0")


def _code_v4_bundle(tokens: set[str]) -> dict:
    return {
        "method_fingerprints": {
            "Lcom/example/{};->m()V".format(
                token.replace(":", "_").replace("/", "_")
            ): token
            for token in sorted(tokens)
        },
        "total_methods": len(tokens),
        "mode": "v4_shingled",
    }


def _resource_bundle(
    code_tokens: set[str],
    digests: set[tuple[str, str]],
    code_fingerprints: set[str] | None = None,
) -> dict:
    return {
        "mode": "quick",
        "code": code_tokens,
        "metadata": set(),
        "component": {},
        "resource": {"resource_digests": digests},
        "library": {},
        "code_v4_shingled": _code_v4_bundle(code_fingerprints or set()),
    }


def _shared_resource(count: int) -> set[tuple[str, str]]:
    return {
        ("res/layout/screen_{:02d}.xml".format(index), "digest-{:02d}".format(index))
        for index in range(count)
    }


class TestCodeStatsAddedCodeEvidencePolicy(unittest.TestCase):
    def _run_one_pair(self, bundle_a: dict, bundle_b: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            decoded_a = root / "decoded-a"
            decoded_b = root / "decoded-b"
            touch_apk(apk_a)
            touch_apk(apk_b)
            decoded_a.mkdir()
            decoded_b.mkdir()

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, resource]
    metric: jaccard
    threshold: 0.70
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {
                                    "app_id": "A",
                                    "apk_path": str(apk_a),
                                    "decoded_dir": str(decoded_a),
                                },
                                "app_b": {
                                    "app_id": "B",
                                    "apk_path": str(apk_b),
                                    "decoded_dir": str(decoded_b),
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[bundle_a, bundle_b],
            ):
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )
        return payload[0]

    def test_added_code_evidence_promotes_selected_score_with_resource_support(self) -> None:
        shared_code = {"method:shared_{:02d}".format(index) for index in range(50)}
        left_only_code = {"method:left_only_{:02d}".format(index) for index in range(10)}
        added_code = {"method:added_{:02d}".format(index) for index in range(30)}
        shared_resources = _shared_resource(10)

        result = self._run_one_pair(
            _resource_bundle(shared_code | left_only_code, shared_resources),
            _resource_bundle(shared_code | added_code, shared_resources),
        )

        self.assertEqual(result["status"], "success")
        self.assertFalse(result["code_stats_containment_policy_applied"])
        self.assertTrue(result["code_stats_added_code_policy_applied"])
        self.assertAlmostEqual(result["preserved_core_similarity"], 50 / 60)
        self.assertEqual(result["preserved_core_method_count"], 50)
        self.assertAlmostEqual(result["added_code_delta"], 30 / 80)
        self.assertAlmostEqual(result["added_code_resource_support_score"], 1.0)
        self.assertAlmostEqual(result["added_code_evidence_score"], 50 / 60)
        self.assertAlmostEqual(result["similarity_score"], 50 / 60)
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_added_code_with_resource_support",
        )
        self.assertEqual(result["payload_or_hook_hint"], "added_code_delta_candidate")
        self.assertEqual(result["permission_or_component_delta"], "not_extracted_current_profile")
        self.assertEqual(result["added_code_representation"], "code")

        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertAlmostEqual(
            evidence_refs[("added_code_evidence", "R_code_stats_added_code")],
            50 / 60,
        )
        self.assertAlmostEqual(
            evidence_refs[("resource_corroboration", "resource_path_digest")],
            1.0,
        )

    def test_added_code_without_resource_support_does_not_promote(self) -> None:
        shared_code = {"method:shared_{:02d}".format(index) for index in range(50)}
        left_only_code = {"method:left_only_{:02d}".format(index) for index in range(10)}
        added_code = {"method:added_{:02d}".format(index) for index in range(30)}
        left_resources = _shared_resource(10)
        right_resources = {
            ("res/layout/other_{:02d}.xml".format(index), "other-{:02d}".format(index))
            for index in range(10)
        }

        result = self._run_one_pair(
            _resource_bundle(shared_code | left_only_code, left_resources),
            _resource_bundle(shared_code | added_code, right_resources),
        )

        self.assertEqual(result["status"], "low_similarity")
        self.assertFalse(result["code_stats_added_code_policy_applied"])
        self.assertAlmostEqual(result["preserved_core_similarity"], 50 / 60)
        self.assertAlmostEqual(result["added_code_resource_support_score"], 0.0)
        self.assertLess(result["similarity_score"], 0.70)

    def test_added_code_uses_method_fingerprint_core_when_code_tokens_are_coarse(self) -> None:
        shared_code = {"fp:shared_{:02d}".format(index) for index in range(50)}
        left_only_code = {"fp:left_only_{:02d}".format(index) for index in range(10)}
        added_code = {"fp:added_{:02d}".format(index) for index in range(30)}
        shared_resources = _shared_resource(7)
        left_resources = shared_resources | {
            ("res/layout/left_only_{:02d}.xml".format(index), "left-{:02d}".format(index))
            for index in range(2)
        }
        right_resources = shared_resources | {
            ("res/layout/right_only_{:02d}.xml".format(index), "right-{:02d}".format(index))
            for index in range(2)
        }

        result = self._run_one_pair(
            _resource_bundle(set(), left_resources, shared_code | left_only_code),
            _resource_bundle(set(), right_resources, shared_code | added_code),
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["code_stats_added_code_policy_applied"])
        self.assertEqual(result["added_code_representation"], "code_fingerprint")
        self.assertAlmostEqual(result["preserved_core_similarity"], 50 / 60)
        self.assertEqual(result["preserved_core_method_count"], 50)
        self.assertAlmostEqual(result["added_code_delta"], 30 / 80)
        self.assertAlmostEqual(result["added_code_resource_support_score"], 7 / 9)
        self.assertAlmostEqual(result["added_code_evidence_score"], 7 / 9)
        self.assertAlmostEqual(result["similarity_score"], 7 / 9)
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_added_code_with_resource_support",
        )


if __name__ == "__main__":
    unittest.main()

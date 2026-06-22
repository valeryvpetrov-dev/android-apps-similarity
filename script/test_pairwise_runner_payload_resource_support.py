#!/usr/bin/env python3
"""Tests for payload/resource supported pairwise scoring."""

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
            "Lcom/example/{};->m()V".format(token.replace(":", "_")): token
            for token in sorted(tokens)
        },
        "total_methods": len(tokens),
        "mode": "v4_shingled",
    }


def _code_v4_bundle_from_pairs(pairs: dict[str, str]) -> dict:
    return {
        "method_fingerprints": dict(pairs),
        "total_methods": len(pairs),
        "mode": "v4_shingled",
    }


def _resource_bundle(
    code_fingerprints: set[str],
    resource_digests: set[tuple[str, str]],
) -> dict:
    return {
        "mode": "quick",
        "code": set(),
        "metadata": set(),
        "component": {},
        "resource": {"resource_digests": resource_digests},
        "library": {},
        "code_v4_shingled": _code_v4_bundle(code_fingerprints),
    }


def _resource_bundle_from_pairs(
    method_fingerprints: dict[str, str],
    resource_digests: set[tuple[str, str]],
) -> dict:
    return {
        "mode": "quick",
        "code": set(),
        "metadata": set(),
        "component": {},
        "resource": {"resource_digests": resource_digests},
        "library": {},
        "code_v4_shingled": _code_v4_bundle_from_pairs(method_fingerprints),
    }


class TestPayloadResourceSupportPolicy(unittest.TestCase):
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

    def test_code_and_resource_support_promotes_payload_like_pair(self) -> None:
        shared_code = {"fp:shared_{:04d}".format(index) for index in range(64)}
        left_only_code = {"fp:left_{:04d}".format(index) for index in range(22)}
        right_only_code = {"fp:right_{:04d}".format(index) for index in range(336)}
        shared_resources = {
            ("res/layout/shared_{:02d}.xml".format(index), "shared-{:02d}".format(index))
            for index in range(13)
        }
        left_resources = shared_resources | {
            ("res/layout/left_{:02d}.xml".format(index), "left-{:02d}".format(index))
            for index in range(5)
        }
        right_resources = shared_resources | {
            ("res/layout/right_{:02d}.xml".format(index), "right-{:02d}".format(index))
            for index in range(5)
        }

        result = self._run_one_pair(
            _resource_bundle(shared_code | left_only_code, left_resources),
            _resource_bundle(shared_code | right_only_code, right_resources),
        )

        self.assertEqual(result["status"], "success")
        self.assertFalse(result["code_stats_added_code_policy_applied"])
        self.assertFalse(result["code_stats_repack_core_policy_applied"])
        self.assertTrue(result["code_stats_payload_resource_policy_applied"])
        self.assertAlmostEqual(result["payload_resource_code_similarity"], 64 / 86)
        self.assertEqual(result["payload_resource_method_count"], 64)
        self.assertAlmostEqual(result["payload_resource_added_code_delta"], 336 / 400)
        self.assertAlmostEqual(result["payload_resource_support_score"], 13 / 18)
        self.assertAlmostEqual(
            result["payload_resource_score"],
            ((64 / 86) + (13 / 18)) / 2,
        )
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_payload_resource_support",
        )
        self.assertAlmostEqual(
            result["similarity_score"],
            ((64 / 86) + (13 / 18)) / 2,
        )

        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertAlmostEqual(
            evidence_refs[("payload_resource_support", "R_code_stats_payload_resource")],
            ((64 / 86) + (13 / 18)) / 2,
        )

    def test_strict_resource_bridge_promotes_moderate_code_payload_pair(self) -> None:
        shared_exact = {
            "Lcom/example/Shared{:03d};->m()V".format(index): "fp:shared_{:03d}".format(index)
            for index in range(52)
        }
        left_renamed = {
            "Lcom/example/LeftRenamed{:03d};->m()V".format(index): "fp:renamed_{:03d}".format(index)
            for index in range(14)
        }
        right_renamed = {
            "Lcom/example/RightRenamed{:03d};->m()V".format(index): "fp:renamed_{:03d}".format(index)
            for index in range(14)
        }
        left_only = {
            "Lcom/example/LeftOnly{:03d};->m()V".format(index): "fp:left_{:03d}".format(index)
            for index in range(44)
        }
        right_only = {
            "Lcom/example/RightOnly{:03d};->m()V".format(index): "fp:right_{:03d}".format(index)
            for index in range(44)
        }
        shared_resources = {
            ("res/layout/shared_{:02d}.xml".format(index), "shared-{:02d}".format(index))
            for index in range(19)
        }
        left_resources = shared_resources | {
            ("res/layout/left_{:02d}.xml".format(index), "left-{:02d}".format(index))
            for index in range(2)
        }
        right_resources = shared_resources | {
            ("res/layout/right_{:02d}.xml".format(index), "right-{:02d}".format(index))
            for index in range(81)
        }

        result = self._run_one_pair(
            _resource_bundle_from_pairs(
                shared_exact | left_renamed | left_only,
                left_resources,
            ),
            _resource_bundle_from_pairs(
                shared_exact | right_renamed | right_only,
                right_resources,
            ),
        )

        exact_code_similarity = 52 / 110
        resource_support = 19 / 21
        bridge_score = (exact_code_similarity + 2 * resource_support) / 3

        self.assertEqual(result["status"], "success")
        self.assertFalse(result["code_stats_payload_resource_policy_applied"])
        self.assertTrue(result["code_stats_payload_resource_bridge_policy_applied"])
        self.assertAlmostEqual(
            result["payload_resource_code_similarity"],
            exact_code_similarity,
        )
        self.assertAlmostEqual(
            result["payload_resource_fp_counter_containment"],
            66 / 110,
        )
        self.assertAlmostEqual(
            result["payload_resource_support_score"],
            resource_support,
        )
        self.assertAlmostEqual(result["payload_resource_bridge_score"], bridge_score)
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_payload_resource_bridge",
        )
        self.assertAlmostEqual(result["similarity_score"], bridge_score)

        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertAlmostEqual(
            evidence_refs[
                (
                    "payload_resource_bridge_support",
                    "R_code_stats_payload_resource_bridge",
                )
            ],
            bridge_score,
        )

    def test_strict_resource_bridge_requires_strong_resource_support(self) -> None:
        shared_exact = {
            "Lcom/example/Shared{:03d};->m()V".format(index): "fp:shared_{:03d}".format(index)
            for index in range(52)
        }
        left_renamed = {
            "Lcom/example/LeftRenamed{:03d};->m()V".format(index): "fp:renamed_{:03d}".format(index)
            for index in range(14)
        }
        right_renamed = {
            "Lcom/example/RightRenamed{:03d};->m()V".format(index): "fp:renamed_{:03d}".format(index)
            for index in range(14)
        }
        left_only = {
            "Lcom/example/LeftOnly{:03d};->m()V".format(index): "fp:left_{:03d}".format(index)
            for index in range(44)
        }
        right_only = {
            "Lcom/example/RightOnly{:03d};->m()V".format(index): "fp:right_{:03d}".format(index)
            for index in range(44)
        }
        shared_resources = {
            ("res/layout/shared_{:02d}.xml".format(index), "shared-{:02d}".format(index))
            for index in range(16)
        }
        left_resources = shared_resources | {
            ("res/layout/left_{:02d}.xml".format(index), "left-{:02d}".format(index))
            for index in range(5)
        }
        right_resources = shared_resources | {
            ("res/layout/right_{:02d}.xml".format(index), "right-{:02d}".format(index))
            for index in range(5)
        }

        result = self._run_one_pair(
            _resource_bundle_from_pairs(
                shared_exact | left_renamed | left_only,
                left_resources,
            ),
            _resource_bundle_from_pairs(
                shared_exact | right_renamed | right_only,
                right_resources,
            ),
        )

        self.assertFalse(result["code_stats_payload_resource_policy_applied"])
        self.assertFalse(result["code_stats_payload_resource_bridge_policy_applied"])
        self.assertAlmostEqual(result["payload_resource_support_score"], 16 / 21)
        self.assertNotEqual(
            result["similarity_score_source"],
            "code_stats_payload_resource_bridge",
        )


if __name__ == "__main__":
    unittest.main()

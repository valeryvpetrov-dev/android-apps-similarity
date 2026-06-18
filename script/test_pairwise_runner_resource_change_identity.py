#!/usr/bin/env python3
"""Tests for resource-change tolerant code identity in pairwise scoring."""

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


class TestResourceChangeIdentityPolicy(unittest.TestCase):
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

    def test_identical_code_fingerprint_promotes_despite_resource_changes(self) -> None:
        shared_code = {"fp:shared_{:02d}".format(index) for index in range(12)}
        left_resources = {
            ("res/layout/main.xml", "old-layout"),
            ("res/drawable/icon.png", "old-icon"),
            ("res/values/strings.xml", "shared-strings"),
        }
        right_resources = {
            ("res/layout/main.xml", "new-layout"),
            ("res/drawable/icon.png", "new-icon"),
            ("res/values/strings.xml", "shared-strings"),
        }

        result = self._run_one_pair(
            _resource_bundle(shared_code, left_resources),
            _resource_bundle(shared_code, right_resources),
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(
            result["code_stats_resource_change_identity_policy_applied"]
        )
        self.assertAlmostEqual(result["resource_change_identity_score"], 1.0)
        self.assertAlmostEqual(
            result["resource_change_identity_resource_support_score"],
            1 / 3,
        )
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_resource_change_tolerant_code_identity",
        )
        self.assertAlmostEqual(result["similarity_score"], 1.0)
        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertAlmostEqual(
            evidence_refs[
                (
                    "resource_change_tolerant_code_identity",
                    "R_code_stats_resource_change_identity",
                )
            ],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()

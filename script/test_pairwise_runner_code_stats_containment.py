#!/usr/bin/env python3
"""Tests for code stats containment score promotion in pairwise runner."""

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


def _resource_bundle(code_tokens: set[str], digests: set[tuple[str, str]]) -> dict:
    return {
        "mode": "quick",
        "code": code_tokens,
        "metadata": set(),
        "component": {},
        "resource": {"resource_digests": digests},
        "library": {},
    }


class TestCodeStatsContainmentPolicy(unittest.TestCase):
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

    def test_containment_with_resource_digest_support_promotes_selected_score(self) -> None:
        bundle_a = _resource_bundle(
            code_tokens={"method:core_a", "method:core_b"},
            digests={
                ("res/layout/main.xml", "digest-main"),
                ("res/drawable/icon.png", "digest-icon"),
            },
        )
        bundle_b = _resource_bundle(
            code_tokens={
                "method:core_a",
                "method:core_b",
                "method:extra_a",
                "method:extra_b",
            },
            digests={
                ("res/layout/main.xml", "digest-main"),
                ("res/drawable/icon.png", "digest-icon"),
            },
        )

        result = self._run_one_pair(bundle_a, bundle_b)

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["full_similarity_score"], 4 / 6)
        self.assertAlmostEqual(result["library_reduced_score"], 4 / 6)
        self.assertAlmostEqual(result["similarity_score"], 1.0)
        self.assertEqual(
            result["similarity_score_source"],
            "code_stats_containment_with_resource_corroboration",
        )
        self.assertTrue(result["code_stats_containment_policy_applied"])
        self.assertAlmostEqual(result["code_stats_containment_score"], 1.0)
        self.assertAlmostEqual(result["code_stats_resource_corroboration_score"], 1.0)
        self.assertEqual(result["code_stats_containment_direction"], "a_in_b")
        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertAlmostEqual(
            evidence_refs[
                ("code_stats_containment", "R_code_stats_containment")
            ],
            1.0,
        )
        self.assertAlmostEqual(
            evidence_refs[
                ("resource_corroboration", "resource_path_digest")
            ],
            1.0,
        )

    def test_containment_without_resource_digest_support_does_not_promote(self) -> None:
        bundle_a = _resource_bundle(
            code_tokens={"method:core_a", "method:core_b"},
            digests={
                ("res/layout/main.xml", "digest-main"),
                ("res/drawable/icon.png", "digest-icon"),
            },
        )
        bundle_b = _resource_bundle(
            code_tokens={
                "method:core_a",
                "method:core_b",
                "method:extra_a",
                "method:extra_b",
            },
            digests={
                ("res/layout/main.xml", "digest-other"),
                ("res/drawable/icon.png", "digest-other"),
            },
        )

        result = self._run_one_pair(bundle_a, bundle_b)

        self.assertEqual(result["status"], "low_similarity")
        self.assertLess(result["similarity_score"], 0.70)
        self.assertFalse(result["code_stats_containment_policy_applied"])
        self.assertAlmostEqual(result["code_stats_containment_score"], 1.0)
        self.assertAlmostEqual(result["code_stats_resource_corroboration_score"], 0.0)

    def test_detailed_scores_preserve_explicit_containment_selected_score(self) -> None:
        scores = pairwise_runner.build_detailed_scores(
            {
                "full_similarity_score": 4 / 6,
                "library_reduced_score": 4 / 6,
                "similarity_score": 1.0,
                "similarity_score_source": (
                    "code_stats_containment_with_resource_corroboration"
                ),
            },
            "success",
        )

        self.assertAlmostEqual(scores["similarity_score"], 1.0)
        self.assertAlmostEqual(scores["selected_similarity_score"], 1.0)
        self.assertAlmostEqual(scores["full_similarity_score"], 4 / 6)
        self.assertAlmostEqual(scores["library_reduced_score"], 4 / 6)
        self.assertEqual(
            scores["similarity_score_source"],
            "code_stats_containment_with_resource_corroboration",
        )


if __name__ == "__main__":
    unittest.main()

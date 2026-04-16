#!/usr/bin/env python3
"""Tests for pairwise_runner enhanced decoded-layer path."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


class TestPairwiseRunnerEnhanced(unittest.TestCase):
    def test_run_pairwise_uses_decoded_layers_for_non_code_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource, library]
    metric: cosine
    threshold: 0.10
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
                                    "decoded_dir": "/tmp/decoded-a",
                                },
                                "app_b": {
                                    "app_id": "B",
                                    "apk_path": str(apk_b),
                                    "decoded_dir": "/tmp/decoded-b",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {"androidx.appcompat": {"class_count": 10}},
                },
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ) as features_mock:
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        self.assertEqual(features_mock.call_count, 2)
        result = payload[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["views_used"], ["component", "resource", "library"])
        self.assertAlmostEqual(result["full_similarity_score"], 1.0)
        self.assertAlmostEqual(result["library_reduced_score"], 1.0)

    def test_run_pairwise_fails_when_decoded_layers_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = pairwise_runner.run_pairwise(
                config_path=config_path,
                enriched_path=enriched_path,
                ins_block_sim_threshold=0.8,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )

        result = payload[0]
        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["full_similarity_score"])
        self.assertIsNone(result["library_reduced_score"])

    def test_run_pairwise_discovers_shared_apk_and_decoded_dirs_by_app_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shared_root = root / "shared"
            apk_dir = shared_root / "datasets" / "fdroid-corpus-v2-apks"
            decoded_root = shared_root / "datasets" / "fdroid-corpus-v2-decoded"
            apk_dir.mkdir(parents=True)
            decoded_root.mkdir(parents=True)

            apk_a = apk_dir / "app.alpha.apk"
            apk_b = apk_dir / "app.beta.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            (decoded_root / "app.alpha").mkdir()
            (decoded_root / "app.beta").mkdir()
            (decoded_root / "app.alpha" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
            (decoded_root / "app.beta" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")

            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "app.alpha"},
                                "app_b": {"app_id": "app.beta"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {},
                },
            }

            with mock.patch.dict("os.environ", {"PHD_SHARED_DATA_ROOT": str(shared_root)}), mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ):
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        result = payload[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["views_used"], ["component", "resource"])


if __name__ == "__main__":
    unittest.main()

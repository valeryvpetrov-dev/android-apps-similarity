#!/usr/bin/env python3
"""Direct library-noise evidence in pairwise result."""
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


def _feature_bundle(method_fingerprints: dict[str, str]) -> dict:
    return {
        "mode": "quick",
        "code": set(method_fingerprints),
        "metadata": set(),
        "component": {},
        "resource": {"resource_digests": {("res/layout/main.xml", "digest-main")}},
        "library": {},
        "code_v4_shingled": {
            "method_fingerprints": dict(method_fingerprints),
            "total_methods": len(method_fingerprints),
            "mode": "v4_shingled",
        },
    }


def _build_enriched_pair_file(root: Path, apk_a: Path, apk_b: Path) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    write_text(
        config_path,
        """
stages:
  pairwise:
    features: [code, resource]
    metric: jaccard
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
    return config_path, enriched_path


class TestPairwiseLibraryNoiseDirectEvidence(unittest.TestCase):
    def test_run_pairwise_adds_direct_library_noise_without_score_effect(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

            bundle_a = _feature_bundle(
                {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                }
            )
            bundle_b = _feature_bundle(
                {
                    "Lbase/A;->a()V": "fp:a",
                    "Lbase/B;->b()V": "fp:b",
                    "Lcom/vendor/sdk/Ad;->load()V": "fp:ad-load",
                    "Lcom/vendor/sdk/Ad;->show()V": "fp:ad-show",
                }
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

        row = payload[0]
        self.assertEqual(row["status"], "success")
        self.assertTrue(row["library_noise_direct_evidence_applied"])
        self.assertEqual(row["library_noise_direct_evidence_role"], "evidence_only")
        self.assertEqual(row["library_noise_direct_score_effect"], "none")
        self.assertFalse(row["library_noise_direct_score_included"])
        self.assertEqual(row["library_noise_direct_right_only_method_count"], 2)
        self.assertEqual(row["library_noise_direct_common_method_id_count"], 2)
        self.assertEqual(
            row["library_noise_direct_top_namespace_prefix"],
            "com.vendor.sdk",
        )
        self.assertNotEqual(row["similarity_score_source"], "library_noise")

        library_noise_items = [
            item for item in row["evidence"] if item["signal_type"] == "library_noise"
        ]
        self.assertEqual(len(library_noise_items), 1)
        self.assertEqual(library_noise_items[0]["score_effect"], "none")
        self.assertFalse(library_noise_items[0]["score_included"])
        self.assertEqual(
            library_noise_items[0]["top_namespace_prefix"],
            "com.vendor.sdk",
        )
        self.assertEqual(
            library_noise_items[0]["method_sample"],
            [
                "Lcom/vendor/sdk/Ad;->load()V",
                "Lcom/vendor/sdk/Ad;->show()V",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Direct obfuscation evidence in pairwise result."""
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


class TestPairwiseObfuscationDirectEvidence(unittest.TestCase):
    def test_run_pairwise_adds_direct_obfuscation_without_score_effect(
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
                    "Lcom/example/app/Feature00;-><init>()V": "fp-init-0",
                    "Lcom/example/app/Feature00;->compute00()I": "fp-compute-0",
                    "Lcom/example/app/Feature01;-><init>()V": "fp-init-1",
                    "Lcom/example/app/Feature01;->compute01()I": "fp-compute-1",
                }
            )
            bundle_b = _feature_bundle(
                {
                    "Lcom/example/app/a;-><init>()V": "fp-init-0",
                    "Lcom/example/app/a;->a()I": "fp-compute-0",
                    "Lcom/example/app/b;-><init>()V": "fp-init-1",
                    "Lcom/example/app/b;->b()I": "fp-compute-1",
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
        self.assertTrue(row["obfuscation_direct_evidence_applied"])
        self.assertEqual(row["obfuscation_direct_evidence_role"], "evidence_only")
        self.assertEqual(row["obfuscation_direct_score_effect"], "none")
        self.assertFalse(row["obfuscation_direct_score_included"])
        self.assertTrue(row["obfuscation_direct_same_package"])
        self.assertEqual(row["obfuscation_direct_common_fingerprint_count"], 4)
        self.assertEqual(row["obfuscation_direct_right_class_name_sample"], ["a", "b"])
        self.assertEqual(row["obfuscation_direct_right_method_name_sample"], ["a", "b"])
        self.assertNotEqual(
            row["similarity_score_source"],
            "obfuscation_direct_evidence",
        )

        obfuscation_items = [
            item
            for item in row["evidence"]
            if item["signal_type"] == "obfuscation_direct_evidence"
        ]
        self.assertEqual(len(obfuscation_items), 1)
        self.assertEqual(obfuscation_items[0]["score_effect"], "none")
        self.assertFalse(obfuscation_items[0]["score_included"])
        self.assertTrue(obfuscation_items[0]["same_package"])
        self.assertEqual(obfuscation_items[0]["common_fingerprint_count"], 4)
        self.assertEqual(obfuscation_items[0]["right_class_name_sample"], ["a", "b"])
        self.assertEqual(obfuscation_items[0]["right_method_name_sample"], ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

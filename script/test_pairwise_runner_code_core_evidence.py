#!/usr/bin/env python3
"""Code core evidence in pairwise result."""
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


def write_apk(path: Path, *, package_name: str) -> None:
    manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="{}"><application /></manifest>'
    ).format(package_name)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest.encode("utf-8"))
        archive.writestr("classes.dex", b"dex\n035\0")


def _make_feature_bundle(side_only: str) -> dict:
    return {
        "mode": "enhanced",
        "code": {"shared.code"},
        "metadata": set(),
        "component": {
            "activities": [{"name": ".MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        },
        "resource": {"resource_digests": {("res/layout/main.xml", "digest-1")}},
        "library": {"libraries": {"androidx.appcompat": {"class_count": 10}}},
        "signing": {"hash": None},
        "code_v4_shingled": {
            "method_fingerprints": {
                f"Lcom/example/{side_only};->a()V": f"fp:side_only:{side_only}",
                f"Lcom/example/{side_only};->b()V": "fp:shared_1",
                f"Lcom/example/{side_only};->c()V": "fp:shared_2",
            }
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


class TestPairwiseCodeCoreEvidence(unittest.TestCase):
    def test_run_pairwise_adds_same_code_core_evidence_without_score_effect(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            write_apk(apk_a, package_name="com.example.left")
            write_apk(apk_b, package_name="com.example.right")
            config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[
                    _make_feature_bundle("Left"),
                    _make_feature_bundle("Right"),
                ],
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
        self.assertTrue(row["code_core_evidence_applied"])
        self.assertEqual(row["code_core_evidence_role"], "evidence_only")
        self.assertEqual(row["code_core_score_effect"], "none")
        self.assertFalse(row["code_core_score_included"])
        self.assertEqual(row["code_core_common_fingerprint_count"], 2)
        self.assertEqual(
            row["code_core_common_fingerprint_sample"],
            ["fp:shared_1", "fp:shared_2"],
        )
        self.assertEqual(row["similarity_score_source"], "library_reduced_score")

        code_core_items = [
            item for item in row["evidence"] if item["signal_type"] == "same_code_core"
        ]
        self.assertEqual(len(code_core_items), 1)
        self.assertEqual(code_core_items[0]["score_effect"], "none")
        self.assertEqual(code_core_items[0]["common_fingerprint_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

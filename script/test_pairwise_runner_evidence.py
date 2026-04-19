#!/usr/bin/env python3
"""EXEC-088: evidence блок в pair_row run_pairwise.

`run_pairwise` для успешной пары должен возвращать pair_row с
непустым списком `evidence`: per-layer Evidence для каждого слоя
в views_used плюс одна signature_match Evidence.
"""
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


def _make_feature_bundle() -> dict:
    return {
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
        "resource": {"resource_digests": {("res/layout/main.xml", "digest-1")}},
        "library": {"libraries": {"androidx.appcompat": {"class_count": 10}}},
        "signing": {"hash": None},
    }


def _build_enriched_pair_file(root: Path, apk_a: Path, apk_b: Path) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
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
    return config_path, enriched_path


class TestEvidenceInPairRow(unittest.TestCase):

    def _run(self, hash_a: str | None, hash_b: str | None) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

            def fake_extract(path):
                return {str(apk_a): hash_a, str(apk_b): hash_b}.get(str(path))

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[_make_feature_bundle(), _make_feature_bundle()],
            ), mock.patch.object(
                pairwise_runner,
                "extract_apk_signature_hash",
                side_effect=fake_extract,
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

    def test_success_pair_has_non_empty_evidence(self) -> None:
        row = self._run("a" * 64, "a" * 64)
        self.assertEqual(row["status"], "success")
        self.assertIn("evidence", row)
        self.assertIsInstance(row["evidence"], list)
        self.assertGreater(len(row["evidence"]), 0)

    def test_evidence_contains_layer_score_per_view(self) -> None:
        row = self._run("a" * 64, "a" * 64)
        layer_refs = {
            item["ref"]
            for item in row["evidence"]
            if item["signal_type"] == "layer_score"
        }
        self.assertEqual(layer_refs, set(row["views_used"]))

    def test_evidence_contains_signature_match_on_match(self) -> None:
        row = self._run("a" * 64, "a" * 64)
        sig_items = [
            item
            for item in row["evidence"]
            if item["signal_type"] == "signature_match"
        ]
        self.assertEqual(len(sig_items), 1)
        self.assertEqual(sig_items[0]["source_stage"], "signing")
        self.assertEqual(sig_items[0]["ref"], "apk_signature")
        self.assertEqual(sig_items[0]["magnitude"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

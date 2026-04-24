#!/usr/bin/env python3
"""EXEC-089: signature_match в pair_row run_pairwise.

`run_pairwise` должен после успешного анализа пары возвращать
явный signature_match блок `{score, status}`, соответствующий
результату compare_signatures на двух APK.
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


class TestSignatureMatchInPairRow(unittest.TestCase):

    def _run_with_hashes(self, hash_a, hash_b):
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

    def test_match_when_both_hashes_equal(self):
        result = self._run_with_hashes("a" * 64, "a" * 64)
        self.assertEqual(result["status"], "success")
        self.assertIn("signature_match", result)
        self.assertEqual(result["signature_match"]["status"], "match")
        self.assertEqual(result["signature_match"]["score"], 1.0)

    def test_mismatch_when_hashes_differ(self):
        result = self._run_with_hashes("a" * 64, "b" * 64)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["signature_match"]["status"], "mismatch")
        self.assertEqual(result["signature_match"]["score"], 0.0)

    def test_missing_when_one_hash_is_none(self):
        result = self._run_with_hashes("a" * 64, None)
        self.assertEqual(result["signature_match"]["status"], "missing")
        self.assertEqual(result["signature_match"]["score"], 0.0)

    def test_missing_when_both_hashes_are_none(self):
        # DEEP-20-BOTH-EMPTY-AUDIT: единая семантика both_empty.
        # Оба хеша None → status='both_missing', both_empty=True
        # (ранее был 'missing' — общий статус для one_empty/both_empty,
        # что не давало downstream исключить слой из агрегации).
        result = self._run_with_hashes(None, None)
        self.assertEqual(
            result["signature_match"]["status"], "both_missing",
        )
        self.assertIs(
            result["signature_match"].get("both_empty"), True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for framework-shift evidence in pairwise runner."""

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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _anchor_words(count: int) -> list[str]:
    return ["brandanchor{:03d}".format(index) for index in range(count)]


def _write_framework_shift_decoded_dirs(
    decoded_a: Path,
    decoded_b: Path,
    *,
    package_a: str = "com.example.same",
    package_b: str = "com.example.same",
) -> None:
    anchors = _anchor_words(60)
    write_text(
        decoded_a / "AndroidManifest.xml",
        '<manifest package="{}" />'.format(package_a),
    )
    write_text(
        decoded_b / "AndroidManifest.xml",
        '<manifest package="{}" />'.format(package_b),
    )
    write_text(
        decoded_a / "assets/www/index.html",
        "<html>{}</html>".format(" ".join(anchors)),
    )
    write_text(
        decoded_a / "smali/org/apache/cordova/CordovaActivity.smali",
        '.class public Lorg/apache/cordova/CordovaActivity;\n',
    )
    write_text(
        decoded_b / "res/layout/main.xml",
        "<LinearLayout>{}</LinearLayout>".format(" ".join(anchors)),
    )


class TestFrameworkShiftEvidencePolicy(unittest.TestCase):
    def _run_one_pair(
        self,
        bundle_a: dict,
        bundle_b: dict,
        *,
        package_a: str = "com.example.same",
        package_b: str = "com.example.same",
    ) -> dict:
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
            _write_framework_shift_decoded_dirs(
                decoded_a,
                decoded_b,
                package_a=package_a,
                package_b=package_b,
            )

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

    def test_framework_shift_evidence_is_evidence_only_and_does_not_promote_score(self) -> None:
        result = self._run_one_pair(
            _resource_bundle(
                {"method:left_only"},
                {("assets/www/index.html", "left-digest")},
            ),
            _resource_bundle(
                {"method:right_only"},
                {("res/layout/main.xml", "right-digest")},
            ),
        )

        self.assertEqual(result["status"], "low_similarity")
        self.assertAlmostEqual(result["similarity_score"], 0.0)
        self.assertEqual(result["similarity_score_source"], "library_reduced_score")
        self.assertTrue(result["framework_shift_evidence_applied"])
        self.assertEqual(result["framework_shift_evidence_role"], "evidence_only")
        self.assertEqual(result["framework_shift_common_anchor_count"], 60)
        self.assertGreaterEqual(result["framework_shift_anchor_containment"], 0.10)

        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertIn(
            ("framework_shift_evidence", "R_framework_shift_anchors"),
            evidence_refs,
        )

    def test_framework_shift_evidence_requires_package_guard(self) -> None:
        result = self._run_one_pair(
            _resource_bundle({"method:left_only"}, set()),
            _resource_bundle({"method:right_only"}, set()),
            package_a="com.example.left",
            package_b="com.example.right",
        )

        self.assertFalse(result["framework_shift_evidence_applied"])
        evidence_refs = {
            (item["signal_type"], item["ref"])
            for item in result["evidence"]
        }
        self.assertNotIn(
            ("framework_shift_evidence", "R_framework_shift_anchors"),
            evidence_refs,
        )


if __name__ == "__main__":
    unittest.main()

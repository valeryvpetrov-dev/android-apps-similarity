#!/usr/bin/env python3
"""Tests for C05 static evidence in pairwise analysis."""

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


def touch_apk(path: Path, extra_entries: tuple[str, ...] = ()) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", "<manifest />")
        archive.writestr("classes.dex", b"dex\n035\0")
        for entry in extra_entries:
            archive.writestr(entry, b"payload")


def _bundle(
    code_tokens: set[str],
    component_features: dict,
    resource_digests: set[tuple[str, str]],
    libraries: set[str] | None = None,
) -> dict:
    return {
        "mode": "quick",
        "code": code_tokens,
        "metadata": set(),
        "component": component_features,
        "resource": {"resource_digests": resource_digests},
        "library": {
            "libraries": {library_id: {"match_type": "package_prefix"} for library_id in (libraries or set())}
        },
    }


class TestC05StaticEvidencePolicy(unittest.TestCase):
    _RAW_RELATION_FIELDS = {
        "c05_static_relation_score",
        "c05_static_code_namespace_overlap",
        "c05_static_component_namespace_overlap",
        "c05_static_code_token_containment",
        "c05_static_relation_namespace_sample",
    }

    def test_c05_static_namespace_prefixes_drop_platform_dalvik_prefixes(self) -> None:
        prefixes = pairwise_runner._c05_static_namespace_prefixes(
            {
                "Landroid/view/View;->draw(Landroid/graphics/Canvas;)V",
                "ILandroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;",
                "Ljava/lang/String;->length()I",
                "CILjava/lang/StringBuilder;->append(Ljava/lang/String;)V",
                "Lcom/payload/Stage;->run()V",
            }
        )

        self.assertIn("com.payload", prefixes)
        self.assertNotIn("Landroid.view", prefixes)
        self.assertNotIn("ILandroid.os", prefixes)
        self.assertNotIn("Ljava.lang", prefixes)
        self.assertNotIn("CILjava.lang", prefixes)

    def _run_one_pair(
        self,
        bundle_a: dict,
        bundle_b: dict,
        extra_apk_b_entries: tuple[str, ...] = (),
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
            touch_apk(apk_b, extra_apk_b_entries)
            decoded_a.mkdir()
            decoded_b.mkdir()

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, component, resource]
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

    def test_c05_static_high_evidence_stays_evidence_only(self) -> None:
        left_components = {
            "activities": [{"name": "com.example.MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        }
        right_components = {
            "activities": [{"name": "com.example.MainActivity"}],
            "services": [{"name": "com.example.PayloadService"}],
            "receivers": [],
            "providers": [],
            "permissions": {
                "android.permission.INTERNET",
                "android.permission.SEND_SMS",
            },
            "features": set(),
        }

        result = self._run_one_pair(
            _bundle(
                code_tokens={"com.example.MainActivity:onCreate", "left.core"},
                component_features=left_components,
                resource_digests={("res/layout/main.xml", "left-digest")},
                libraries={"androidx.appcompat"},
            ),
            _bundle(
                code_tokens={
                    "com.example.MainActivity:onCreate",
                    "payload.a",
                    "payload.b",
                    "payload.c",
                    "payload.d",
                    "payload.e",
                    "payload.f",
                },
                component_features=right_components,
                resource_digests={("res/layout/main.xml", "right-digest")},
                libraries={"androidx.appcompat", "com.payload.sdk"},
            ),
            extra_apk_b_entries=(
                "classes2.dex",
                "lib/armeabi-v7a/libpayload.so",
            ),
        )

        self.assertEqual(result["status"], "low_similarity")
        self.assertTrue(result["c05_static_evidence_applied"])
        self.assertEqual(result["c05_static_evidence_role"], "evidence_only")
        self.assertEqual(result["c05_static_evidence_score_effect"], "none")
        self.assertFalse(result["c05_static_evidence_score_included"])
        self.assertGreater(result["c05_static_component_delta_count"], 0)
        self.assertEqual(result["c05_static_permission_delta_count"], 1)
        self.assertEqual(result["c05_static_extra_dex_delta_count"], 1)
        self.assertEqual(result["c05_static_native_lib_delta_count"], 1)
        self.assertGreater(result["c05_static_library_delta_count"], 0)
        self.assertNotEqual(
            result.get("similarity_score_source"),
            "c05_static_manifest_relation_high_score",
        )
        self.assertFalse(
            any(key.startswith("c05_static_score_") for key in result),
            msg="quarantined C05 score-policy fields must not reach pair output",
        )
        self.assertTrue(self._RAW_RELATION_FIELDS.isdisjoint(result))
        self.assertLess(result["similarity_score"], 0.70)

        evidence_refs = {
            (item["signal_type"], item["ref"]): item["magnitude"]
            for item in result["evidence"]
        }
        self.assertIn(
            ("c05_static_evidence", "R_c05_static_evidence"),
            evidence_refs,
        )
        evidence_record = next(
            item
            for item in result["evidence"]
            if item["signal_type"] == "c05_static_evidence"
        )
        self.assertEqual(evidence_record["evidence_role"], "evidence_only")
        self.assertEqual(evidence_record["score_effect"], "none")
        self.assertFalse(evidence_record["score_included"])
        self.assertFalse(evidence_record["relation_inferred"])

    def test_c05_static_container_only_evidence_does_not_promote_score(self) -> None:
        components = {
            "activities": [{"name": "com.example.MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        }

        result = self._run_one_pair(
            _bundle(
                code_tokens={"com.example.MainActivity:onCreate"},
                component_features=components,
                resource_digests={("res/layout/main.xml", "left-digest")},
            ),
            _bundle(
                code_tokens={
                    "com.example.MainActivity:onCreate",
                    "payload.a",
                    "payload.b",
                    "payload.c",
                    "payload.d",
                    "payload.e",
                    "payload.f",
                },
                component_features=components,
                resource_digests={("res/layout/main.xml", "right-digest")},
            ),
            extra_apk_b_entries=("classes2.dex",),
        )

        self.assertEqual(result["status"], "low_similarity")
        self.assertTrue(result["c05_static_evidence_applied"])
        self.assertEqual(result["c05_static_manifest_delta_count"], 0)
        self.assertEqual(result["c05_static_extra_dex_delta_count"], 1)
        self.assertGreaterEqual(result["c05_static_evidence_score"], 0.70)
        self.assertFalse(
            any(key.startswith("c05_static_score_") for key in result)
        )
        self.assertTrue(self._RAW_RELATION_FIELDS.isdisjoint(result))
        self.assertNotEqual(
            result.get("similarity_score_source"),
            "c05_static_manifest_relation_high_score",
        )
        self.assertLess(result["similarity_score"], 0.70)

    def test_c05_static_platform_namespace_overlap_does_not_promote_score(self) -> None:
        left_components = {
            "activities": [{"name": "com.left.MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        }
        right_components = {
            "activities": [{"name": "com.right.MainActivity"}],
            "services": [{"name": "com.google.ads.AdActivity"}],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        }

        result = self._run_one_pair(
            _bundle(
                code_tokens={
                    "Landroid/view/View;->draw(Landroid/graphics/Canvas;)V",
                    "Ljava/lang/String;->length()I",
                    "com.left.Core:onlyLeft",
                },
                component_features=left_components,
                resource_digests={("res/layout/main.xml", "left-digest")},
                libraries={"androidx.appcompat"},
            ),
            _bundle(
                code_tokens={
                    "Landroid/view/View;->onTouchEvent(Landroid/view/MotionEvent;)Z",
                    "Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;",
                    "com.right.Payload:onlyRight",
                },
                component_features=right_components,
                resource_digests={("res/layout/main.xml", "right-digest")},
                libraries={"androidx.appcompat", "lib_abi:armeabi-v7a"},
            ),
            extra_apk_b_entries=("lib/armeabi-v7a/libpayload.so",),
        )

        self.assertFalse(result["c05_static_evidence_applied"])
        self.assertGreater(result["c05_static_manifest_delta_count"], 0)
        self.assertGreater(result["c05_static_native_lib_delta_count"], 0)
        self.assertFalse(
            any(key.startswith("c05_static_score_") for key in result)
        )
        self.assertTrue(self._RAW_RELATION_FIELDS.isdisjoint(result))
        self.assertNotEqual(
            result.get("similarity_score_source"),
            "c05_static_manifest_relation_high_score",
        )
        self.assertLess(result["similarity_score"], 0.70)

    def test_c05_static_evidence_stays_disabled_without_static_delta(self) -> None:
        components = {
            "activities": [{"name": "com.example.MainActivity"}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"android.permission.INTERNET"},
            "features": set(),
        }

        result = self._run_one_pair(
            _bundle(
                code_tokens={"com.example.MainActivity:onCreate"},
                component_features=components,
                resource_digests={("res/layout/main.xml", "same-digest")},
            ),
            _bundle(
                code_tokens={"com.example.MainActivity:onCreate"},
                component_features=components,
                resource_digests={("res/layout/main.xml", "same-digest")},
            ),
        )

        self.assertFalse(result["c05_static_evidence_applied"])
        evidence_types = {item["signal_type"] for item in result["evidence"]}
        self.assertNotIn("c05_static_evidence", evidence_types)


if __name__ == "__main__":
    unittest.main()

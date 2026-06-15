#!/usr/bin/env python3
"""Tests for semantic multiview representation profile.

The profile separates strong identity anchors from supporting structural
signals. Coarse signals such as code statistics and DEX packaging must not
produce a high similarity conclusion by themselves.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import semantic_multiview


def _code_v4(methods: dict[str, str]) -> dict:
    return {
        "mode": "v4",
        "method_fingerprints": dict(methods),
        "total_methods": len(methods),
    }


def _resource(paths_to_digest: dict[str, str]) -> dict:
    return {
        "resource_digests": set(paths_to_digest.items()),
        "file_count": len(paths_to_digest),
        "total_size": 0,
    }


class TestSemanticCodeViews(unittest.TestCase):
    def test_code_stats_survives_method_rename_but_identity_does_not(self) -> None:
        left = semantic_multiview.build_semantic_views_from_features(
            apk_id="A",
            code_v4=_code_v4({
                "Lcom/a/Foo;->run()V": "S:1111",
                "Lcom/a/Foo;->stop()V": "S:2222",
            }),
        )
        right = semantic_multiview.build_semantic_views_from_features(
            apk_id="B",
            code_v4=_code_v4({
                "Lx/y/Z;->a()V": "S:1111",
                "Lx/y/Z;->b()V": "S:2222",
            }),
        )

        result = semantic_multiview.compare_semantic_views(left, right)

        self.assertEqual(result["scores"]["R_code_identity"], 0.0)
        self.assertEqual(result["scores"]["R_code_stats"], 1.0)
        self.assertEqual(result["semantic_band"], "review")
        self.assertNotEqual(result["semantic_band"], "high")

    def test_dex_packaging_is_extracted_from_apk_zip_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = Path(tmpdir) / "sample.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("classes.dex", b"dex\n035\0")
                archive.writestr("classes2.dex", b"dex\n035\0")
                archive.writestr("res/layout/main.xml", b"<LinearLayout />")

            tokens = semantic_multiview.extract_dex_packaging_tokens(apk_path)

        self.assertEqual(
            tokens,
            {
                "dex_count:2",
                "dex_name:classes.dex",
                "dex_name:classes2.dex",
            },
        )


class TestSemanticResourceViews(unittest.TestCase):
    def test_resource_structure_ignores_digest_changes_and_delta_tracks_them(self) -> None:
        left = semantic_multiview.build_semantic_views_from_features(
            apk_id="A",
            resource=_resource({
                "res/layout/main.xml": "digest-old-layout",
                "res/drawable/logo.png": "digest-old-logo",
            }),
        )
        right = semantic_multiview.build_semantic_views_from_features(
            apk_id="B",
            resource=_resource({
                "res/layout/main.xml": "digest-new-layout",
                "res/drawable/logo.png": "digest-new-logo",
            }),
        )

        result = semantic_multiview.compare_semantic_views(left, right)

        self.assertEqual(result["scores"]["R_resource_identity"], 0.0)
        self.assertEqual(result["scores"]["R_resource_structure"], 1.0)
        self.assertEqual(result["resource_delta"]["modified_count"], 2)
        self.assertEqual(result["resource_delta"]["added_count"], 0)
        self.assertEqual(result["resource_delta"]["removed_count"], 0)


class TestSemanticDecisionPolicy(unittest.TestCase):
    def test_same_code_with_changed_resources_is_high_semantic_match(self) -> None:
        left = semantic_multiview.build_semantic_views_from_features(
            apk_id="A",
            code_v4=_code_v4({
                "Lcom/app/Main;->onCreate()V": "S:aaaa",
                "Lcom/app/Sync;->run()V": "S:bbbb",
            }),
            resource=_resource({
                "res/layout/main.xml": "layout-v1",
                "res/drawable/logo.png": "logo-v1",
            }),
        )
        right = semantic_multiview.build_semantic_views_from_features(
            apk_id="B",
            code_v4=_code_v4({
                "Lcom/app/Main;->onCreate()V": "S:aaaa",
                "Lcom/app/Sync;->run()V": "S:bbbb",
            }),
            resource=_resource({
                "res/layout/main.xml": "layout-v2",
                "res/drawable/logo.png": "logo-v2",
            }),
        )

        result = semantic_multiview.compare_semantic_views(left, right)

        self.assertEqual(result["semantic_band"], "high")
        self.assertEqual(result["semantic_relation"], "same_code_resource_changed")
        self.assertEqual(result["scores"]["R_code_identity"], 1.0)
        self.assertEqual(result["scores"]["R_resource_identity"], 0.0)
        self.assertEqual(result["scores"]["R_resource_structure"], 1.0)

    def test_structure_and_packaging_without_identity_stays_review(self) -> None:
        left = semantic_multiview.build_semantic_views_from_features(
            apk_id="A",
            code_v4=_code_v4({"Lcom/a/Foo;->run()V": "S:aaaa"}),
            dex_packaging_tokens={"dex_name:classes.dex", "dex_count:1"},
            resource=_resource({"res/layout/main.xml": "layout-a"}),
        )
        right = semantic_multiview.build_semantic_views_from_features(
            apk_id="B",
            code_v4=_code_v4({"Lcom/b/Bar;->run()V": "S:aaaa"}),
            dex_packaging_tokens={"dex_name:classes.dex", "dex_count:1"},
            resource=_resource({"res/layout/main.xml": "layout-b"}),
        )

        result = semantic_multiview.compare_semantic_views(left, right)

        self.assertEqual(result["scores"]["R_code_identity"], 0.0)
        self.assertEqual(result["scores"]["R_code_stats"], 1.0)
        self.assertEqual(result["scores"]["R_code_packaging"], 1.0)
        self.assertEqual(result["semantic_band"], "review")
        self.assertEqual(result["semantic_relation"], "supporting_signals_without_identity")


class TestSemanticViewArtifacts(unittest.TestCase):
    def test_view_artifact_records_are_emitted_for_v3_4_audit(self) -> None:
        views = semantic_multiview.build_semantic_views_from_features(
            apk_id="A",
            code_v4=_code_v4({"Lcom/app/Main;->onCreate()V": "S:aaaa"}),
            resource=_resource({"res/layout/main.xml": "digest"}),
        )

        artifacts = views["view_artifacts"]

        artifact_types = {artifact["view_type"] for artifact in artifacts}
        self.assertIn("R_code_identity", artifact_types)
        self.assertIn("R_code_stats", artifact_types)
        self.assertIn("R_resource_identity", artifact_types)
        self.assertIn("R_resource_structure", artifact_types)
        for artifact in artifacts:
            self.assertEqual(artifact["record_type"], "ViewArtifactRecord")
            self.assertEqual(artifact["status"], "success")
            self.assertEqual(artifact["apk_id"], "A")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for APK feature extractor wrappers."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


class TestZipLightExtractor(unittest.TestCase):
    def _make_apk(self, root: Path) -> Path:
        apk_path = root / "sample.apk"
        with zipfile.ZipFile(apk_path, "w") as archive:
            archive.writestr("classes.dex", b"dex\n035\0")
            archive.writestr("AndroidManifest.xml", b"<manifest package='x.y'/>")
            archive.writestr("res/layout/main.xml", b"<LinearLayout/>")
            archive.writestr("assets/data.bin", b"payload")
            archive.writestr("lib/arm64-v8a/libx.so", b"native")
        return apk_path

    def test_zip_light_extractor_returns_run_record_views_and_layers(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            result = run_zip_light_extractor(
                apk_path,
                requested_views=["code", "resource", "metadata"],
                profile_ref="profiles/current.yaml",
                representation_spec_ref="profiles/current.yaml#representation_spec",
                config_hash="sha256:config",
            )

        run_record = result["extractor_run_record"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(run_record["record_type"], "ExtractorRunRecord")
        self.assertEqual(run_record["extractor_id"], "zip_light_extractor")
        self.assertEqual(run_record["mode"], "light")
        self.assertEqual(run_record["requested_views"], ["code", "resource", "metadata"])
        self.assertEqual(run_record["produced_views"], ["code", "resource", "metadata"])
        self.assertEqual(run_record["profile_ref"], "profiles/current.yaml")
        self.assertIn("zip_light_extractor", run_record["cache_key"])

        self.assertEqual(set(result["layers"]), {"code", "resource", "metadata"})
        self.assertTrue(result["layers"]["code"])
        self.assertTrue(result["layers"]["resource"])
        self.assertTrue(result["layers"]["metadata"])

        artifacts = result["view_artifacts"]
        self.assertEqual([item["view_type"] for item in artifacts], ["code", "resource", "metadata"])
        for artifact in artifacts:
            self.assertEqual(artifact["record_type"], "ViewArtifactRecord")
            self.assertEqual(artifact["extractor_run_ref"], run_record["run_id"])
            self.assertEqual(artifact["extractor_run_record"]["run_id"], run_record["run_id"])

    def test_zip_light_extractor_reports_missing_apk_as_typed_failure(self) -> None:
        from feature_extractors import run_zip_light_extractor

        result = run_zip_light_extractor("/no/such/app.apk", requested_views=["code"])

        self.assertEqual(result["status"], "unsupported_input")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "unsupported_input")
        self.assertIn("missing_apk", result["extractor_run_record"]["errors"])

    def test_zip_light_extractor_reports_bad_zip_as_analysis_failed(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = Path(tmp) / "broken.apk"
            apk_path.write_text("not a zip", encoding="utf-8")
            result = run_zip_light_extractor(apk_path, requested_views=["code"])

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "analysis_failed")
        self.assertIn("bad_zipfile", result["extractor_run_record"]["errors"])


class TestSemanticMultiviewExtractor(unittest.TestCase):
    def _make_apk(self, root: Path) -> Path:
        apk_path = root / "semantic.apk"
        with zipfile.ZipFile(apk_path, "w") as archive:
            archive.writestr("classes.dex", b"dex\n035\0")
            archive.writestr("AndroidManifest.xml", b"<manifest package='x.y'/>")
            archive.writestr("res/layout/main.xml", b"<LinearLayout/>")
            archive.writestr("res/drawable/icon.png", b"png")
        return apk_path

    def test_semantic_multiview_capability_describes_views_and_modes(self) -> None:
        from feature_extractors import build_semantic_multiview_extractor_capability

        capability = build_semantic_multiview_extractor_capability()

        self.assertEqual(capability["record_type"], "ExtractorCapability")
        self.assertEqual(capability["extractor_id"], "semantic_multiview_extractor")
        self.assertEqual(capability["supported_modes"], ["deep", "diagnostic"])
        self.assertIn("R_code_identity", capability["supported_views"])
        self.assertIn("R_code_stats", capability["supported_views"])
        self.assertIn("R_resource_identity", capability["supported_views"])

    def test_semantic_multiview_extractor_returns_run_record_and_view_artifacts(self) -> None:
        from feature_extractors import run_semantic_multiview_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            result = run_semantic_multiview_extractor(
                apk_path,
                requested_views=[
                    "R_code_identity",
                    "R_code_stats",
                    "R_code_packaging",
                ],
                profile_ref="profiles/semantic.yaml",
                representation_spec_ref="profiles/semantic.yaml#representation_spec",
                config_hash="sha256:semantic-config",
            )

        run_record = result["extractor_run_record"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(run_record["record_type"], "ExtractorRunRecord")
        self.assertEqual(run_record["extractor_id"], "semantic_multiview_extractor")
        self.assertEqual(run_record["mode"], "deep")
        self.assertEqual(
            run_record["requested_views"],
            ["R_code_identity", "R_code_stats", "R_code_packaging"],
        )
        self.assertEqual(
            run_record["produced_views"],
            ["R_code_identity", "R_code_stats", "R_code_packaging"],
        )
        self.assertEqual(run_record["profile_ref"], "profiles/semantic.yaml")
        self.assertIn("semantic_multiview_extractor", run_record["cache_key"])

        semantic_views = result["semantic_views"]
        self.assertEqual(
            set(semantic_views["views"]),
            {"R_code_identity", "R_code_stats", "R_code_packaging"},
        )
        self.assertEqual(
            [item["view_type"] for item in result["view_artifacts"]],
            ["R_code_identity", "R_code_stats", "R_code_packaging"],
        )
        for artifact in result["view_artifacts"]:
            self.assertEqual(artifact["record_type"], "ViewArtifactRecord")
            self.assertEqual(artifact["extractor_run_ref"], run_record["run_id"])
            self.assertEqual(artifact["extractor_run_record"]["run_id"], run_record["run_id"])

    def test_semantic_multiview_extractor_reports_unsupported_view_as_partial(self) -> None:
        from feature_extractors import run_semantic_multiview_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            result = run_semantic_multiview_extractor(
                apk_path,
                requested_views=["R_code_packaging", "R_unknown"],
            )

        self.assertEqual(result["status"], "partial_result")
        self.assertEqual(result["extractor_run_record"]["status"], "partial_result")
        self.assertEqual(result["extractor_run_record"]["produced_views"], ["R_code_packaging"])
        self.assertIn("unsupported_view:R_unknown", result["warnings"])

    def test_semantic_multiview_extractor_reports_missing_apk_as_typed_failure(self) -> None:
        from feature_extractors import run_semantic_multiview_extractor

        result = run_semantic_multiview_extractor(
            "/no/such/app.apk",
            requested_views=["R_code_identity"],
        )

        self.assertEqual(result["status"], "unsupported_input")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["semantic_views"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "unsupported_input")
        self.assertIn("missing_apk", result["extractor_run_record"]["errors"])

    def test_semantic_multiview_extractor_reports_bad_zip_as_analysis_failed(self) -> None:
        from feature_extractors import run_semantic_multiview_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = Path(tmp) / "broken.apk"
            apk_path.write_text("not a zip", encoding="utf-8")
            result = run_semantic_multiview_extractor(
                apk_path,
                requested_views=["R_code_identity"],
            )

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["semantic_views"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "analysis_failed")
        self.assertIn("bad_zipfile", result["extractor_run_record"]["errors"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for APK feature extractor wrappers."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def test_zip_light_artifact_schema_versions_match_runtime_manifest(self) -> None:
        import feature_extractors
        from runtime_profile_contract import build_runtime_profile_manifest

        schema_versions = getattr(
            feature_extractors,
            "ZIP_LIGHT_VIEW_SCHEMA_VERSIONS",
            None,
        )
        self.assertIsNotNone(schema_versions)
        with self.assertRaises(TypeError):
            schema_versions["code"] = "mutated"

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            result = feature_extractors.run_zip_light_extractor(apk_path)

        actual_versions = {
            artifact["view_type"]: artifact["view_schema_version"]
            for artifact in result["view_artifacts"]
        }
        manifest = build_runtime_profile_manifest()
        self.assertEqual(
            actual_versions,
            manifest["light_view_schema_versions"],
        )
        self.assertEqual(
            actual_versions,
            dict(schema_versions),
        )

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

    def test_zip_light_extractor_reports_missing_dex_as_analysis_failed(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = Path(tmp) / "without-code.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"<manifest package='x.y'/>")
                archive.writestr("resources.arsc", b"resources")

            result = run_zip_light_extractor(apk_path, requested_views=["code"])

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "analysis_failed")
        self.assertIn("code_empty_apk", result["extractor_run_record"]["errors"])

    def test_zip_light_extractor_reports_invalid_dex_as_analysis_failed(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = Path(tmp) / "invalid-dex.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"<manifest package='x.y'/>")
                archive.writestr("classes.dex", b"not-a-real-dex")

            result = run_zip_light_extractor(apk_path, requested_views=["code"])

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["view_artifacts"], [])
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["extractor_run_record"]["status"], "analysis_failed")
        self.assertIn("invalid_dex_payload", result["extractor_run_record"]["errors"])

    def test_zip_light_extractor_rejects_forbidden_fresh_layers_before_cache_write(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                return_value={"code": {"method_namespace:Lcom/example"}},
            ), mock.patch(
                "feature_extractors._write_extractor_cache",
            ) as write_cache:
                result = feature_extractors.run_zip_light_extractor(
                    apk_path,
                    cache_dir=cache_dir,
                )

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["view_artifacts"], [])
        self.assertIn(
            "rejected_active_token:code:method_namespace:",
            result["errors"],
        )
        write_cache.assert_not_called()

    def test_zip_light_extractor_fails_closed_for_malformed_fresh_and_cached_layers(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                return_value={"code": "not-a-token-collection"},
            ), mock.patch(
                "feature_extractors._write_extractor_cache",
            ) as write_cache:
                fresh_result = feature_extractors.run_zip_light_extractor(
                    apk_path,
                    cache_dir=cache_dir,
                )

            cache = feature_extractors._build_extractor_cache(cache_dir)
            cache_key = feature_extractors._zip_light_cache_key(
                apk_sha256=feature_extractors._sha256_file(apk_path),
            )
            feature_extractors._write_extractor_cache(
                cache,
                cache_key,
                {"layers": None},
            )
            cached_result = feature_extractors.run_zip_light_extractor(
                apk_path,
                cache_dir=cache_dir,
            )

        for result, reason in (
            (fresh_result, "invalid_active_layer:code:expected_token_collection"),
            (cached_result, "invalid_active_layers:expected_dict"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(result["status"], "analysis_failed")
                self.assertEqual(result["layers"], {})
                self.assertEqual(result["view_artifacts"], [])
                self.assertIn(reason, result["errors"])
        write_cache.assert_not_called()

    def test_zip_light_extractor_rejects_forbidden_cached_layers(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"
            apk_sha256 = feature_extractors._sha256_file(apk_path)
            cache_key = feature_extractors._zip_light_cache_key(
                apk_sha256=apk_sha256,
            )
            cache = feature_extractors._build_extractor_cache(cache_dir)
            feature_extractors._write_extractor_cache(
                cache,
                cache_key,
                {"layers": {"metadata": {"apk_name:cached"}}},
            )

            result = feature_extractors.run_zip_light_extractor(
                apk_path,
                cache_dir=cache_dir,
            )

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["view_artifacts"], [])
        self.assertIn(
            "rejected_active_token:metadata:apk_name:",
            result["errors"],
        )

    def test_zip_light_extractor_maps_rejected_active_token_error_to_analysis_failed(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                side_effect=feature_extractors.RejectedActiveTokenError(
                    "rejected_active_token:code:method_namespace:"
                ),
            ):
                result = feature_extractors.run_zip_light_extractor(apk_path)

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["layers"], {})
        self.assertEqual(result["view_artifacts"], [])
        self.assertIn(
            "rejected_active_token:code:method_namespace:",
            result["errors"],
        )

    def test_zip_light_extractor_reuses_cache_on_second_call(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"

            first = run_zip_light_extractor(
                apk_path,
                requested_views=["code", "resource"],
                cache_dir=cache_dir,
            )
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                side_effect=AssertionError("zip extractor must not run on cache hit"),
            ):
                second = run_zip_light_extractor(
                    apk_path,
                    requested_views=["code", "resource"],
                    cache_dir=cache_dir,
                )

        self.assertEqual(first["extractor_run_record"]["cache_status"], "miss")
        self.assertEqual(second["extractor_run_record"]["cache_status"], "hit")
        self.assertEqual(first["layers"], second["layers"])
        for artifact in second["view_artifacts"]:
            self.assertEqual(
                artifact["extractor_run_ref"],
                second["extractor_run_record"]["run_id"],
            )

    def test_zip_light_cache_serves_requested_subset_from_full_payload(self) -> None:
        from feature_extractors import run_zip_light_extractor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"

            first = run_zip_light_extractor(apk_path, cache_dir=cache_dir)
            second = run_zip_light_extractor(
                apk_path,
                requested_views=["metadata"],
                cache_dir=cache_dir,
            )

        self.assertEqual(first["extractor_run_record"]["cache_status"], "miss")
        self.assertEqual(second["extractor_run_record"]["cache_status"], "hit")
        self.assertEqual(second["extractor_run_record"]["produced_views"], ["metadata"])
        self.assertEqual(set(second["layers"]), {"metadata"})

    def test_zip_light_cache_misses_entry_seeded_with_v1_after_v2_upgrade(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"
            with mock.patch.object(
                feature_extractors,
                "ZIP_LIGHT_EXTRACTOR_VERSION",
                "zip-light-v1",
            ):
                seeded = feature_extractors.run_zip_light_extractor(
                    apk_path,
                    cache_dir=cache_dir,
                )
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                wraps=feature_extractors.extract_layers_from_apk,
            ) as extract_layers:
                result = feature_extractors.run_zip_light_extractor(
                    apk_path,
                    cache_dir=cache_dir,
                )

        self.assertEqual(seeded["extractor_run_record"]["extractor_version"], "zip-light-v1")
        self.assertEqual(result["extractor_run_record"]["extractor_version"], "zip-light-v2")
        self.assertEqual(result["extractor_run_record"]["cache_status"], "miss")
        extract_layers.assert_called_once()

    def test_zip_light_cache_misses_when_only_config_hash_changes(self) -> None:
        import feature_extractors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"
            first = feature_extractors.run_zip_light_extractor(
                apk_path,
                cache_dir=cache_dir,
                config_hash="sha256:config-a",
            )
            with mock.patch(
                "feature_extractors.extract_layers_from_apk",
                wraps=feature_extractors.extract_layers_from_apk,
            ) as extract_layers:
                second = feature_extractors.run_zip_light_extractor(
                    apk_path,
                    cache_dir=cache_dir,
                    config_hash="sha256:config-b",
                )

        self.assertEqual(first["extractor_run_record"]["cache_status"], "miss")
        self.assertEqual(second["extractor_run_record"]["cache_status"], "miss")
        extract_layers.assert_called_once()


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

    def test_semantic_multiview_extractor_reuses_cache_on_second_call(self) -> None:
        from feature_extractors import run_semantic_multiview_extractor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = self._make_apk(root)
            cache_dir = root / "cache"

            first = run_semantic_multiview_extractor(
                apk_path,
                requested_views=["R_code_identity", "R_code_stats"],
                cache_dir=cache_dir,
            )
            with mock.patch(
                "feature_extractors.extract_semantic_views",
                side_effect=AssertionError("semantic extractor must not run on cache hit"),
            ):
                second = run_semantic_multiview_extractor(
                    apk_path,
                    requested_views=["R_code_identity", "R_code_stats"],
                    cache_dir=cache_dir,
                )

        self.assertEqual(first["extractor_run_record"]["cache_status"], "miss")
        self.assertEqual(second["extractor_run_record"]["cache_status"], "hit")
        self.assertEqual(
            first["semantic_views"]["views"],
            second["semantic_views"]["views"],
        )
        for artifact in second["view_artifacts"]:
            self.assertEqual(
                artifact["extractor_run_ref"],
                second["extractor_run_record"]["run_id"],
            )


if __name__ == "__main__":
    unittest.main()

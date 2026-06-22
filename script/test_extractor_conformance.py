#!/usr/bin/env python3
"""Tests for v3.4 extractor conformance checks."""
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


class TestExtractorConformance(unittest.TestCase):
    def _make_apk(self, root: Path) -> Path:
        apk_path = root / "conformance.apk"
        with zipfile.ZipFile(apk_path, "w") as archive:
            archive.writestr("classes.dex", b"dex\n035\0")
            archive.writestr("AndroidManifest.xml", b"<manifest package='x.y'/>")
            archive.writestr("res/layout/main.xml", b"<LinearLayout/>")
            archive.writestr("res/drawable/icon.png", b"png")
            archive.writestr("assets/data.bin", b"payload")
        return apk_path

    def test_default_registry_lists_registered_extractors(self) -> None:
        from extractor_conformance import (
            build_default_extractor_registry,
            validate_extractor_capability,
        )

        registry = build_default_extractor_registry()

        self.assertEqual(registry["record_type"], "ExtractorRegistry")
        extractor_ids = [item["extractor_id"] for item in registry["extractors"]]
        self.assertEqual(
            extractor_ids,
            ["zip_light_extractor", "semantic_multiview_extractor"],
        )
        for capability in registry["extractors"]:
            self.assertEqual(validate_extractor_capability(capability), [])

    def test_conformance_check_passes_for_registered_extractors(self) -> None:
        from extractor_conformance import run_extractor_conformance_check

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            report = run_extractor_conformance_check(
                apk_path,
                profile_ref="profiles/current.yaml",
                representation_spec_ref="profiles/current.yaml#representation_spec",
                config_hash="sha256:test-config",
            )

        self.assertEqual(report["record_type"], "ExtractorConformanceReport")
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            [item["extractor_id"] for item in report["checked_extractors"]],
            ["zip_light_extractor", "semantic_multiview_extractor"],
        )
        for item in report["checked_extractors"]:
            self.assertEqual(item["status"], "success")
            self.assertTrue(item["produced_views"])

    def test_capability_validator_requires_cache_key_identity_fields(self) -> None:
        from extractor_conformance import validate_extractor_capability
        from feature_extractors import build_zip_light_extractor_capability

        capability = build_zip_light_extractor_capability()
        capability["cache_key_fields"] = ["apk_sha256"]

        self.assertIn(
            "capability:zip_light_extractor:missing_cache_key_field:mode",
            validate_extractor_capability(capability),
        )

    def test_run_validator_requires_view_artifact_provenance(self) -> None:
        from extractor_conformance import validate_extractor_run_result
        from feature_extractors import (
            build_zip_light_extractor_capability,
            run_zip_light_extractor,
        )

        with tempfile.TemporaryDirectory() as tmp:
            apk_path = self._make_apk(Path(tmp))
            result = run_zip_light_extractor(apk_path, requested_views=["code"])

        result["view_artifacts"][0].pop("extractor_run_ref")
        errors = validate_extractor_run_result(
            result,
            build_zip_light_extractor_capability(),
            payload_key="layers",
        )

        self.assertIn(
            "run:zip_light_extractor:artifact:code:missing_extractor_run_ref",
            errors,
        )


if __name__ == "__main__":
    unittest.main()

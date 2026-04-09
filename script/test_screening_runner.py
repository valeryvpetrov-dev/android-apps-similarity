#!/usr/bin/env python3
"""Tests for screening_runner cheap-path metadata extraction."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from screening_runner import extract_layers_from_apk


def _write_apk(tmpdir: Path, name: str, manifest_bytes: bytes) -> Path:
    apk_path = tmpdir / name
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest_bytes)
        archive.writestr("classes.dex", b"dex")
    return apk_path


class TestScreeningRunnerMetadataExtraction(unittest.TestCase):
    def test_extract_layers_from_apk_adds_manifest_metadata_tokens(self) -> None:
        manifest = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.binary"
    android:versionCode="42">

    <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34" />
</manifest>
""".encode("utf-16le")

        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = _write_apk(Path(tmpdir), "sample.apk", manifest)
            layers = extract_layers_from_apk(apk_path)

        self.assertIn("package_name:com.example.binary", layers["metadata"])
        self.assertIn("version_code:42", layers["metadata"])
        self.assertIn("min_sdk:24", layers["metadata"])
        self.assertIn("target_sdk:34", layers["metadata"])

    def test_extract_layers_from_apk_skips_missing_manifest_values(self) -> None:
        manifest = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.partial">

    <application>
        <activity android:name=".MainActivity" />
    </application>
</manifest>
""".encode("utf-16le")

        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = _write_apk(Path(tmpdir), "partial.apk", manifest)
            layers = extract_layers_from_apk(apk_path)

        self.assertIn("package_name:com.example.partial", layers["metadata"])
        self.assertFalse(any(token.startswith("version_code:") for token in layers["metadata"]))
        self.assertFalse(any(token.startswith("min_sdk:") for token in layers["metadata"]))
        self.assertFalse(any(token.startswith("target_sdk:") for token in layers["metadata"]))


if __name__ == "__main__":
    unittest.main()

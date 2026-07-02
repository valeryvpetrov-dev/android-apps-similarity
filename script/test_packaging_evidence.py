#!/usr/bin/env python3
"""Tests for APK packaging evidence extraction."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import packaging_evidence


def _write_apk(
    path: Path,
    *,
    package_name: str,
    entries: dict[str, bytes] | None = None,
) -> None:
    manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="{}"><application /></manifest>'
    ).format(package_name)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest.encode("utf-8"))
        archive.writestr("classes.dex", b"dex\n035\0")
        for name, payload in (entries or {}).items():
            archive.writestr(name, payload)


class TestPackagingEvidence(unittest.TestCase):
    def test_extract_apk_packaging_profile_reads_manifest_and_zip_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = Path(tmpdir) / "sample.apk"
            _write_apk(
                apk_path,
                package_name="com.example.left",
                entries={
                    "classes2.dex": b"dex\n035\0",
                    "META-INF/CERT.RSA": b"cert-left",
                    "res/layout/main.xml": b"<LinearLayout />",
                    "lib/armeabi-v7a/libsample.so": b"native",
                },
            )

            profile = packaging_evidence.extract_apk_packaging_profile(apk_path)

        self.assertEqual(profile["manifest_package_name"], "com.example.left")
        self.assertEqual(profile["dex_names"], ["classes.dex", "classes2.dex"])
        self.assertEqual(profile["signature_file_names"], ["META-INF/CERT.RSA"])
        self.assertEqual(profile["native_lib_names"], ["lib/armeabi-v7a/libsample.so"])
        self.assertGreaterEqual(profile["entry_count"], 5)

    def test_build_packaging_evidence_fields_detects_direct_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            left = root / "left.apk"
            right = root / "right.apk"
            _write_apk(
                left,
                package_name="com.example.left",
                entries={"META-INF/CERT.RSA": b"cert-left"},
            )
            _write_apk(
                right,
                package_name="com.example.right",
                entries={
                    "META-INF/CERT.RSA": b"cert-right",
                    "classes2.dex": b"dex\n035\0",
                    "lib/arm64-v8a/libpayload.so": b"native-right",
                    "assets/payload.bin": b"payload",
                },
            )

            with mock.patch.object(
                packaging_evidence,
                "extract_apk_signature_hash",
                side_effect=lambda path: "left-hash" if Path(path) == left else "right-hash",
            ):
                fields = packaging_evidence.build_packaging_evidence_fields(left, right)

        self.assertTrue(fields["packaging_evidence_applied"])
        self.assertEqual(fields["packaging_evidence_role"], "evidence_only")
        self.assertFalse(fields["packaging_score_included"])
        self.assertTrue(fields["manifest_package_name_delta"])
        self.assertTrue(fields["signing_certificate_delta"])
        self.assertTrue(fields["apk_entry_layout_delta"])
        self.assertEqual(fields["left_manifest_package_name"], "com.example.left")
        self.assertEqual(fields["right_manifest_package_name"], "com.example.right")
        self.assertGreater(fields["packaging_evidence_score"], 0.0)
        self.assertIn("manifest_package_name_delta", fields["packaging_delta_kinds"])
        self.assertIn("signing_certificate_delta", fields["packaging_delta_kinds"])
        self.assertIn("apk_entry_layout_delta", fields["packaging_delta_kinds"])
        self.assertTrue(fields["package_rename_evidence_applied"])
        self.assertEqual(
            fields["package_rename_left_manifest_package_name"],
            "com.example.left",
        )
        self.assertEqual(
            fields["package_rename_right_manifest_package_name"],
            "com.example.right",
        )
        self.assertEqual(fields["package_rename_score_effect"], "none")
        self.assertFalse(fields["package_rename_score_included"])
        self.assertTrue(fields["apk_layout_evidence_applied"])
        self.assertEqual(fields["apk_layout_score_effect"], "none")
        self.assertFalse(fields["apk_layout_score_included"])
        self.assertIn("dex_layout_delta", fields["apk_layout_delta_kinds"])
        self.assertIn("native_lib_delta", fields["apk_layout_delta_kinds"])
        self.assertTrue(fields["apk_layout_dex_name_delta"])
        self.assertTrue(fields["apk_layout_native_lib_delta"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

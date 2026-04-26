#!/usr/bin/env python3
"""TDD for SCREENING-22-PRECOMPUTE-SIGNATURE."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from script.screening_runner import (
    SCREENING_SIGNATURE_FALLBACK_WARNING,
    _build_candidate_pairs_via_lsh,
    build_screening_signature,
    extract_layers_from_apk,
)
from script.run_screening_signature_precompute import (
    run_screening_signature_precompute,
)


def _write_apk(apk_dir: Path, name: str, manifest_package: str) -> Path:
    apk_path = apk_dir / name
    manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="{}"><application /></manifest>'.format(manifest_package)
    ).encode("utf-16le")
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest)
        archive.writestr("classes.dex", b"dex\n035\x00")
        archive.writestr("res/layout/main.xml", b"<LinearLayout />")
    return apk_path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestScreeningSignaturePrecompute(unittest.TestCase):
    def test_run_precompute_scans_apks_and_writes_signature_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_dir = root / "apk"
            apk_dir.mkdir()
            apk_path = _write_apk(apk_dir, "sample.apk", "com.example.sample")
            out_path = root / "screening-signatures-v1.jsonl"

            run_screening_signature_precompute(apk_dir, out_path)

            records = _read_jsonl(out_path)
            expected_record = {
                "app_id": apk_path.stem,
                "apk_path": str(apk_path.resolve()),
                "layers": extract_layers_from_apk(apk_path),
            }
            expected_signature = build_screening_signature(expected_record)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["app_id"], "sample")
            self.assertEqual(records[0]["screening_signature"], expected_signature)
            self.assertEqual(records[0]["signature_version"], "v1")
            self.assertIn("sha256", records[0])
            self.assertIn("built_at", records[0])

    def test_repeated_precompute_over_same_apks_is_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_dir = root / "apk"
            apk_dir.mkdir()
            _write_apk(apk_dir, "b.apk", "com.example.b")
            _write_apk(apk_dir, "a.apk", "com.example.a")
            first_out = root / "first.jsonl"
            second_out = root / "second.jsonl"

            run_screening_signature_precompute(apk_dir, first_out)
            run_screening_signature_precompute(apk_dir, second_out)

            self.assertEqual(
                first_out.read_text(encoding="utf-8"),
                second_out.read_text(encoding="utf-8"),
            )

    def test_precomputed_records_do_not_trigger_screening_fallback_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_dir = root / "apk"
            apk_dir.mkdir()
            _write_apk(apk_dir, "left.apk", "com.example.same")
            _write_apk(apk_dir, "right.apk", "com.example.same")
            out_path = root / "records.jsonl"

            run_screening_signature_precompute(apk_dir, out_path)
            records = _read_jsonl(out_path)
            _build_candidate_pairs_via_lsh(
                records,
                {
                    "type": "minhash_lsh",
                    "num_perm": 64,
                    "bands": 32,
                    "seed": 42,
                    "features": ["code", "resource"],
                },
            )

            for record in records:
                self.assertNotIn(
                    SCREENING_SIGNATURE_FALLBACK_WARNING,
                    record.get("screening_warnings", []),
                )

    def test_invalid_apk_records_error_and_keeps_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_dir = root / "apk"
            apk_dir.mkdir()
            _write_apk(apk_dir, "valid.apk", "com.example.valid")
            (apk_dir / "broken.apk").write_bytes(b"not a zip")
            out_path = root / "records.jsonl"

            run_screening_signature_precompute(apk_dir, out_path)

            records = _read_jsonl(out_path)
            by_app_id = {record["app_id"]: record for record in records}
            self.assertIn("valid", by_app_id)
            self.assertIn("broken", by_app_id)
            self.assertIn("screening_signature", by_app_id["valid"])
            self.assertIn("error", by_app_id["broken"])
            self.assertIsInstance(by_app_id["broken"]["error"], str)
            self.assertNotEqual(by_app_id["broken"]["error"], "")


if __name__ == "__main__":
    unittest.main()

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

import screening_runner
from screening_runner import build_candidate_list, extract_layers_from_apk


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


class TestScreeningRunnerCandidateListContract(unittest.TestCase):
    def test_build_candidate_list_adds_screening_handoff_fields(self) -> None:
        app_records = [
            {"app_id": "APP-A"},
            {"app_id": "APP-B"},
        ]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = lambda **kwargs: 0.42  # type: ignore[assignment]
            candidate_list = build_candidate_list(
                app_records=app_records,
                selected_layers=["code"],
                metric="jaccard",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        finally:
            screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]

        self.assertEqual(len(candidate_list), 1)
        row = candidate_list[0]
        self.assertEqual(row["app_a"], "APP-A")
        self.assertEqual(row["app_b"], "APP-B")
        self.assertEqual(row["query_app_id"], "APP-A")
        self.assertEqual(row["candidate_app_id"], "APP-B")
        self.assertEqual(row["retrieval_rank"], 1)
        self.assertEqual(row["retrieval_features_used"], ["code"])
        self.assertEqual(row["screening_warnings"], [])
        self.assertIsNone(row["screening_explanation"])

    def test_build_candidate_list_assigns_rank_after_sorting(self) -> None:
        app_records = [
            {"app_id": "APP-C"},
            {"app_id": "APP-A"},
            {"app_id": "APP-B"},
        ]
        scores = {
            ("APP-A", "APP-B"): 0.40,
            ("APP-A", "APP-C"): 0.70,
            ("APP-B", "APP-C"): 0.70,
        }

        def fake_score(**kwargs: object) -> float:
            app_a = kwargs["app_a"]
            app_b = kwargs["app_b"]
            assert isinstance(app_a, dict)
            assert isinstance(app_b, dict)
            return scores[(app_a["app_id"], app_b["app_id"])]

        original_score = screening_runner.calculate_pair_score
        try:
            screening_runner.calculate_pair_score = fake_score  # type: ignore[assignment]
            candidate_list = build_candidate_list(
                app_records=app_records,
                selected_layers=["code", "resource"],
                metric="jaccard",
                threshold=0.10,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
            )
        finally:
            screening_runner.calculate_pair_score = original_score  # type: ignore[assignment]

        self.assertEqual(
            [(row["app_a"], row["app_b"], row["retrieval_score"], row["retrieval_rank"]) for row in candidate_list],
            [
                ("APP-A", "APP-C", 0.70, 1),
                ("APP-B", "APP-C", 0.70, 2),
                ("APP-A", "APP-B", 0.40, 3),
            ],
        )
        self.assertEqual(candidate_list[0]["retrieval_features_used"], ["code", "resource"])


if __name__ == "__main__":
    unittest.main()

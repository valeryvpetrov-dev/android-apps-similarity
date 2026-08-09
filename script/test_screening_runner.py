#!/usr/bin/env python3
"""Tests for screening_runner cheap-path metadata extraction."""

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

import screening_runner
from screening_runner import build_candidate_list, extract_layers_from_apk


def _write_apk(tmpdir: Path, name: str, manifest_bytes: bytes) -> Path:
    apk_path = tmpdir / name
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest_bytes)
        archive.writestr("classes.dex", b"dex")
    return apk_path


class TestScreeningRunnerMetadataExtraction(unittest.TestCase):
    def test_validate_app_records_rejects_forbidden_active_layer_tokens(self) -> None:
        app_records = [
            {
                "app_id": "clean",
                "layers": {
                    "code": {"method_id:Lcom/example/Clean;->run()V"},
                    "component": set(),
                    "resource": set(),
                    "metadata": set(),
                    "library": set(),
                },
            },
            {
                "app_id": "contaminated",
                "layers": {
                    "code": {"method_namespace:Lcom/example"},
                    "component": set(),
                    "resource": set(),
                    "metadata": set(),
                    "library": set(),
                },
            },
        ]

        with self.assertRaisesRegex(
            screening_runner.RejectedActiveTokenError,
            "^rejected_active_token:code:method_namespace:$",
        ):
            screening_runner.validate_app_records(app_records)

    def test_build_screening_signature_rejects_persisted_quarantined_tokens_and_keeps_clean_signature(self) -> None:
        for token in (
            "code:method_namespace:Lcom/example",
            "code:method_namespace_segment:example",
            "metadata:apk_name:legacy",
        ):
            with self.subTest(token=token):
                app_record = {"screening_signature": ["code:method_id:clean", token]}
                with self.assertRaisesRegex(
                    screening_runner.RejectedActiveTokenError,
                    "^rejected_active_token:",
                ):
                    screening_runner.build_screening_signature(app_record)

        clean_record = {
            "screening_signature": ["metadata:manifest_present:1", "code:method_id:clean"],
        }
        self.assertEqual(
            screening_runner.build_screening_signature(clean_record),
            ["code:method_id:clean", "metadata:manifest_present:1"],
        )

    def test_validate_active_layer_tokens_rejects_malformed_active_layers_deterministically(self) -> None:
        invalid_cases = (
            (
                None,
                "invalid_active_layers:expected_dict",
            ),
            (
                {"code": "method_id:unexpected-string-container"},
                "invalid_active_layer:code:expected_token_collection",
            ),
            (
                {"metadata": {"manifest_present:1", 1}},
                "invalid_active_layer:metadata:expected_string_tokens",
            ),
        )

        for layers, reason in invalid_cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(
                    screening_runner.RejectedActiveTokenError,
                    "^{}$".format(reason),
                ):
                    screening_runner.validate_active_layer_tokens(layers)

    def test_extract_layers_from_apk_keeps_method_ids_without_rejected_namespace_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = _write_apk(Path(tmpdir), "method-id.apk", b"<manifest />")
            with mock.patch.object(
                screening_runner,
                "_extract_code_method_identity_tokens",
                return_value={"method_id:Lcom/example/Feature;->run()V"},
            ):
                layers = extract_layers_from_apk(apk_path)

        self.assertIn("method_id:Lcom/example/Feature;->run()V", layers["code"])
        self.assertFalse(
            any(
                token.startswith("method_namespace:")
                for token in layers["code"]
            )
        )
        self.assertFalse(
            any(
                token.startswith("method_namespace_segment:")
                for token in layers["code"]
            )
        )

    def test_extract_layers_from_apk_excludes_filename_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_path = _write_apk(root, "first-name.apk", b"<manifest />")
            second_path = root / "second-name.apk"
            second_path.write_bytes(first_path.read_bytes())

            first_layers = extract_layers_from_apk(first_path)
            second_layers = extract_layers_from_apk(second_path)

        self.assertEqual(first_layers["metadata"], second_layers["metadata"])
        self.assertFalse(
            any(
                token.startswith("apk_name:")
                for token in first_layers["metadata"]
            )
        )

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

    def test_code_layer_keeps_method_identity_when_same_dex_is_packaged_as_multidex(self) -> None:
        source_apk = (
            Path(__file__).resolve().parents[1]
            / "apk"
            / "simple_app"
            / "simple_app-releaseNonOptimized.apk"
        )
        if not source_apk.is_file():
            self.skipTest("simple_app APK fixture is missing")

        with tempfile.TemporaryDirectory() as tmpdir:
            multidex_apk = Path(tmpdir) / "simple_app_multidex.apk"
            with zipfile.ZipFile(source_apk, "r") as src:
                classes_dex = src.read("classes.dex")
                with zipfile.ZipFile(multidex_apk, "w", compression=zipfile.ZIP_STORED) as dst:
                    for info in src.infolist():
                        if info.is_dir():
                            continue
                        dst.writestr(info.filename, src.read(info.filename))
                    dst.writestr("classes2.dex", classes_dex)

            left = {"app_id": "left", "layers": extract_layers_from_apk(source_apk)}
            right = {"app_id": "right", "layers": extract_layers_from_apk(multidex_apk)}
            score = screening_runner.calculate_pair_score(
                app_a=left,
                app_b=right,
                metric="jaccard",
                selected_layers=["code"],
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=1,
            )

        self.assertGreaterEqual(score, 0.90)
        self.assertTrue(
            any(token.startswith("method_id:") for token in left["layers"]["code"])
        )

    def test_code_layer_keeps_method_ids_above_shingled_limit(self) -> None:
        source_apk = (
            Path(__file__).resolve().parents[1]
            / "apk"
            / "simple_app"
            / "simple_app-releaseNonOptimized.apk"
        )
        if not source_apk.is_file():
            self.skipTest("simple_app APK fixture is missing")

        tokens = screening_runner._extract_code_method_identity_tokens(
            source_apk,
            total_dex_bytes=screening_runner.METHOD_IDENTITY_DEX_BYTES_LIMIT + 1,
        )

        self.assertTrue(any(token.startswith("method_id:") for token in tokens))

    def test_code_layer_derives_namespace_tokens_from_method_ids(self) -> None:
        original_code = {
            "method_id:Lcom/phd/m3/c06/f090/Feature00;-><init>()V",
            "method_id:Lcom/phd/m3/c06/f090/Feature00;->compute00()I",
        }
        obfuscated_code = {
            "method_id:Lcom/phd/m3/c06/f090/a;-><init>()V",
            "method_id:Lcom/phd/m3/c06/f090/a;->a()I",
        }

        original_augmented = (
            original_code
            | screening_runner._code_method_namespace_tokens(original_code)
        )
        obfuscated_augmented = (
            obfuscated_code
            | screening_runner._code_method_namespace_tokens(obfuscated_code)
        )

        self.assertIn(
            "method_namespace:Lcom/phd/m3/c06/f090",
            original_augmented,
        )
        self.assertIn(
            "method_namespace:Lcom/phd/m3/c06/f090",
            obfuscated_augmented,
        )
        self.assertGreater(
            screening_runner.jaccard_similarity(
                original_augmented,
                obfuscated_augmented,
            ),
            0.0,
        )

    def test_code_layer_derives_filtered_namespace_segment_tokens(self) -> None:
        original_code = {
            "method_id:Lcom/phd/m3/c04/f169/Feature00;->value00()I",
        }
        renamed_code = {
            "method_id:Lcom/phd/m3/c04/renamed/f169/Feature00;->value00()I",
        }

        original_augmented = (
            original_code
            | screening_runner._code_method_namespace_tokens(original_code)
        )
        renamed_augmented = (
            renamed_code
            | screening_runner._code_method_namespace_tokens(renamed_code)
        )
        original_segments = screening_runner._code_method_namespace_segment_tokens(
            original_augmented
        )
        renamed_segments = screening_runner._code_method_namespace_segment_tokens(
            renamed_augmented
        )

        self.assertIn("method_namespace_segment:c04", original_segments)
        self.assertIn("method_namespace_segment:f169", original_segments)
        self.assertIn("method_namespace_segment:c04", renamed_segments)
        self.assertIn("method_namespace_segment:f169", renamed_segments)
        self.assertNotIn("method_namespace_segment:com", original_segments)
        self.assertGreater(
            screening_runner.jaccard_similarity(
                original_augmented | original_segments,
                renamed_augmented | renamed_segments,
            ),
            0.0,
        )


class TestScreeningRunnerPairScoreCache(unittest.TestCase):
    def test_calculate_pair_score_caches_aggregated_features_per_layer_set(self) -> None:
        app_a = {
            "app_id": "APP-A",
            "layers": {
                "code": {"c1", "c2"},
                "resource": {"r1"},
            },
        }
        app_b = {
            "app_id": "APP-B",
            "layers": {
                "code": {"c2", "c3"},
                "resource": {"r1", "r2"},
            },
        }

        first_score = screening_runner.calculate_pair_score(
            app_a=app_a,
            app_b=app_b,
            metric="jaccard",
            selected_layers=["code", "resource"],
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=1,
        )
        cache_key = screening_runner.SCREENING_AGGREGATE_FEATURE_CACHE_KEY
        cached_a = app_a[cache_key][("code", "resource")]
        cached_b = app_b[cache_key][("code", "resource")]
        second_score = screening_runner.calculate_pair_score(
            app_a=app_a,
            app_b=app_b,
            metric="jaccard",
            selected_layers=["code", "resource"],
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=1,
        )

        self.assertEqual(first_score, second_score)
        self.assertIn(("code", "resource"), app_a[cache_key])
        self.assertIn(("code", "resource"), app_b[cache_key])
        self.assertIs(cached_a, app_a[cache_key][("code", "resource")])
        self.assertIs(cached_b, app_b[cache_key][("code", "resource")])


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
        self.assertEqual(row["query_app_id"], "APP-A")
        self.assertEqual(row["candidate_app_id"], "APP-B")
        self.assertEqual(row["retrieval_rank"], 1)
        self.assertEqual(row["retrieval_features_used"], ["code"])
        self.assertEqual(row["screening_status"], "preliminary_positive")
        self.assertIsInstance(row["screening_cost_ms"], int)
        self.assertNotIn("app_a", row)
        self.assertNotIn("app_b", row)
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
            [
                (
                    row["query_app_id"],
                    row["candidate_app_id"],
                    row["retrieval_score"],
                    row["retrieval_rank"],
                )
                for row in candidate_list
            ],
            [
                ("APP-A", "APP-C", 0.70, 1),
                ("APP-B", "APP-C", 0.70, 2),
                ("APP-A", "APP-B", 0.40, 3),
            ],
        )
        self.assertEqual(candidate_list[0]["retrieval_features_used"], ["code", "resource"])


if __name__ == "__main__":
    unittest.main()

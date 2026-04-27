from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from script import run_libloom_real_quality as rq


def _write_smali(decoded_app: Path, package_name: str, class_name: str = "Demo") -> None:
    package_dir = decoded_app / "smali" / Path(*package_name.split("."))
    package_dir.mkdir(parents=True, exist_ok=True)
    descriptor = "L{}/{};".format(package_name.replace(".", "/"), class_name)
    (package_dir / f"{class_name}.smali").write_text(
        f".class public {descriptor}\n.super Ljava/lang/Object;\n",
        encoding="utf-8",
    )


class BlockedReportTests(unittest.TestCase):
    def test_missing_libloom_home_writes_blocked_report_with_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus_dir = root / "fdroid-corpus-v2-apks"
            corpus_dir.mkdir()
            (corpus_dir / "sample_1.apk").write_bytes(b"fake apk")
            output_path = root / "artifact" / "report.json"

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LIBLOOM_HOME", None)
                report = rq.run_quality(
                    corpus_dir=str(corpus_dir),
                    output_path=str(output_path),
                )

            self.assertEqual(report["status"], "libloom_blocked")
            self.assertEqual(report["reason"], "LIBLOOM_HOME is not set")
            self.assertEqual(report["corpus_size"], 1)
            self.assertEqual(report["n_apks_with_tpl"], 0)
            self.assertEqual(report["precision"], 0.0)
            self.assertEqual(report["recall"], 0.0)
            self.assertEqual(report["coverage"], 0.0)
            self.assertEqual(report["top_detected_tpl"], [])
            self.assertEqual(len(report["per_apk_results"]), 1)
            self.assertTrue(output_path.is_file())
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["status"], "libloom_blocked")


class SyntheticLabelsTests(unittest.TestCase):
    def test_synthetic_labels_use_decoded_smali_and_library_view_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = root / "fdroid-corpus-v2-apks" / "app_1.apk"
            apk_path.parent.mkdir()
            apk_path.write_bytes(b"fake apk")
            decoded_app = root / "fdroid-corpus-v2-decoded" / "app_1"

            for package_name in (
                "okhttp3",
                "okhttp3.internal",
                "okhttp3.internal.cache",
            ):
                _write_smali(decoded_app, package_name)
            for package_name in (
                "com.google.gson",
                "com.google.gson.internal",
            ):
                _write_smali(decoded_app, package_name)

            labels = rq.build_synthetic_labels(
                [apk_path],
                decoded_root=str(root / "fdroid-corpus-v2-decoded"),
            )

            self.assertEqual(labels[apk_path.name]["ground_truth"], ["gson", "okhttp3"])
            self.assertEqual(labels[apk_path.name]["label_source"], "decoded-library_view_v2")


class RealRunMetricsTests(unittest.TestCase):
    def test_run_quality_scores_predictions_and_top_detected_tpl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus_dir = root / "apks"
            corpus_dir.mkdir()
            apk_a = corpus_dir / "a.apk"
            apk_b = corpus_dir / "b.apk"
            apk_a.write_bytes(b"a")
            apk_b.write_bytes(b"b")

            output_path = root / "report.json"
            labels = {
                "a.apk": {
                    "ground_truth": ["okhttp3", "gson"],
                    "label_source": "test",
                    "decoded_dir": None,
                },
                "b.apk": {
                    "ground_truth": [],
                    "label_source": "test",
                    "decoded_dir": None,
                },
            }

            runtime = {
                "status": "available",
                "available": True,
                "reason": "available",
                "version": "test",
                "jar_path": str(root / "LIBLOOM.jar"),
                "libs_profile_dir": str(root / "libs_profile"),
            }

            def detect_fake(apk_path: str, **_kwargs):
                if Path(apk_path).name == "a.apk":
                    return {
                        "status": "ok",
                        "libraries": [
                            {"name": "okhttp3", "version": [], "similarity": 0.9},
                            {"name": "retrofit2", "version": [], "similarity": 0.8},
                        ],
                        "elapsed_sec": 0.1,
                        "error_reason": None,
                    }
                return {
                    "status": "ok",
                    "libraries": [],
                    "elapsed_sec": 0.1,
                    "error_reason": None,
                }

            with mock.patch.object(
                rq.libloom_adapter, "verify_libloom_setup", return_value=runtime
            ), mock.patch.object(
                rq,
                "build_synthetic_labels",
                return_value=labels,
            ), mock.patch.object(
                rq.libloom_adapter,
                "detect_libraries",
                side_effect=detect_fake,
            ):
                report = rq.run_quality(
                    corpus_dir=str(corpus_dir),
                    output_path=str(output_path),
                    decoded_root=None,
                    timeout_sec=10,
                    java_heap_mb=128,
                )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["corpus_size"], 2)
            self.assertEqual(report["n_apks_with_tpl"], 1)
            self.assertAlmostEqual(report["coverage"], 0.5)
            self.assertAlmostEqual(report["precision"], 0.5)
            self.assertAlmostEqual(report["recall"], 0.5)
            self.assertEqual(
                report["top_detected_tpl"],
                [
                    {"tpl": "okhttp3", "count": 1},
                    {"tpl": "retrofit2", "count": 1},
                ],
            )


if __name__ == "__main__":
    unittest.main()

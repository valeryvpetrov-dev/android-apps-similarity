from __future__ import annotations

import json
import os
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from script import run_libloom_real_quality as rq


REAL_FDROID_APK_ROOT = (
    Path.home() / "Library" / "Caches" / "phd-shared" / "datasets" / "fdroid-corpus-v2-apks"
)
REAL_FDROID_DECODED_ROOT = (
    Path.home() / "Library" / "Caches" / "phd-shared" / "datasets" / "fdroid-corpus-v2-decoded"
)
REAL_MINI_TARGET_TPLS = {"okhttp3", "retrofit2", "gson"}


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


class Noise30ReportContractTests(unittest.TestCase):
    def test_noise30_defaults_point_to_new_artifact(self) -> None:
        self.assertEqual(rq.RUN_ID, "NOISE-30-LIBLOOM-REAL-QUALITY")
        self.assertEqual(
            rq.DEFAULT_OUTPUT,
            rq.PROJECT_ROOT
            / "experiments"
            / "artifacts"
            / "NOISE-30-LIBLOOM-REAL-QUALITY"
            / "report.json",
        )

    def test_available_report_includes_libloom_sha_and_profile_file_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus_dir = root / "apks"
            corpus_dir.mkdir()
            apk_path = corpus_dir / "sample.apk"
            apk_path.write_bytes(b"apk")

            jar_path = root / "LIBLOOM.jar"
            jar_bytes = b"real-ish libloom jar"
            jar_path.write_bytes(jar_bytes)
            profile_dir = root / "libs_profile"
            profile_dir.mkdir()
            (profile_dir / "okhttp-4.12.0.txt").write_text("okhttp", encoding="utf-8")
            (profile_dir / "gson-2.11.0.txt").write_text("gson", encoding="utf-8")

            runtime = {
                "status": "available",
                "available": True,
                "reason": "available",
                "version": "test",
                "jar_path": str(jar_path),
                "libs_profile_dir": str(profile_dir),
            }

            with mock.patch.object(
                rq.libloom_adapter, "verify_libloom_setup", return_value=runtime
            ), mock.patch.object(
                rq,
                "build_synthetic_labels",
                return_value={
                    apk_path.name: {
                        "ground_truth": ["okhttp3"],
                        "label_source": "test",
                        "decoded_dir": None,
                    }
                },
            ), mock.patch.object(
                rq.libloom_adapter,
                "detect_libraries",
                return_value={
                    "status": "ok",
                    "libraries": [{"name": "okhttp-4.12.0", "version": [], "similarity": 1.0}],
                    "elapsed_sec": 0.1,
                    "error_reason": None,
                },
            ):
                report = rq.run_quality(
                    corpus_dir=str(corpus_dir),
                    output_path=str(root / "report.json"),
                )

            self.assertEqual(
                report["libloom_jar_sha"],
                hashlib.sha256(jar_bytes).hexdigest(),
            )
            self.assertEqual(report["libs_profile_size"], 2)
            self.assertEqual(report["precision"], 1.0)
            self.assertEqual(report["recall"], 1.0)

    def test_run_quality_can_use_explicit_libs_profile_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus_dir = root / "apks"
            corpus_dir.mkdir()
            apk_path = corpus_dir / "sample.apk"
            apk_path.write_bytes(b"apk")

            jar_path = root / "LIBLOOM.jar"
            jar_path.write_bytes(b"jar")
            default_profile = root / "default_profile"
            default_profile.mkdir()
            (default_profile / "old.txt").write_text("old", encoding="utf-8")
            override_profile = root / "override_profile"
            override_profile.mkdir()
            (override_profile / "new-1.txt").write_text("new", encoding="utf-8")
            (override_profile / "new-2.txt").write_text("new", encoding="utf-8")

            runtime = {
                "status": "available",
                "available": True,
                "reason": "available",
                "version": "test",
                "jar_path": str(jar_path),
                "libs_profile_dir": str(override_profile),
            }

            with mock.patch.object(
                rq.libloom_adapter, "verify_libloom_setup", return_value=runtime
            ) as verify_mock, mock.patch.object(
                rq,
                "build_synthetic_labels",
                return_value={
                    apk_path.name: {
                        "ground_truth": [],
                        "label_source": "test",
                        "decoded_dir": None,
                    }
                },
            ), mock.patch.object(
                rq.libloom_adapter,
                "detect_libraries",
                return_value={
                    "status": "ok",
                    "libraries": [],
                    "elapsed_sec": 0.1,
                    "error_reason": None,
                },
            ):
                report = rq.run_quality(
                    corpus_dir=str(corpus_dir),
                    output_path=str(root / "report.json"),
                    libs_profile_dir=str(override_profile),
                )

            verify_mock.assert_called_once_with(libs_profile_dir=str(override_profile))
            self.assertEqual(report["libs_profile_size"], 2)
            self.assertEqual(
                report["source"]["libs_profile_dir"],
                str(override_profile),
            )

    def test_canonicalizes_versioned_maven_profile_names(self) -> None:
        self.assertEqual(rq.canonicalize_tpl("okhttp-4.12.0"), "okhttp3")
        self.assertEqual(rq.canonicalize_tpl("retrofit-2.11.0"), "retrofit2")
        self.assertEqual(rq.canonicalize_tpl("gson-2.11.0"), "gson")
        self.assertEqual(rq.canonicalize_tpl("material-1.12.0"), "material-components")
        self.assertEqual(rq.canonicalize_tpl("dagger-2.56.2"), "dagger2")
        self.assertEqual(rq.canonicalize_tpl("kotlin-stdlib-jdk7-2.0.0"), "kotlin-stdlib")
        self.assertEqual(rq.canonicalize_tpl("media3-exoplayer-1.9.0"), "androidx-media3")
        self.assertEqual(rq.canonicalize_tpl("work-runtime-2.10.0"), "androidx-workmanager")


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


def _select_real_mini_apks() -> tuple[list[Path], set[str]]:
    if not REAL_FDROID_APK_ROOT.is_dir() or not REAL_FDROID_DECODED_ROOT.is_dir():
        raise unittest.SkipTest("F-Droid v2 APK/decoded corpus is unavailable")

    selected: list[Path] = []
    covered: set[str] = set()
    fallback_selected: list[Path] = []
    fallback_covered: set[str] = set()

    for apk_path in sorted(REAL_FDROID_APK_ROOT.glob("*.apk")):
        decoded_dir = REAL_FDROID_DECODED_ROOT / apk_path.stem
        if not decoded_dir.is_dir():
            continue
        packages = rq._extract_packages_from_decoded_dir(decoded_dir)
        detections = rq.library_view_v2.detect_tpl_in_packages(packages)
        labels = {
            rq.canonicalize_tpl(tpl_id)
            for tpl_id, info in detections.items()
            if isinstance(info, dict) and info.get("detected")
        }
        hits = labels & REAL_MINI_TARGET_TPLS
        if not hits:
            continue
        if REAL_MINI_TARGET_TPLS <= hits:
            return [apk_path], hits
        fallback_selected.append(apk_path)
        fallback_covered |= hits
        if REAL_MINI_TARGET_TPLS <= fallback_covered:
            return fallback_selected, fallback_covered

    if fallback_selected:
        return fallback_selected, fallback_covered
    raise unittest.SkipTest("no real APK with target TPL labels found")


class RealLibloomMiniCorpusTests(unittest.TestCase):
    _report: dict | None = None
    _expected_target_tpls: set[str] = set()

    @classmethod
    def _real_report(cls) -> dict:
        if cls._report is not None:
            return cls._report

        runtime = rq.libloom_adapter.verify_libloom_setup()
        if not runtime.get("available"):
            raise unittest.SkipTest(f"LIBLOOM unavailable: {runtime.get('reason')}")

        selected_apks, expected_target_tpls = _select_real_mini_apks()
        tmp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp_dir.cleanup)
        root = Path(tmp_dir.name)
        mini_corpus = root / "apks"
        mini_corpus.mkdir()
        for apk_path in selected_apks:
            shutil.copy2(apk_path, mini_corpus / apk_path.name)

        timeout_sec = int(os.environ.get("LIBLOOM_REAL_TEST_TIMEOUT_SEC", "600"))
        cls._report = rq.run_quality(
            corpus_dir=str(mini_corpus),
            output_path=str(root / "report.json"),
            decoded_root=str(REAL_FDROID_DECODED_ROOT),
            timeout_sec=timeout_sec,
            java_heap_mb=2048,
        )
        cls._expected_target_tpls = expected_target_tpls
        return cls._report

    def test_real_libloom_mini_corpus_precision_and_recall_are_nonzero(self) -> None:
        report = self._real_report()
        self.assertEqual(report["status"], "ok")
        self.assertGreater(report["precision"], 0.0)
        self.assertGreater(report["recall"], 0.0)

    def test_real_libloom_mini_corpus_coverage_is_nonzero(self) -> None:
        report = self._real_report()
        self.assertGreater(report["coverage"], 0.0)
        self.assertGreater(report["n_apks_with_tpl"], 0)

    def test_real_libloom_top_detected_tpl_contains_target_tpls_present_in_apk(self) -> None:
        report = self._real_report()
        top_names = {entry["tpl"] for entry in report["top_detected_tpl"]}
        self.assertTrue(
            self._expected_target_tpls <= top_names,
            f"missing target TPLs: {sorted(self._expected_target_tpls - top_names)}",
        )


if __name__ == "__main__":
    unittest.main()

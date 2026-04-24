#!/usr/bin/env python3
"""Integration tests for the full e2e similarity pipeline (E-E2E-SMOKE-001).

Covers:
  - test_e2e_pipeline_runs_with_all_mocked: all stages complete without
    raising, regardless of external tool availability (androguard, LIBLOOM,
    apktool).
  - test_e2e_pipeline_preserves_per_view_scores: screening.per_view_scores
    propagates into deepening.prior_per_view_scores (contract linkage).
  - test_e2e_pipeline_records_timings: every stage carries a non-negative
    elapsed_ms measurement.

Heavy external dependencies are mocked:
  - APKiD via apkid_adapter.apkid_available / detect_classifiers.
  - LIBLOOM via libloom_adapter.detect_libraries.
  - apktool + androguard via deepening_runner.resolve_or_materialize_decoded_dir
    and deepening_runner.load_enhanced_features.
  - GED via pairwise_runner.calculate_pair_scores.

Run:
    python3 -m unittest script.test_e2e_pipeline
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# The orchestrator lives outside the submodule; we import it via an explicit
# sys.path injection so the test can run from any cwd.
_PHD_ROOT_CANDIDATES = [
    SCRIPT_DIR.parent.parent / "wave17-B-phd",
    SCRIPT_DIR.parent.parent / "phd",
    SCRIPT_DIR.parent.parent.parent.parent,
]
PHD_ROOT = next(
    (
        candidate
        for candidate in _PHD_ROOT_CANDIDATES
        if (candidate / "experiments" / "scripts" / "run_e2e_smoke.py").is_file()
    ),
    _PHD_ROOT_CANDIDATES[-1],
)
SMOKE_DIR = PHD_ROOT / "experiments" / "scripts"
if str(SMOKE_DIR) not in sys.path:
    sys.path.insert(0, str(SMOKE_DIR))

# Modules under test.
import deepening_runner  # noqa: E402
import pairwise_runner  # noqa: E402
import run_e2e_smoke  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_fake_apk(apk_path: Path, manifest_bytes: bytes | None = None) -> None:
    """Build a minimal APK-shaped zip with classes.dex + AndroidManifest.xml."""
    if manifest_bytes is None:
        manifest_bytes = (
            b"<?xml version='1.0' encoding='utf-8'?>"
            b'<manifest package="com.example.test" android:versionCode="1">'
            b"<application/>"
            b"</manifest>"
        )
    # dex magic: "dex\n035\0"
    dex_bytes = b"dex\n035\x00" + b"\x00" * 64
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest_bytes)
        archive.writestr("classes.dex", dex_bytes)
        archive.writestr("res/layout/main.xml", b"<LinearLayout/>")
        archive.writestr("META-INF/CERT.RSA", b"fake-cert")


def _write_cascade_config(path: Path) -> None:
    # Keep it stdlib-parseable: both the submodule simple-YAML parsers accept this.
    path.write_text(
        (
            "stages:\n"
            "  screening:\n"
            "    features: [code, metadata]\n"
            "    metric: jaccard\n"
            "    threshold: 0.0\n"
            "  deepening:\n"
            "    features: [code]\n"
            "  pairwise:\n"
            "    features: [code, metadata]\n"
            "    metric: jaccard\n"
            "    threshold: 0.0\n"
        ),
        encoding="utf-8",
    )


def _mocked_enhanced_features() -> dict:
    # Shape follows m_static_views.extract_all_features so deepening accepts it.
    return {
        "mode": "enhanced",
        "component": {
            "activities": [],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": set(),
            "features": set(),
        },
        "resource": {"resource_digests": set()},
        "library": {"libraries": {}},
        "code": set(),
        "metadata": set(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestE2EPipeline(unittest.TestCase):
    """Integration tests for the e2e similarity pipeline."""

    def setUp(self) -> None:
        # Pipeline тесты мокают тяжёлые зависимости (androguard, apktool, LIBLOOM),
        # но верификатор обязательных зависимостей в runner'ах (вызывается из
        # run_screening/run_deepening/run_pairwise) срабатывает до моков и падает
        # на хосте без androguard. Выставляем SIMILARITY_SKIP_REQ_CHECK=1, чтобы
        # отключить fail-fast именно в этом интеграционном прогоне. Отдельные
        # тесты fail-fast поведения живут в test_verify_deps_wiring.py.
        self._saved_skip_env = os.environ.get("SIMILARITY_SKIP_REQ_CHECK")
        os.environ["SIMILARITY_SKIP_REQ_CHECK"] = "1"

    def tearDown(self) -> None:
        if self._saved_skip_env is None:
            os.environ.pop("SIMILARITY_SKIP_REQ_CHECK", None)
        else:
            os.environ["SIMILARITY_SKIP_REQ_CHECK"] = self._saved_skip_env

    def _run_pipeline(self, tmpdir: Path) -> dict:
        apk_query = tmpdir / "query.apk"
        apk_candidate = tmpdir / "candidate.apk"
        _write_fake_apk(apk_query)
        _write_fake_apk(apk_candidate)

        config_path = tmpdir / "cascade.yaml"
        _write_cascade_config(config_path)

        # Mock APKiD adapter pieces so noise_cleanup doesn't require the real tool.
        fake_classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": ["dx"],
            "anti_debug": [],
            "anti_vm": [],
            "manipulators": [],
            "apkid_version": "3.1.0-test",
            "rules_sha256": "deadbeef",
            "status": "ok",
            "elapsed_sec": 0.01,
            "raw_stdout": "{}",
        }

        # Mock deepening heavy parts: apktool + androguard-based features.
        # resolve_or_materialize_decoded_dir is called once per side (a/b),
        # but we run the pipeline twice in some tests so we cycle values.
        def _fake_decode(
            candidate, app, side, apk_path, decoded_cache
        ):  # signature must match deepening_runner
            fake_dir = str(tmpdir / "decoded-{}".format(side))
            decoded_cache[apk_path] = fake_dir
            return fake_dir

        # Mock pairwise heavy part: produce a plausible score so contracts fill.
        def _fake_pair_scores(
            apk_a,
            apk_b,
            decoded_a,
            decoded_b,
            selected_layers,
            metric,
            ins_block_sim_threshold,
            ged_timeout_sec,
            processes_count,
            threads_count,
            layer_cache,
            code_cache,
        ):
            return 0.85, 0.80, list(selected_layers)

        with mock.patch(
            "apkid_adapter.apkid_available", return_value=True
        ), mock.patch(
            "apkid_adapter.detect_classifiers", return_value=fake_classification
        ), mock.patch(
            "libloom_adapter.detect_libraries",
            return_value={
                "status": "ok",
                "libraries": [
                    {"name": "androidx.compose", "version": ["1.0"], "similarity": 0.95}
                ],
                "unknown_packages": [],
                "elapsed_sec": 0.1,
                "raw_stdout": None,
                "raw_stderr": None,
                "appname": "query",
                "error_reason": None,
            },
        ), mock.patch(
            "libloom_adapter.libloom_available", return_value=True
        ), mock.patch.object(
            deepening_runner,
            "resolve_or_materialize_decoded_dir",
            side_effect=_fake_decode,
        ), mock.patch.object(
            deepening_runner,
            "load_enhanced_features",
            return_value=_mocked_enhanced_features(),
        ), mock.patch.object(
            deepening_runner,
            "build_code_layer",
            side_effect=lambda apk_path, cache: (5, False),
        ), mock.patch.object(
            pairwise_runner,
            "calculate_pair_scores",
            side_effect=_fake_pair_scores,
        ):
            # Force a usable "jar" + "profile dir" for LIBLOOM gating, but the
            # adapter functions are mocked above so no real java is invoked.
            fake_jar = tmpdir / "LIBLOOM.jar"
            fake_jar.write_bytes(b"\x00\x01\x02")
            fake_profile = tmpdir / "libs_profile"
            fake_profile.mkdir()
            (fake_profile / "lib1.json").write_text("{}", encoding="utf-8")

            report = run_e2e_smoke.run_e2e_smoke(
                apk_query=apk_query,
                apk_candidate=apk_candidate,
                config_path=config_path,
                libloom_jar=fake_jar,
                libloom_profile_dir=fake_profile,
                apkid_timeout_sec=10,
            )
        return report

    # --- test 1: pipeline goes end-to-end without exceptions ---------------

    def test_e2e_pipeline_runs_with_all_mocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_test_") as tmp:
            tmpdir = Path(tmp)
            report = self._run_pipeline(tmpdir)

        stages_by_name = {stage["name"]: stage for stage in report["stages"]}
        expected = {
            "noise_cleanup",
            "screening",
            "deepening",
            "pairwise",
            "interpretation",
        }
        self.assertEqual(set(stages_by_name.keys()), expected)

        # Every stage must be marked ok=True (not skipped, not failed) because
        # all external deps are mocked.
        for name, stage in stages_by_name.items():
            self.assertTrue(
                stage["ok"],
                msg="stage {} not ok: {}".format(name, stage),
            )
            self.assertFalse(
                stage["skipped"],
                msg="stage {} skipped unexpectedly: {}".format(name, stage),
            )

        # Final pair result must carry a similarity score.
        self.assertIsNotNone(report["final_similarity_score"])
        self.assertEqual(report["final_verdict"], "success")
        self.assertIsNotNone(report["apk_query_sha256"])
        self.assertIsNotNone(report["apk_candidate_sha256"])

    # --- test 2: screening.per_view_scores -> deepening.prior_per_view_scores

    def test_e2e_pipeline_preserves_per_view_scores(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_test_") as tmp:
            tmpdir = Path(tmp)
            report = self._run_pipeline(tmpdir)

        stages_by_name = {stage["name"]: stage for stage in report["stages"]}
        screening_stage = stages_by_name["screening"]
        deepening_stage = stages_by_name["deepening"]

        # Screening must have produced per_view_scores on its candidate row.
        self.assertIn(
            "has_per_view_scores=True",
            screening_stage["notes"],
            msg="screening notes missing per_view_scores flag: {}".format(
                screening_stage["notes"]
            ),
        )

        # Deepening must carry prior_per_view_scores across into enriched row.
        self.assertIn(
            "has_prior_per_view_scores=True",
            deepening_stage["notes"],
            msg="deepening notes missing prior_per_view_scores flag: {}".format(
                deepening_stage["notes"]
            ),
        )

        # Contract link must be declared.
        link_fields = {link["field"] for link in report["contract_links"]}
        self.assertTrue(
            any("per_view_scores -> prior_per_view_scores" in field for field in link_fields),
            msg="contract_links missing per_view_scores linkage: {}".format(link_fields),
        )

    # --- test 3: shortcut survives deepening and skips heavy pairwise -------

    def test_e2e_shortcut_survives_deepening_to_pairwise(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_shortcut_") as tmp:
            tmpdir = Path(tmp)
            apk_a = tmpdir / "a.apk"
            apk_b = tmpdir / "b.apk"
            _write_fake_apk(apk_a)
            _write_fake_apk(apk_b)

            screening_candidate = {
                "app_a": {"app_id": "com.example.a", "apk_path": str(apk_a)},
                "app_b": {"app_id": "com.example.b", "apk_path": str(apk_b)},
                "shortcut_applied": True,
                "shortcut_reason": pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
                "signature_match": {
                    "status": "match",
                    "score": 1.0,
                    "cert_hash": "same-cert",
                },
            }

            deepened = deepening_runner.enrich_candidate(
                candidate=screening_candidate,
                layers_to_enrich=[],
                code_cache={},
                decoded_cache={},
                feature_cache={},
            )

            self.assertIs(deepened.get("shortcut_applied"), True)
            self.assertEqual(
                deepened.get("shortcut_reason"),
                pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
            )
            self.assertEqual(deepened.get("signature_match", {}).get("status"), "match")

            with mock.patch.object(
                pairwise_runner,
                "calculate_pair_scores",
                side_effect=AssertionError("heavy pairwise path must be skipped"),
            ) as calculate_pair_scores:
                pair_row = pairwise_runner._compute_pair_row_with_caches(
                    candidate=deepened,
                    selected_layers=["code", "metadata"],
                    metric="jaccard",
                    threshold=0.0,
                    ins_block_sim_threshold=0.80,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=1,
                    layer_cache={},
                    code_cache={},
                    apk_discovery_cache={},
                )

            calculate_pair_scores.assert_not_called()
            self.assertEqual(
                pair_row["deep_verification_status"],
                pairwise_runner.DEEP_VERIFICATION_STATUS_SKIPPED,
            )
            self.assertLessEqual(pair_row["elapsed_ms_deep"], 10)
            self.assertIs(pair_row["shortcut_applied"], True)

    # --- test 4: every stage records a timing ------------------------------

    def test_e2e_pipeline_records_timings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_test_") as tmp:
            tmpdir = Path(tmp)
            report = self._run_pipeline(tmpdir)

        for stage in report["stages"]:
            self.assertIn("elapsed_ms", stage)
            self.assertIsInstance(stage["elapsed_ms"], int)
            self.assertGreaterEqual(
                stage["elapsed_ms"],
                0,
                msg="stage {} has negative elapsed_ms: {}".format(
                    stage["name"], stage["elapsed_ms"]
                ),
            )

        self.assertIn("e2e_elapsed_ms", report)
        self.assertIsInstance(report["e2e_elapsed_ms"], int)
        self.assertGreaterEqual(report["e2e_elapsed_ms"], 0)
        # Total e2e time should be at least the sum of measured stage times
        # (minus a small tolerance for measurement rounding).
        total_stage_ms = sum(stage["elapsed_ms"] for stage in report["stages"])
        # Allow 100ms tolerance on both sides for rounding + orchestration overhead.
        self.assertGreaterEqual(report["e2e_elapsed_ms"] + 100, total_stage_ms)


if __name__ == "__main__":
    unittest.main()

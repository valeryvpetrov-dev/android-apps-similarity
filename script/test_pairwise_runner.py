#!/usr/bin/env python3
"""Tests for pairwise_runner enhanced decoded-layer path."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
from feature_cache_sqlite import FeatureCacheSqlite


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


class TestPairwiseRunnerEnhanced(unittest.TestCase):
    def test_run_pairwise_supports_quick_layers_without_decoded_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            feature_cache_path = root / "feature-cache.sqlite"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            apk_a.write_bytes(b"fake_apk_a")
            apk_b.write_bytes(b"fake_apk_b")

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, metadata]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "quick",
                "code": {"dex:classes.dex"},
                "metadata": {"manifest_present:1", "signing_present:1"},
                "component": {"manifest_component:com.example.MainActivity"},
                "resource": {"res_type:layout", "res_ext:xml"},
                "library": {"meta_inf_ext:RSA"},
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ) as features_mock:
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                    feature_cache_path=feature_cache_path,
                )

        result = payload[0]
        self.assertEqual(features_mock.call_count, 2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["views_used"], ["code", "metadata"])
        self.assertAlmostEqual(result["full_similarity_score"], 1.0)
        self.assertAlmostEqual(result["library_reduced_score"], 1.0)

    def test_run_pairwise_passes_cached_feature_bundles_to_semantic_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            feature_cache_path = root / "feature-cache.sqlite"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            apk_a.write_bytes(b"fake_apk_a")
            apk_b.write_bytes(b"fake_apk_b")

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, resource]
    metric: jaccard
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle_a = {
                "mode": "quick",
                "code": {"dex:classes.dex", "method:a"},
                "metadata": set(),
                "component": {},
                "resource": {"res_type:layout"},
                "library": set(),
                "code_v4": {"method_ids": ["A.m"], "method_fingerprints": {"A.m": "fp-a"}},
            }
            feature_bundle_b = {
                "mode": "quick",
                "code": {"dex:classes.dex", "method:b"},
                "metadata": set(),
                "component": {},
                "resource": {"res_type:layout"},
                "library": set(),
                "code_v4": {"method_ids": ["B.m"], "method_fingerprints": {"B.m": "fp-b"}},
            }

            semantic_result = {
                "profile_id": "R_semantic_multiview_decision_policy_v0",
                "status": "success",
                "scores": {},
            }
            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle_a, feature_bundle_b],
            ), mock.patch.object(
                pairwise_runner,
                "run_semantic_multiview_check",
                return_value=semantic_result,
            ) as semantic_mock:
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                    feature_cache_path=feature_cache_path,
                )

        self.assertEqual(payload[0]["semantic_multiview"], semantic_result)
        semantic_mock.assert_called_once()
        kwargs = semantic_mock.call_args.kwargs
        self.assertEqual(kwargs["feature_bundle_a"], feature_bundle_a)
        self.assertEqual(kwargs["feature_bundle_b"], feature_bundle_b)

    def test_run_pairwise_keeps_backward_compat_with_mocked_calculate_pair_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, metadata]
    metric: jaccard
    threshold: 0.0
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_pair_scores(
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

            with mock.patch.object(
                pairwise_runner,
                "calculate_pair_scores",
                side_effect=fake_pair_scores,
            ):
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        result = payload[0]
        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["full_similarity_score"], 0.85)
        self.assertAlmostEqual(result["library_reduced_score"], 0.80)

    def test_run_pairwise_uses_decoded_layers_for_non_code_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource, library]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {
                                    "app_id": "A",
                                    "apk_path": str(apk_a),
                                    "decoded_dir": "/tmp/decoded-a",
                                },
                                "app_b": {
                                    "app_id": "B",
                                    "apk_path": str(apk_b),
                                    "decoded_dir": "/tmp/decoded-b",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {"androidx.appcompat": {"class_count": 10}},
                },
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ) as features_mock:
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        self.assertEqual(features_mock.call_count, 2)
        result = payload[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["views_used"], ["component", "resource", "library"])
        self.assertAlmostEqual(result["full_similarity_score"], 1.0)
        self.assertAlmostEqual(result["library_reduced_score"], 1.0)

    def test_run_pairwise_uses_quick_apk_features_when_decoded_layers_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "quick",
                "code": set(),
                "metadata": set(),
                "component": {"activity:com.example.MainActivity"},
                "resource": {"res/layout/main.xml", "res/values/strings.xml"},
                "library": set(),
            }

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ) as features_mock:
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        result = payload[0]
        self.assertEqual(features_mock.call_count, 2)
        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["full_similarity_score"], 1.0)
        self.assertAlmostEqual(result["library_reduced_score"], 1.0)

    def test_run_pairwise_discovers_shared_apk_and_decoded_dirs_by_app_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shared_root = root / "shared"
            apk_dir = shared_root / "datasets" / "fdroid-corpus-v2-apks"
            decoded_root = shared_root / "datasets" / "fdroid-corpus-v2-decoded"
            apk_dir.mkdir(parents=True)
            decoded_root.mkdir(parents=True)

            apk_a = apk_dir / "app.alpha.apk"
            apk_b = apk_dir / "app.beta.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            (decoded_root / "app.alpha").mkdir()
            (decoded_root / "app.beta").mkdir()
            (decoded_root / "app.alpha" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
            (decoded_root / "app.beta" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")

            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "app.alpha"},
                                "app_b": {"app_id": "app.beta"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {},
                },
            }

            with mock.patch.dict("os.environ", {"PHD_SHARED_DATA_ROOT": str(shared_root)}), mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ):
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                )

        result = payload[0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["views_used"], ["component", "resource"])

    def test_pair_worker_reuses_sqlite_cache_across_independent_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packaged_pairwise_runner = importlib.import_module("script.pairwise_runner")
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            cache_path = root / "feature-cache.sqlite"
            apk_a.write_bytes(b"fake_apk_a")
            apk_b.write_bytes(b"fake_apk_b")

            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [component, resource, library]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            candidate = {
                "app_a": {
                    "app_id": "A",
                    "apk_path": str(apk_a),
                    "decoded_dir": "/tmp/decoded-a",
                },
                "app_b": {
                    "app_id": "B",
                    "apk_path": str(apk_b),
                    "decoded_dir": "/tmp/decoded-b",
                },
            }
            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": {"android.permission.INTERNET"},
                    "features": set(),
                },
                "resource": {
                    "resource_digests": {("res/layout/main.xml", "digest-1")},
                },
                "library": {
                    "libraries": {"androidx.appcompat": {"class_count": 10}},
                },
            }

            with mock.patch.dict("os.environ", {"FEATURE_CACHE_PATH": str(cache_path)}), mock.patch.object(
                packaged_pairwise_runner,
                "extract_all_features",
                side_effect=[feature_bundle, feature_bundle],
            ) as features_mock:
                row_one = json.loads(
                    packaged_pairwise_runner._pair_worker_isolated(
                        json.dumps(candidate),
                        str(config_path),
                        0.8,
                        30,
                        1,
                        2,
                    )
                )
                row_two = json.loads(
                    packaged_pairwise_runner._pair_worker_isolated(
                        json.dumps(candidate),
                        str(config_path),
                        0.8,
                        30,
                        1,
                        2,
                    )
                )

        self.assertEqual(features_mock.call_count, 2)
        self.assertEqual(row_one["status"], "success")
        self.assertEqual(row_two["status"], "success")


class TestPairwiseFeatureCacheQuarantine(unittest.TestCase):
    @staticmethod
    def _fresh_bundle() -> dict:
        return {
            "mode": "quick",
            "code": {"method_id:Lcom/example/App;->run()V"},
            "metadata": {"manifest_present:1"},
            "component": set(),
            "resource": set(),
            "library": set(),
        }

    def test_pairwise_skips_v1_bundle_with_rejected_active_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_path = root / "sample.apk"
            apk_path.write_bytes(b"apk-fixture")
            cache = FeatureCacheSqlite(root / "feature-cache.sqlite")
            apk_sha256 = pairwise_runner._sha256_of_file(apk_path)
            cache.put(
                apk_sha256,
                "v1",
                {
                    "mode": "quick",
                    "code": {"method_namespace:Lcom/example"},
                    "metadata": {"apk_name:legacy"},
                },
            )
            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                return_value=self._fresh_bundle(),
            ) as extract_features:
                layers = pairwise_runner.load_layers_for_pairwise(
                    str(apk_path),
                    None,
                    ["code", "metadata"],
                    {},
                    feature_cache=cache,
                )
            cache.close()

        self.assertEqual(
            layers["code"],
            {"method_id:Lcom/example/App;->run()V"},
        )
        extract_features.assert_called_once()

    def test_pairwise_rejects_current_cache_bundle_with_rejected_active_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_path = root / "sample.apk"
            apk_path.write_bytes(b"apk-fixture")
            cache = FeatureCacheSqlite(root / "feature-cache.sqlite")
            cache.put(
                pairwise_runner._sha256_of_file(apk_path),
                pairwise_runner.FEATURE_CACHE_VERSION,
                {
                    "mode": "quick",
                    "code": {"method_namespace:Lcom/example"},
                    "metadata": set(),
                },
            )
            with self.assertRaisesRegex(
                pairwise_runner.PairwiseAnalysisError,
                "rejected_active_token:code:method_namespace:",
            ):
                pairwise_runner.load_layers_for_pairwise(
                    str(apk_path),
                    None,
                    ["code"],
                    {},
                    feature_cache=cache,
                )
            cache.close()

    def test_run_pairwise_preserves_quarantine_reason_from_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            enriched_path = root / "enriched.json"
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            write_text(
                config_path,
                """
stages:
  pairwise:
    features: [code, metadata]
    metric: cosine
    threshold: 0.10
""".strip(),
            )
            enriched_path.write_text(
                json.dumps(
                    {
                        "enriched_candidates": [
                            {
                                "app_a": {"app_id": "A", "apk_path": str(apk_a)},
                                "app_b": {"app_id": "B", "apk_path": str(apk_b)},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                side_effect=pairwise_runner.RejectedActiveTokenError(
                    "rejected_active_token:code:method_namespace:"
                ),
            ):
                payload = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    processes_count=1,
                    threads_count=2,
                )

        result = payload[0]
        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["full_similarity_score"])
        self.assertIsNone(result["library_reduced_score"])
        self.assertEqual(
            result["analysis_failed_reason"],
            "rejected_active_token:code:method_namespace:",
        )


if __name__ == "__main__":
    unittest.main()

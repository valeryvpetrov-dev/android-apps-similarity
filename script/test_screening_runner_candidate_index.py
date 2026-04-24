#!/usr/bin/env python3
"""Tests for screening_runner MinHash/LSH candidate_index integration (EXEC-084)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import (
    build_candidate_list,
    extract_candidate_index_params,
    run_screening,
)


def _build_app_record(app_id: str, code_features: set[str], resource_features: set[str]) -> dict:
    return {
        "app_id": app_id,
        "layers": {
            "code": set(code_features),
            "component": set(),
            "resource": set(resource_features),
            "metadata": set(),
            "library": set(),
        },
    }


def _write_config(tmpdir: Path, candidate_index_block: str | None, filename: str = "cascade.yaml") -> Path:
    config_text_lines = [
        "cascade_config_version: v1",
        "",
        "static_application_model:",
        "  name: M_static",
        "  layers: [code, component, resource, metadata, library]",
        "",
        "stages:",
        "  screening:",
        "    features: [code, resource]",
        "    metric: jaccard",
        "    threshold: 0.10",
        "    interpretation_mode: none",
    ]
    if candidate_index_block is not None:
        config_text_lines.append(candidate_index_block)
    config_text_lines += [
        "  deepening:",
        "    features: [code]",
        "    metric: cosine",
        "    threshold: 0.40",
        "    interpretation_mode: none",
        "  pairwise:",
        "    features: [code]",
        "    metric: ged",
        "    threshold: 0.70",
        "    interpretation_mode: none",
        "aggregation:",
        "  strategy: single_stage_score",
        "  weights: {}",
    ]
    config_text = "\n".join(config_text_lines) + "\n"
    config_path = tmpdir / filename
    config_path.write_text(config_text, encoding="utf-8")
    return config_path


def _build_small_corpus() -> list[dict]:
    """Build a 6-app corpus with two tight clusters and an outlier.

    Cluster X: APP-X1, APP-X2, APP-X3 share 90%+ of features.
    Cluster Y: APP-Y1, APP-Y2 share 80%+ of features.
    APP-Z is an outlier with disjoint features.
    """
    base_x = {"f_x_{}".format(i) for i in range(40)}
    base_y = {"f_y_{}".format(i) for i in range(40)}
    base_z = {"f_z_{}".format(i) for i in range(40)}

    app_x1 = _build_app_record("APP-X1", base_x, {"r_x"})
    app_x2 = _build_app_record(
        "APP-X2",
        (base_x - {"f_x_0", "f_x_1"}) | {"f_x_extra_1"},
        {"r_x"},
    )
    app_x3 = _build_app_record(
        "APP-X3",
        (base_x - {"f_x_2"}) | {"f_x_extra_2", "f_x_extra_3"},
        {"r_x"},
    )
    app_y1 = _build_app_record("APP-Y1", base_y, {"r_y"})
    app_y2 = _build_app_record(
        "APP-Y2",
        (base_y - {"f_y_0", "f_y_1", "f_y_2", "f_y_3"}) | {"f_y_extra_1", "f_y_extra_2"},
        {"r_y"},
    )
    app_z = _build_app_record("APP-Z", base_z, {"r_z"})
    return [app_x1, app_x2, app_x3, app_y1, app_y2, app_z]


class TestExtractCandidateIndexParams(unittest.TestCase):
    def test_returns_none_when_block_absent(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code"],
                    "metric": "jaccard",
                    "threshold": 0.25,
                }
            }
        }
        self.assertIsNone(
            extract_candidate_index_params(config, default_features=["code"], metric="jaccard")
        )

    def test_parses_minhash_lsh_block(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code", "resource"],
                    "metric": "jaccard",
                    "threshold": 0.25,
                    "candidate_index": {
                        "type": "minhash_lsh",
                        "num_perm": 128,
                        "bands": 32,
                        "seed": 7,
                    },
                }
            }
        }
        params = extract_candidate_index_params(
            config, default_features=["code", "resource"], metric="jaccard"
        )
        self.assertIsNotNone(params)
        assert params is not None  # for type-checker
        self.assertEqual(params["type"], "minhash_lsh")
        self.assertEqual(params["num_perm"], 128)
        self.assertEqual(params["bands"], 32)
        self.assertEqual(params["seed"], 7)
        self.assertEqual(params["features"], ["code", "resource"])

    def test_features_override_is_respected(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code", "resource", "metadata"],
                    "metric": "jaccard",
                    "threshold": 0.25,
                    "candidate_index": {
                        "type": "minhash_lsh",
                        "num_perm": 64,
                        "bands": 32,
                        "features": ["metadata"],
                    },
                }
            }
        }
        params = extract_candidate_index_params(
            config,
            default_features=["code", "resource", "metadata"],
            metric="jaccard",
        )
        assert params is not None
        self.assertEqual(params["features"], ["metadata"])

    def test_non_jaccard_metric_raises(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code"],
                    "metric": "cosine",
                    "threshold": 0.25,
                    "candidate_index": {"type": "minhash_lsh"},
                }
            }
        }
        with self.assertRaises(ValueError):
            extract_candidate_index_params(config, default_features=["code"], metric="cosine")

    def test_unsupported_type_raises(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code"],
                    "metric": "jaccard",
                    "threshold": 0.25,
                    "candidate_index": {"type": "bloom"},
                }
            }
        }
        with self.assertRaises(ValueError):
            extract_candidate_index_params(config, default_features=["code"], metric="jaccard")

    def test_bands_must_divide_num_perm(self) -> None:
        config = {
            "stages": {
                "screening": {
                    "features": ["code"],
                    "metric": "jaccard",
                    "threshold": 0.25,
                    "candidate_index": {
                        "type": "minhash_lsh",
                        "num_perm": 128,
                        "bands": 30,
                    },
                }
            }
        }
        with self.assertRaises(ValueError):
            extract_candidate_index_params(config, default_features=["code"], metric="jaccard")


class TestBuildCandidateListWithLSH(unittest.TestCase):
    def test_build_candidate_list_without_index_keeps_canonical_contract(self) -> None:
        records = _build_small_corpus()
        result = build_candidate_list(
            app_records=records,
            selected_layers=["code", "resource"],
            metric="jaccard",
            threshold=0.10,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params=None,
        )
        pairs = sorted((row["query_app_id"], row["candidate_app_id"]) for row in result)
        # Expect intra-cluster pairs to be present; outlier APP-Z disjoint.
        self.assertIn(("APP-X1", "APP-X2"), pairs)
        self.assertIn(("APP-X1", "APP-X3"), pairs)
        self.assertIn(("APP-X2", "APP-X3"), pairs)
        self.assertIn(("APP-Y1", "APP-Y2"), pairs)
        self.assertNotIn(("APP-X1", "APP-Z"), pairs)
        self.assertTrue(all("app_a" not in row and "app_b" not in row for row in result))

    def test_build_candidate_list_with_lsh_preserves_intra_cluster_pairs(self) -> None:
        records = _build_small_corpus()
        params = {
            "type": "minhash_lsh",
            "num_perm": 128,
            "bands": 32,
            "seed": 42,
            "features": ["code", "resource"],
        }
        result = build_candidate_list(
            app_records=records,
            selected_layers=["code", "resource"],
            metric="jaccard",
            threshold=0.10,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params=params,
        )
        pairs = sorted((row["query_app_id"], row["candidate_app_id"]) for row in result)
        self.assertIn(("APP-X1", "APP-X2"), pairs)
        self.assertIn(("APP-X1", "APP-X3"), pairs)
        self.assertIn(("APP-X2", "APP-X3"), pairs)
        self.assertIn(("APP-Y1", "APP-Y2"), pairs)
        self.assertTrue(all("app_a" not in row and "app_b" not in row for row in result))


class TestRunScreeningIntegration(unittest.TestCase):
    def test_run_screening_backward_compat_no_candidate_index(self) -> None:
        records = _build_small_corpus()
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            # Convert set-valued layers to lists for JSON-serializable records.
            serializable_records = []
            for record in records:
                layers_list = {
                    layer: sorted(values) for layer, values in record["layers"].items()
                }
                serializable_records.append(
                    {"app_id": record["app_id"], "layers": layers_list}
                )
            apps_path = tmpdir / "apps_features.json"
            apps_path.write_text(
                json.dumps({"apps": serializable_records}), encoding="utf-8"
            )

            # Two configs: one without candidate_index, one with.
            config_no_index = _write_config(tmpdir, candidate_index_block=None)
            with mock.patch.dict(os.environ, {"SIMILARITY_SKIP_REQ_CHECK": "1"}):
                result_without_index = run_screening(
                    cascade_config_path=config_no_index,
                    apps_features_json_path=apps_path,
                )
            # Baseline: exact O(n^2) screening. Record pairs passing threshold.
            pairs_without_index = {
                (row["query_app_id"], row["candidate_app_id"]): row["retrieval_score"]
                for row in result_without_index
            }
            self.assertGreater(len(pairs_without_index), 0)
            # Run the same config through the screening_runner once more and
            # confirm identical output — baseline is stable.
            with mock.patch.dict(os.environ, {"SIMILARITY_SKIP_REQ_CHECK": "1"}):
                result_without_index_again = run_screening(
                    cascade_config_path=config_no_index,
                    apps_features_json_path=apps_path,
                )
            self.assertEqual(result_without_index, result_without_index_again)

    def test_run_screening_with_lsh_has_high_recall_on_small_corpus(self) -> None:
        records = _build_small_corpus()
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            serializable_records = []
            for record in records:
                layers_list = {
                    layer: sorted(values) for layer, values in record["layers"].items()
                }
                serializable_records.append(
                    {"app_id": record["app_id"], "layers": layers_list}
                )
            apps_path = tmpdir / "apps_features.json"
            apps_path.write_text(
                json.dumps({"apps": serializable_records}), encoding="utf-8"
            )

            config_no_index = _write_config(
                tmpdir, candidate_index_block=None, filename="cascade_exact.yaml"
            )
            config_with_index = _write_config(
                tmpdir,
                candidate_index_block=(
                    "    candidate_index:\n"
                    "      type: minhash_lsh\n"
                    "      num_perm: 128\n"
                    "      bands: 32\n"
                    "      seed: 42\n"
                    "      features: [code, resource]\n"
                ),
                filename="cascade_with_index.yaml",
            )

            with mock.patch.dict(os.environ, {"SIMILARITY_SKIP_REQ_CHECK": "1"}):
                result_exact = run_screening(
                    cascade_config_path=config_no_index,
                    apps_features_json_path=apps_path,
                )
                result_lsh = run_screening(
                    cascade_config_path=config_with_index,
                    apps_features_json_path=apps_path,
                )

            exact_pairs = {
                (row["query_app_id"], row["candidate_app_id"])
                for row in result_exact
            }
            lsh_pairs = {
                (row["query_app_id"], row["candidate_app_id"])
                for row in result_lsh
            }

            # LSH-screened output is a subset of exact output (since LSH only
            # filters candidate pairs, exact Jaccard runs afterwards).
            self.assertTrue(lsh_pairs.issubset(exact_pairs))

            # Recall >= 0.9 against exact screening baseline.
            if exact_pairs:
                recall = len(lsh_pairs & exact_pairs) / len(exact_pairs)
                self.assertGreaterEqual(
                    recall,
                    0.9,
                    msg="LSH recall {} < 0.9; exact={} lsh={}".format(
                        recall, sorted(exact_pairs), sorted(lsh_pairs)
                    ),
                )


if __name__ == "__main__":
    unittest.main()

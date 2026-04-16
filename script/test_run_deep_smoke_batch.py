#!/usr/bin/env python3
"""Tests for run_deep_smoke_batch.py (DEEP-002)."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script.run_deep_smoke_batch import load_pairs, parse_args, run_batch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CONFIG_YAML = textwrap.dedent("""\
    stages:
      screening:
        features: [code, metadata]
        metric: jaccard
        threshold: 0.3
      pairwise:
        features: [code, metadata]
        metric: jaccard
        threshold: 0.03
""")

MINIMAL_PAIR = {
    "app_a": {"app_id": "app_a", "apk_path": "/fake/a.apk"},
    "app_b": {"app_id": "app_b", "apk_path": "/fake/b.apk"},
}


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_required_args(self):
        args = parse_args(["--pairs", "p.json", "--config", "c.yaml", "--output", "out.json"])
        assert args.pairs == "p.json"
        assert args.config == "c.yaml"
        assert args.output == "out.json"

    def test_defaults(self):
        args = parse_args(["--pairs", "p.json", "--config", "c.yaml", "--output", "out.json"])
        assert args.ins_block_sim_threshold == 0.80
        assert args.ged_timeout_sec == 30

    def test_override_defaults(self):
        args = parse_args([
            "--pairs", "p.json",
            "--config", "c.yaml",
            "--output", "out.json",
            "--ins-block-sim-threshold", "0.5",
            "--ged-timeout-sec", "10",
        ])
        assert args.ins_block_sim_threshold == 0.5
        assert args.ged_timeout_sec == 10

    def test_missing_required_raises(self):
        with pytest.raises(SystemExit):
            parse_args(["--config", "c.yaml", "--output", "out.json"])


# ---------------------------------------------------------------------------
# load_pairs tests
# ---------------------------------------------------------------------------

class TestLoadPairs:
    def test_load_array(self, tmp_path):
        pairs_file = tmp_path / "pairs.json"
        pairs = [MINIMAL_PAIR]
        pairs_file.write_text(json.dumps(pairs), encoding="utf-8")
        result = load_pairs(pairs_file)
        assert result == pairs

    def test_load_wrapped_object(self, tmp_path):
        pairs_file = tmp_path / "pairs.json"
        payload = {"enriched_candidates": [MINIMAL_PAIR]}
        pairs_file.write_text(json.dumps(payload), encoding="utf-8")
        result = load_pairs(pairs_file)
        assert result == [MINIMAL_PAIR]

    def test_empty_array(self, tmp_path):
        pairs_file = tmp_path / "pairs.json"
        pairs_file.write_text("[]", encoding="utf-8")
        result = load_pairs(pairs_file)
        assert result == []

    def test_invalid_json_raises(self, tmp_path):
        pairs_file = tmp_path / "pairs.json"
        pairs_file.write_text("not json", encoding="utf-8")
        with pytest.raises(Exception):
            load_pairs(pairs_file)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_pairs(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# run_batch tests
# ---------------------------------------------------------------------------

class TestRunBatch:
    def _write_config(self, tmp_path: Path, content: str = VALID_CONFIG_YAML) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(content, encoding="utf-8")
        return config_path

    def _write_pairs(self, tmp_path: Path, pairs: list) -> Path:
        pairs_path = tmp_path / "pairs.json"
        pairs_path.write_text(json.dumps(pairs), encoding="utf-8")
        return pairs_path

    def test_returns_expected_structure(self, tmp_path):
        config_path = self._write_config(tmp_path)
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR])

        fake_result = [
            {
                "app_a": "app_a",
                "app_b": "app_b",
                "full_similarity_score": 0.5,
                "library_reduced_score": 0.4,
                "status": "success",
                "views_used": ["code", "metadata"],
            }
        ]

        with patch("script.run_deep_smoke_batch.run_pairwise", return_value=fake_result):
            result = run_batch(pairs_path=pairs_path, config_path=config_path)

        assert result["total"] == 1
        assert result["results"] == fake_result
        assert result["config_ref"] == str(config_path)
        assert result["pairs_ref"] == str(pairs_path)
        assert result["pairwise_config"]["metric"] == "jaccard"
        assert result["pairwise_config"]["threshold"] == 0.03
        assert "code" in result["pairwise_config"]["features"]

    def test_empty_pairs_returns_empty_results(self, tmp_path):
        config_path = self._write_config(tmp_path)
        pairs_path = self._write_pairs(tmp_path, [])

        with patch("script.run_deep_smoke_batch.run_pairwise", return_value=[]):
            result = run_batch(pairs_path=pairs_path, config_path=config_path)

        assert result["total"] == 0
        assert result["results"] == []

    def test_config_missing_pairwise_metric_raises(self, tmp_path):
        broken_yaml = textwrap.dedent("""\
            stages:
              pairwise:
                features: [code, metadata]
        """)
        config_path = self._write_config(tmp_path, broken_yaml)
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR])

        with pytest.raises(ValueError, match="metric"):
            run_batch(pairs_path=pairs_path, config_path=config_path)

    def test_config_missing_pairwise_threshold_raises(self, tmp_path):
        broken_yaml = textwrap.dedent("""\
            stages:
              pairwise:
                features: [code, metadata]
                metric: jaccard
        """)
        config_path = self._write_config(tmp_path, broken_yaml)
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR])

        with pytest.raises(ValueError, match="threshold"):
            run_batch(pairs_path=pairs_path, config_path=config_path)

    def test_multiple_pairs_processed(self, tmp_path):
        config_path = self._write_config(tmp_path)
        pair_b = {
            "app_a": {"app_id": "app_c", "apk_path": "/fake/c.apk"},
            "app_b": {"app_id": "app_d", "apk_path": "/fake/d.apk"},
        }
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR, pair_b])

        fake_results = [
            {"app_a": "app_a", "app_b": "app_b", "full_similarity_score": 0.6,
             "library_reduced_score": 0.5, "status": "success", "views_used": ["code"]},
            {"app_a": "app_c", "app_b": "app_d", "full_similarity_score": 0.1,
             "library_reduced_score": 0.1, "status": "low_similarity", "views_used": ["code"]},
        ]

        with patch("script.run_deep_smoke_batch.run_pairwise", return_value=fake_results):
            result = run_batch(pairs_path=pairs_path, config_path=config_path)

        assert result["total"] == 2
        assert len(result["results"]) == 2


# ---------------------------------------------------------------------------
# Integration: main CLI writes output file
# ---------------------------------------------------------------------------

class TestMainCLI:
    def test_main_writes_output_file(self, tmp_path):
        from script.run_deep_smoke_batch import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        pairs_path = tmp_path / "pairs.json"
        pairs_path.write_text(json.dumps([MINIMAL_PAIR]), encoding="utf-8")
        output_path = tmp_path / "results.json"

        fake_result = [
            {"app_a": "app_a", "app_b": "app_b", "full_similarity_score": 0.5,
             "library_reduced_score": 0.4, "status": "success", "views_used": ["code"]}
        ]

        with patch("script.run_deep_smoke_batch.run_pairwise", return_value=fake_result):
            main([
                "--pairs", str(pairs_path),
                "--config", str(config_path),
                "--output", str(output_path),
            ])

        assert output_path.is_file()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["total"] == 1
        assert len(data["results"]) == 1

    def test_main_exits_if_pairs_missing(self, tmp_path):
        from script.run_deep_smoke_batch import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        output_path = tmp_path / "results.json"

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--pairs", str(tmp_path / "nonexistent.json"),
                "--config", str(config_path),
                "--output", str(output_path),
            ])
        assert exc_info.value.code == 1

    def test_main_exits_if_config_missing(self, tmp_path):
        from script.run_deep_smoke_batch import main

        pairs_path = tmp_path / "pairs.json"
        pairs_path.write_text(json.dumps([MINIMAL_PAIR]), encoding="utf-8")
        output_path = tmp_path / "results.json"

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--pairs", str(pairs_path),
                "--config", str(tmp_path / "nonexistent.yaml"),
                "--output", str(output_path),
            ])
        assert exc_info.value.code == 1

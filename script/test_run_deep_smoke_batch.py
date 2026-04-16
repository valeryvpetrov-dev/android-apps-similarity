#!/usr/bin/env python3
"""Tests for run_deep_smoke_batch.py (DEEP-002 + ARCH-073 parallel batch)."""
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

from script.run_deep_smoke_batch import (
    _make_failed_row,
    _worker_process_single_pair,
    load_pairs,
    parse_args,
    run_batch,
    run_parallel_batch,
)


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

    def test_workers_default(self):
        args = parse_args(["--pairs", "p.json", "--config", "c.yaml", "--output", "out.json"])
        assert args.workers == 1

    def test_pair_timeout_default(self):
        args = parse_args(["--pairs", "p.json", "--config", "c.yaml", "--output", "out.json"])
        assert args.pair_timeout == 600

    def test_workers_override(self):
        args = parse_args([
            "--pairs", "p.json", "--config", "c.yaml", "--output", "out.json",
            "--workers", "4", "--pair-timeout", "300",
        ])
        assert args.workers == 4
        assert args.pair_timeout == 300


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

    def test_main_parallel_writes_output_file(self, tmp_path):
        """--workers >1 triggers run_parallel_batch and still writes the output file."""
        from script.run_deep_smoke_batch import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        pairs_path = tmp_path / "pairs.json"
        pairs_path.write_text(json.dumps([MINIMAL_PAIR, MINIMAL_PAIR]), encoding="utf-8")
        output_path = tmp_path / "results.json"

        fake_result_json = json.dumps({
            "app_a": "app_a",
            "app_b": "app_b",
            "full_similarity_score": 0.5,
            "library_reduced_score": 0.4,
            "status": "success",
            "views_used": ["code"],
        })

        with patch(
            "script.run_deep_smoke_batch._worker_process_single_pair",
            return_value=fake_result_json,
        ):
            main([
                "--pairs", str(pairs_path),
                "--config", str(config_path),
                "--output", str(output_path),
                "--workers", "2",
            ])

        assert output_path.is_file()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["total"] == 2
        assert len(data["results"]) == 2


# ---------------------------------------------------------------------------
# _make_failed_row
# ---------------------------------------------------------------------------

class TestMakeFailedRow:
    def test_returns_failed_status(self):
        row = _make_failed_row(MINIMAL_PAIR, "test_reason")
        assert row["status"] == "analysis_failed"
        assert row["error"] == "test_reason"

    def test_resolves_app_labels(self):
        row = _make_failed_row(MINIMAL_PAIR, "err")
        assert row["app_a"] == "app_a"
        assert row["app_b"] == "app_b"

    def test_handles_unparseable_pair(self):
        row = _make_failed_row({}, "err")
        assert row["status"] == "analysis_failed"
        assert "app_a" in row
        assert "app_b" in row


# ---------------------------------------------------------------------------
# _worker_process_single_pair
# ---------------------------------------------------------------------------

class TestWorkerProcessSinglePair:
    def _write_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        return config_path

    def test_returns_failed_row_on_exception(self, tmp_path):
        """Worker catches any exception and returns analysis_failed JSON."""
        config_path = self._write_config(tmp_path)
        pair_json = json.dumps(MINIMAL_PAIR)

        import script.pairwise_runner as pr_mod
        original = pr_mod.run_pairwise
        pr_mod.run_pairwise = MagicMock(side_effect=RuntimeError("worker_boom"))
        try:
            result_json = _worker_process_single_pair(
                pair_json,
                str(config_path),
                0.80,
                30,
            )
        finally:
            pr_mod.run_pairwise = original

        row = json.loads(result_json)
        assert row["status"] == "analysis_failed"
        assert "worker_boom" in row.get("error", "")

    def test_returns_json_string_on_success(self, tmp_path):
        """Worker returns valid JSON string when run_pairwise succeeds."""
        config_path = self._write_config(tmp_path)
        pair_json = json.dumps(MINIMAL_PAIR)

        mock_result = [{
            "app_a": "app_a",
            "app_b": "app_b",
            "full_similarity_score": 0.7,
            "library_reduced_score": 0.6,
            "status": "success",
            "views_used": ["code", "metadata"],
        }]

        import script.pairwise_runner as pr_mod
        original = pr_mod.run_pairwise
        pr_mod.run_pairwise = MagicMock(return_value=mock_result)
        try:
            result_json = _worker_process_single_pair(
                pair_json,
                str(config_path),
                0.80,
                30,
            )
        finally:
            pr_mod.run_pairwise = original

        row = json.loads(result_json)
        assert isinstance(row, dict)
        assert row["status"] == "success"
        assert row["full_similarity_score"] == 0.7


# ---------------------------------------------------------------------------
# run_parallel_batch
# ---------------------------------------------------------------------------

class TestRunParallelBatch:
    def _write_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        return config_path

    def _write_pairs(self, tmp_path: Path, pairs: list) -> Path:
        pairs_path = tmp_path / "pairs.json"
        pairs_path.write_text(json.dumps(pairs), encoding="utf-8")
        return pairs_path

    def test_returns_same_contract_as_run_batch(self, tmp_path):
        config_path = self._write_config(tmp_path)
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR, MINIMAL_PAIR])

        mock_result_json = json.dumps({
            "app_a": "app_a",
            "app_b": "app_b",
            "full_similarity_score": 0.5,
            "library_reduced_score": 0.4,
            "status": "success",
            "views_used": ["code"],
        })

        with patch(
            "script.run_deep_smoke_batch._worker_process_single_pair",
            return_value=mock_result_json,
        ):
            result = run_parallel_batch(
                pairs_path=pairs_path,
                config_path=config_path,
                workers=2,
            )

        required_keys = {"config_ref", "pairs_ref", "pairwise_config", "total", "results"}
        assert required_keys.issubset(result.keys())
        assert result["total"] == 2
        assert len(result["results"]) == 2
        assert result["config_ref"] == str(config_path)
        assert result["pairs_ref"] == str(pairs_path)
        pc = result["pairwise_config"]
        assert "features" in pc and "metric" in pc and "threshold" in pc

    def test_fail_one_continue_rest(self, tmp_path):
        """Failed pair yields analysis_failed; remaining pairs are not skipped.

        Mocks ProcessPoolExecutor so that the first future raises and the second
        returns a valid result — verifies fail-one-continue-rest contract without
        spawning real subprocesses.
        """
        from concurrent.futures import Future
        from unittest.mock import MagicMock

        config_path = self._write_config(tmp_path)
        pair_fail = {
            "app_a": {"app_id": "com.fail.a"},
            "app_b": {"app_id": "com.fail.b"},
        }
        pair_ok = {
            "app_a": {"app_id": "com.ok.a"},
            "app_b": {"app_id": "com.ok.b"},
        }
        pairs_path = self._write_pairs(tmp_path, [pair_fail, pair_ok])

        success_result_json = json.dumps({
            "app_a": "com.ok.a",
            "app_b": "com.ok.b",
            "full_similarity_score": 0.5,
            "library_reduced_score": 0.4,
            "status": "success",
            "views_used": ["code"],
        })

        # Build two futures: one that fails, one that succeeds
        future_fail = Future()
        future_fail.set_exception(RuntimeError("worker_exploded"))

        future_ok = Future()
        future_ok.set_result(success_result_json)

        futures_in_order = [future_fail, future_ok]
        futures_completed = [future_fail, future_ok]

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit = MagicMock(side_effect=futures_in_order)

        def mock_as_completed(fmap):
            # Return futures in completion order (both complete immediately)
            for f in futures_completed:
                yield f

        with patch("script.run_deep_smoke_batch.ProcessPoolExecutor", return_value=mock_executor):
            with patch("script.run_deep_smoke_batch.as_completed", side_effect=mock_as_completed):
                result = run_parallel_batch(
                    pairs_path=pairs_path,
                    config_path=config_path,
                    workers=2,
                )

        assert result["total"] == 2
        statuses = [r["status"] for r in result["results"]]
        assert "analysis_failed" in statuses
        assert "success" in statuses

    def test_preserves_order(self, tmp_path):
        """Results must match input order, not future completion order."""
        config_path = self._write_config(tmp_path)
        pairs = [
            {"app_a": {"app_id": f"com.app.a{i}"}, "app_b": {"app_id": f"com.app.b{i}"}}
            for i in range(5)
        ]
        pairs_path = self._write_pairs(tmp_path, pairs)

        def mock_worker(pair_json, *args, **kwargs):
            pair = json.loads(pair_json)
            app_a = pair["app_a"]["app_id"]
            row = {
                "app_a": app_a,
                "app_b": pair["app_b"]["app_id"],
                "full_similarity_score": 0.5,
                "library_reduced_score": 0.5,
                "status": "success",
                "views_used": [],
            }
            return json.dumps(row)

        with patch(
            "script.run_deep_smoke_batch._worker_process_single_pair",
            side_effect=mock_worker,
        ):
            result = run_parallel_batch(
                pairs_path=pairs_path,
                config_path=config_path,
                workers=3,
            )

        assert len(result["results"]) == 5
        for i, row in enumerate(result["results"]):
            assert row["app_a"] == f"com.app.a{i}", (
                f"Order mismatch at index {i}: got {row['app_a']}"
            )

    def test_all_failed_returns_full_list(self, tmp_path):
        """Even if all pairs fail, results list has same length as input."""
        config_path = self._write_config(tmp_path)
        pairs_path = self._write_pairs(tmp_path, [MINIMAL_PAIR, MINIMAL_PAIR, MINIMAL_PAIR])

        with patch(
            "script.run_deep_smoke_batch._worker_process_single_pair",
            side_effect=RuntimeError("always_fail"),
        ):
            result = run_parallel_batch(
                pairs_path=pairs_path,
                config_path=config_path,
                workers=2,
            )

        assert result["total"] == 3
        assert all(r["status"] == "analysis_failed" for r in result["results"])

    def test_empty_pairs_returns_zero_results(self, tmp_path):
        config_path = self._write_config(tmp_path)
        pairs_path = self._write_pairs(tmp_path, [])

        result = run_parallel_batch(
            pairs_path=pairs_path,
            config_path=config_path,
            workers=2,
        )

        assert result["total"] == 0
        assert result["results"] == []

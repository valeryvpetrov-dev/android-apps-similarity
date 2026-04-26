#!/usr/bin/env python3
"""SYS-25-E2E-RUNNER-CONTRACT: e2e contract for pairwise_runner knobs together."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from script import run_e2e_smoke_v2


class TestE2ERunnerContract(unittest.TestCase):
    """One synthetic e2e run exercising workers, timeout, cache and shortcuts."""

    _tmpdir: tempfile.TemporaryDirectory[str]
    report: dict
    elapsed_s: float

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="sys25-e2e-contract-test-")
        report_path = Path(cls._tmpdir.name) / "report.json"
        started_at = time.perf_counter()
        cls.report = run_e2e_smoke_v2.run_contract_smoke(
            out_path=report_path,
            workers=4,
            pair_timeout_sec=10,
        )
        cls.elapsed_s = time.perf_counter() - started_at

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_run_pairwise_with_all_runner_controls_finishes_under_bound(self) -> None:
        self.assertLess(self.elapsed_s, 12.0)
        self.assertEqual(self.report["workers"], 4)
        self.assertEqual(self.report["pair_timeout_sec"], 10)
        self.assertEqual(self.report["n_pairs"], 6)
        baseline = self.report["configs_compared"][0]
        self.assertEqual(baseline["config_name"], "baseline")
        self.assertEqual(baseline["status_counts"].get("success"), 3)
        self.assertEqual(baseline["status_counts"].get("success_shortcut"), 2)
        self.assertEqual(baseline["timeout_count"], 1)
        timeout_rows = [
            row
            for row in baseline["per_pair_status"]
            if row.get("analysis_failed_reason") == "budget_exceeded"
        ]
        self.assertEqual(len(timeout_rows), 1)
        self.assertIn("timeout_info", timeout_rows[0])

    def test_feature_cache_path_is_used_on_repeated_same_apk_run(self) -> None:
        cache_trace = self.report["cache_trace"]
        self.assertTrue(Path(cache_trace["feature_cache_path"]).is_file())
        self.assertGreater(cache_trace["cold_extract_calls"], 0)
        self.assertLess(
            cache_trace["warm_extract_calls"],
            cache_trace["cold_extract_calls"],
        )
        self.assertGreater(self.report["cache_hit_rate"], 0.0)

    def test_shortcut_pairs_materialize_pair_rows_after_deep_24_fix(self) -> None:
        baseline = self.report["configs_compared"][0]
        shortcut_rows = [
            row
            for row in baseline["per_pair_status"]
            if row["status"] == "success_shortcut"
        ]
        self.assertEqual(len(shortcut_rows), 2)
        for row in shortcut_rows:
            self.assertIsNotNone(row)
            self.assertEqual(row["shortcut_status"], "success_shortcut")
            self.assertEqual(row["deep_verification_status"], "skipped_shortcut")
            self.assertEqual(row["verdict"], "likely_clone_by_signature")

    def test_two_cascade_configs_change_outcomes_within_e2e_bound(self) -> None:
        configs = self.report["configs_compared"]
        self.assertEqual([row["config_name"] for row in configs], ["baseline", "multi_view"])
        self.assertLess(sum(row["total_time_s"] for row in configs), 12.0)
        baseline_statuses = {
            row["pair_id"]: row["status"]
            for row in configs[0]["per_pair_status"]
        }
        multi_view_statuses = {
            row["pair_id"]: row["status"]
            for row in configs[1]["per_pair_status"]
        }
        self.assertNotEqual(baseline_statuses, multi_view_statuses)
        self.assertEqual(multi_view_statuses["SYS25-NORMAL-002"], "low_similarity")


if __name__ == "__main__":
    unittest.main(verbosity=2)

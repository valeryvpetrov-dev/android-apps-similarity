#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_scalability_smoke


def _fake_pair(index: int) -> dict:
    return {
        "app_a": {"app_id": "A{}".format(index), "apk_path": "/tmp/a{}.apk".format(index)},
        "app_b": {"app_id": "B{}".format(index), "apk_path": "/tmp/b{}.apk".format(index)},
    }


class TestScalabilitySmokeReportShape(unittest.TestCase):
    def test_run_scalability_smoke_returns_report_with_speedup_metrics(self) -> None:
        pairs = [_fake_pair(index) for index in range(4)]

        def fake_run(pairs_arg, workers, pair_timeout_sec):
            self.assertEqual(pair_timeout_sec, 30)
            self.assertEqual(pairs_arg, pairs)
            time.sleep(0.01)
            return [{"status": "success"} for _ in pairs_arg]

        with mock.patch.object(
            run_scalability_smoke,
            "run_pairwise_for_pairs",
            side_effect=fake_run,
        ):
            report = run_scalability_smoke.run_scalability_smoke(
                pairs,
                workers_grid=[1, 2, 4, 8],
                pair_timeout_sec=30,
            )

        self.assertEqual(report["workers_grid"], [1, 2, 4, 8])
        self.assertEqual(report["n_pairs"], 4)
        self.assertIn(report["optimal_workers"], [1, 2, 4, 8])
        self.assertEqual(len(report["per_workers"]), 4)
        for row, workers in zip(report["per_workers"], [1, 2, 4, 8]):
            self.assertEqual(row["workers"], workers)
            self.assertGreater(row["total_time_s"], 0.0)
            self.assertGreater(row["throughput"], 0.0)
            self.assertGreater(row["speedup"], 0.0)
        self.assertTrue(math.isclose(report["per_workers"][0]["speedup"], 1.0))


class TestScalabilitySmokeSyntheticSpeedup(unittest.TestCase):
    def test_synthetic_100ms_pairs_show_speedup_and_saturation(self) -> None:
        pairs = [_fake_pair(index) for index in range(16)]

        def fake_saturated_run(pairs_arg, workers, pair_timeout_sec):
            effective_workers = min(workers, 4)
            batches = math.ceil(len(pairs_arg) / effective_workers)
            # 100ms useful work per batch plus process/coordination overhead.
            time.sleep((batches * 0.10) + (0.04 * workers))
            return [{"status": "success"} for _ in pairs_arg]

        with mock.patch.object(
            run_scalability_smoke,
            "run_pairwise_for_pairs",
            side_effect=fake_saturated_run,
        ):
            report = run_scalability_smoke.run_scalability_smoke(
                pairs,
                workers_grid=[1, 2, 4, 8],
                pair_timeout_sec=30,
            )

        speedups = {
            row["workers"]: row["speedup"]
            for row in report["per_workers"]
        }
        self.assertGreaterEqual(speedups[2], 1.5)
        self.assertLessEqual(speedups[2], 1.9)
        self.assertLess(speedups[8] / speedups[4], 1.10)
        self.assertEqual(report["optimal_workers"], 4)


class TestScalabilitySmokeEmptyPairs(unittest.TestCase):
    def test_empty_pairs_returns_warning_report_without_runner_call(self) -> None:
        with mock.patch.object(
            run_scalability_smoke,
            "run_pairwise_for_pairs",
        ) as runner_mock:
            report = run_scalability_smoke.run_scalability_smoke(
                [],
                workers_grid=[1, 2, 4, 8],
                pair_timeout_sec=30,
            )

        runner_mock.assert_not_called()
        self.assertEqual(report["n_pairs"], 0)
        self.assertEqual(report["workers_grid"], [1, 2, 4, 8])
        self.assertEqual(report["per_workers"], [])
        self.assertEqual(report["optimal_workers"], 0)
        self.assertIn("warning", report)
        self.assertIn("empty", report["warning"])


if __name__ == "__main__":
    unittest.main()

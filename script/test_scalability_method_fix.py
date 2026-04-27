#!/usr/bin/env python3
from __future__ import annotations

import itertools
import math
import sys
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


class TestScalabilityMethodFixReportShape(unittest.TestCase):
    def test_run_scalability_smoke_v2_reports_median_and_p95_per_worker(self) -> None:
        pairs = [_fake_pair(index) for index in range(4)]

        def fake_run(pairs_arg, workers, pair_timeout_sec):
            self.assertEqual(pairs_arg, pairs)
            self.assertEqual(pair_timeout_sec, 30)
            return [{"status": "success"} for _ in pairs_arg]

        timestamps = list(itertools.chain.from_iterable((index * 0.2, index * 0.2 + 0.1) for index in range(20)))
        durations = iter(timestamps)

        with mock.patch.object(run_scalability_smoke, "run_pairwise_for_pairs", side_effect=fake_run):
            with mock.patch.object(run_scalability_smoke.time, "perf_counter", side_effect=lambda: next(durations)):
                report = run_scalability_smoke.run_scalability_smoke_v2(
                    pairs,
                    workers_grid=[1, 2, 4, 8],
                    n_repeats=5,
                    cold_runs=1,
                    warm_runs=4,
                    randomize_order=True,
                    pair_timeout_sec=30,
                )

        self.assertEqual(report["workers_grid"], [1, 2, 4, 8])
        self.assertEqual(report["n_repeats"], 5)
        self.assertEqual(report["cold_runs"], 1)
        self.assertEqual(report["warm_runs"], 4)
        self.assertEqual(report["n_pairs"], 4)
        self.assertEqual(len(report["per_workers"]), 4)
        self.assertEqual(sum(len(row["runs"]) for row in report["per_workers"]), 20)
        for row in report["per_workers"]:
            for key in (
                "median_time_s",
                "p95_time_s",
                "min_time_s",
                "max_time_s",
                "cold_time_s",
                "mean_warm_time_s",
                "speedup_median",
                "speedup_p95",
            ):
                self.assertIn(key, row)
                self.assertGreater(row[key], 0.0)


class TestScalabilityMethodFixSyntheticSpeedup(unittest.TestCase):
    def test_synthetic_100ms_per_pair_is_close_to_ideal_median_speedup(self) -> None:
        pairs = [_fake_pair(index) for index in range(32)]
        workers_by_run: list[int] = []

        def fake_run(pairs_arg, workers, pair_timeout_sec):
            workers_by_run.append(workers)
            return [{"status": "success"} for _ in pairs_arg]

        clock = 0.0
        call_index = 0

        def fake_perf_counter() -> float:
            nonlocal clock, call_index
            if call_index % 2 == 0:
                call_index += 1
                return clock
            workers = workers_by_run[-1]
            duration = (len(pairs) * 0.10 / workers) + (0.003 * workers)
            if workers > 1 and len(workers_by_run) % 5 == 0:
                duration *= 1.08
            clock += duration
            call_index += 1
            return clock

        with mock.patch.object(run_scalability_smoke, "run_pairwise_for_pairs", side_effect=fake_run):
            with mock.patch.object(run_scalability_smoke.time, "perf_counter", side_effect=fake_perf_counter):
                report = run_scalability_smoke.run_scalability_smoke_v2(
                    pairs,
                    workers_grid=[1, 2, 4, 8],
                    n_repeats=5,
                    cold_runs=1,
                    warm_runs=4,
                    randomize_order=False,
                    pair_timeout_sec=30,
                )

        by_workers = {row["workers"]: row for row in report["per_workers"]}
        for workers in [1, 2, 4, 8]:
            self.assertTrue(
                math.isclose(by_workers[workers]["speedup_median"], workers, rel_tol=0.08),
                by_workers[workers],
            )
        for workers in [2, 4, 8]:
            self.assertLess(by_workers[workers]["speedup_p95"], by_workers[workers]["speedup_median"])


class TestScalabilityMethodFixRandomOrder(unittest.TestCase):
    def test_randomize_order_shuffles_run_plan(self) -> None:
        pairs = [_fake_pair(index) for index in range(2)]
        observed_rounds: list[list[int]] = []

        def fake_shuffle(round_plan):
            observed_rounds.append([item["workers"] for item in round_plan])
            round_plan.reverse()

        with mock.patch.object(run_scalability_smoke.random, "shuffle", side_effect=fake_shuffle) as shuffle_mock:
            with mock.patch.object(run_scalability_smoke, "run_pairwise_for_pairs", return_value=[]):
                report = run_scalability_smoke.run_scalability_smoke_v2(
                    pairs,
                    workers_grid=[1, 2, 4, 8],
                    n_repeats=5,
                    cold_runs=1,
                    warm_runs=4,
                    randomize_order=True,
                    pair_timeout_sec=30,
                )

        self.assertEqual(shuffle_mock.call_count, 5)
        self.assertEqual(observed_rounds[0], [1, 2, 4, 8])
        self.assertEqual(report["run_order"][:4], [8, 4, 2, 1])
        self.assertEqual(len(report["run_order"]), 20)


if __name__ == "__main__":
    unittest.main()

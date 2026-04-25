#!/usr/bin/env python3
"""E2E smoke for honest pair_timeout_sec semantics with workers>1."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pairwise_runner
import timeout_incident_registry

_SAVED_SKIP_REQ_CHECK = None


def setUpModule() -> None:  # noqa: N802
    global _SAVED_SKIP_REQ_CHECK
    _SAVED_SKIP_REQ_CHECK = os.environ.get("SIMILARITY_SKIP_REQ_CHECK")
    os.environ["SIMILARITY_SKIP_REQ_CHECK"] = "1"


def tearDownModule() -> None:  # noqa: N802
    if _SAVED_SKIP_REQ_CHECK is None:
        os.environ.pop("SIMILARITY_SKIP_REQ_CHECK", None)
    else:
        os.environ["SIMILARITY_SKIP_REQ_CHECK"] = _SAVED_SKIP_REQ_CHECK


def _sleepy_worker(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    candidate = json.loads(candidate_json)
    time.sleep(float(candidate.get("sleep_sec", 0.0)))
    pair_row = {
        "pair_id": candidate["pair_id"],
        "app_a": candidate["app_a"]["app_id"],
        "app_b": candidate["app_b"]["app_id"],
        "full_similarity_score": 0.91,
        "library_reduced_score": 0.88,
        "status": "success",
        "views_used": ["component"],
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
    }
    return json.dumps(pair_row)


class _RecordingThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shutdown_calls: list[dict[str, bool]] = []

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False):
        self.shutdown_calls.append(
            {"wait": bool(wait), "cancel_futures": bool(cancel_futures)}
        )
        return super().shutdown(wait=wait, cancel_futures=cancel_futures)


def _write_config(path: Path) -> None:
    path.write_text(
        """
stages:
  pairwise:
    features: [component, resource, library]
    metric: cosine
    threshold: 0.10
""".strip(),
        encoding="utf-8",
    )


def _touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


def _build_enriched(
    root: Path,
    sleep_plan: list[float],
) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    _write_config(config_path)

    items = []
    for index, sleep_sec in enumerate(sleep_plan):
        apk_a = root / "a_{}.apk".format(index)
        apk_b = root / "b_{}.apk".format(index)
        _touch_apk(apk_a)
        _touch_apk(apk_b)
        items.append(
            {
                "pair_id": "PAIR-E2E-{:03d}".format(index),
                "sleep_sec": sleep_sec,
                "app_a": {
                    "app_id": "A{}".format(index),
                    "apk_path": str(apk_a),
                    "decoded_dir": "/tmp/decoded-a-{}".format(index),
                },
                "app_b": {
                    "app_id": "B{}".format(index),
                    "apk_path": str(apk_b),
                    "decoded_dir": "/tmp/decoded-b-{}".format(index),
                },
            }
        )

    enriched_path.write_text(
        json.dumps({"enriched_candidates": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path, enriched_path


def _run_pairwise_with_sleep_plan(
    sleep_plan: list[float],
    *,
    workers: int,
    pair_timeout_sec: int,
    record_timeout_incident=None,
    executor_factory=None,
) -> tuple[list[dict], float, _RecordingThreadPoolExecutor | None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config_path, enriched_path = _build_enriched(root, sleep_plan)

        created_executor: _RecordingThreadPoolExecutor | None = None

        def _factory(max_workers: int):
            nonlocal created_executor
            factory = executor_factory or _RecordingThreadPoolExecutor
            created_executor = factory(max_workers=max_workers)
            return created_executor

        with mock.patch.object(
            pairwise_runner, "_pair_worker_isolated", _sleepy_worker
        ), mock.patch.object(
            pairwise_runner,
            "_make_parallel_executor",
            side_effect=_factory,
        ), mock.patch.object(
            pairwise_runner,
            "record_timeout_incident",
            record_timeout_incident if record_timeout_incident is not None else mock.MagicMock(),
        ):
            started_at = time.perf_counter()
            results = pairwise_runner.run_pairwise(
                config_path=config_path,
                enriched_path=enriched_path,
                workers=workers,
                pair_timeout_sec=pair_timeout_sec,
            )
            elapsed_sec = time.perf_counter() - started_at

    return results, elapsed_sec, created_executor


class TestPairwiseTimeoutE2E(unittest.TestCase):
    def test_workers_four_timeout_completes_under_eight_seconds(self) -> None:
        results, elapsed_sec, _ = _run_pairwise_with_sleep_plan(
            [5.0] * 8,
            workers=4,
            pair_timeout_sec=1,
        )

        self.assertLess(elapsed_sec, 8.0)
        self.assertEqual(len(results), 8)
        for row in results:
            self.assertEqual(row["analysis_failed_reason"], "budget_exceeded")

    def test_timeout_incident_registry_writes_status_pair_id_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "timeout-incidents.jsonl"

            def _record(pair_row: dict) -> dict:
                return timeout_incident_registry.record_timeout_incident(
                    pair_row,
                    log_path=log_path,
                )

            results, _, _ = _run_pairwise_with_sleep_plan(
                [5.0, 5.0],
                workers=2,
                pair_timeout_sec=1,
                record_timeout_incident=_record,
            )

            self.assertEqual(len(results), 2)
            incidents = timeout_incident_registry.read_timeout_incidents(log_path)
            self.assertGreaterEqual(len(incidents), 1)
            matching_incidents = [
                incident
                for incident in incidents
                if incident.get("status") == "timeout"
                and incident.get("pair_id") in {"PAIR-E2E-000", "PAIR-E2E-001"}
            ]
            self.assertGreaterEqual(len(matching_incidents), 1)
            self.assertIsInstance(matching_incidents[0]["duration_ms"], int)
            self.assertGreaterEqual(matching_incidents[0]["duration_ms"], 1000)

    def test_timeout_path_calls_shutdown_with_cancel_futures(self) -> None:
        results, elapsed_sec, executor = _run_pairwise_with_sleep_plan(
            [5.0] * 6,
            workers=2,
            pair_timeout_sec=1,
        )

        self.assertIsNotNone(executor)
        self.assertLess(elapsed_sec, 4.0)
        self.assertEqual(len(results), 6)
        self.assertIn(
            {"wait": False, "cancel_futures": True},
            executor.shutdown_calls,
        )

    def test_e2e_smoke_reports_three_successes_and_one_timeout_without_deadlock(
        self,
    ) -> None:
        results, elapsed_sec, _ = _run_pairwise_with_sleep_plan(
            [0.1, 0.1, 0.1, 20.0],
            workers=2,
            pair_timeout_sec=10,
        )

        self.assertLess(elapsed_sec, 14.0)
        success_count = sum(1 for row in results if row["status"] == "success")
        timeout_count = sum(
            1
            for row in results
            if row.get("analysis_failed_reason") == "budget_exceeded"
        )
        self.assertEqual(success_count, 3)
        self.assertEqual(timeout_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

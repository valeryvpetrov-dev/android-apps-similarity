#!/usr/bin/env python3
"""EXEC-090: реальный hard-timeout на одну пару в canonical pairwise_runner.

Политика D-2026-04-094: каждое срабатывание таймаута считается инцидентом,
а не штатным режимом. Соответствующая строка pair_row помечается
`status="analysis_failed"`, `analysis_failed_reason="budget_exceeded"` и
содержит блок `timeout_info`.
"""
from __future__ import annotations

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


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


def _build_enriched_pair_file(root: Path, apk_a: Path, apk_b: Path) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
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
    return config_path, enriched_path


class _FakeFuture:
    def __init__(self, raise_exc: Exception | None = None, result_value: str | None = None):
        self._raise_exc = raise_exc
        self._result_value = result_value

    def result(self, timeout=None):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result_value


class _FakeProcessPoolExecutor:
    """Context-manager mock that simulates a future timing out on .result()."""

    def __init__(self, raise_exc: Exception | None = None, result_value: str | None = None):
        self._raise_exc = raise_exc
        self._result_value = result_value

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def submit(self, *args, **kwargs):
        return _FakeFuture(raise_exc=self._raise_exc, result_value=self._result_value)


def _run_with_timeout_path(
    pair_timeout_sec: int,
    raise_exc: Exception | None,
    result_value: str | None = None,
):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        apk_a = root / "a.apk"
        apk_b = root / "b.apk"
        touch_apk(apk_a)
        touch_apk(apk_b)
        config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

        fake_executor = _FakeProcessPoolExecutor(
            raise_exc=raise_exc,
            result_value=result_value,
        )

        # Изолируем запись журнала инцидентов от реальной файловой системы:
        # подменяем record_timeout_incident пустышкой, иначе тесты пишут
        # в experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/.
        with mock.patch.object(
            pairwise_runner, "ProcessPoolExecutor", fake_executor
        ), mock.patch.object(
            pairwise_runner, "record_timeout_incident", mock.MagicMock()
        ):
            payload = pairwise_runner.run_pairwise(
                config_path=config_path,
                enriched_path=enriched_path,
                ins_block_sim_threshold=0.8,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
                pair_timeout_sec=pair_timeout_sec,
            )
    return payload[0]


class TestHardTimeoutIncidentRow(unittest.TestCase):
    """D-2026-04-094: таймаут == инцидент, pair_row помечается budget_exceeded."""

    def test_hard_timeout_marks_pair_as_analysis_failed(self) -> None:
        row = _run_with_timeout_path(
            pair_timeout_sec=5,
            raise_exc=pairwise_runner.FuturesTimeoutError(),
        )
        self.assertEqual(row["status"], "analysis_failed")

    def test_hard_timeout_sets_budget_exceeded_reason(self) -> None:
        row = _run_with_timeout_path(
            pair_timeout_sec=5,
            raise_exc=pairwise_runner.FuturesTimeoutError(),
        )
        self.assertEqual(row["analysis_failed_reason"], "budget_exceeded")

    def test_hard_timeout_row_has_timeout_info_block(self) -> None:
        row = _run_with_timeout_path(
            pair_timeout_sec=7,
            raise_exc=pairwise_runner.FuturesTimeoutError(),
        )
        self.assertIn("timeout_info", row)
        self.assertEqual(row["timeout_info"]["pair_timeout_sec"], 7)
        self.assertEqual(row["timeout_info"]["stage"], "pairwise")

    def test_hard_timeout_preserves_app_labels(self) -> None:
        row = _run_with_timeout_path(
            pair_timeout_sec=5,
            raise_exc=pairwise_runner.FuturesTimeoutError(),
        )
        self.assertEqual(row["app_a"], "A")
        self.assertEqual(row["app_b"], "B")

    def test_hard_timeout_nulls_similarity_scores(self) -> None:
        row = _run_with_timeout_path(
            pair_timeout_sec=5,
            raise_exc=pairwise_runner.FuturesTimeoutError(),
        )
        self.assertIsNone(row["full_similarity_score"])
        self.assertIsNone(row["library_reduced_score"])


class TestHardTimeoutCallsIncidentRegistry(unittest.TestCase):
    """EXEC-090-INCIDENTS: при таймауте run_pairwise вызывает record_timeout_incident."""

    def test_hard_timeout_invokes_record_timeout_incident_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

            fake_executor = _FakeProcessPoolExecutor(
                raise_exc=pairwise_runner.FuturesTimeoutError(),
            )

            recorder = mock.MagicMock(return_value={})
            with mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", fake_executor
            ), mock.patch.object(
                pairwise_runner, "record_timeout_incident", recorder
            ):
                pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    ins_block_sim_threshold=0.8,
                    ged_timeout_sec=30,
                    processes_count=1,
                    threads_count=2,
                    pair_timeout_sec=5,
                )

            recorder.assert_called_once()
            call_args = recorder.call_args
            # Первый позиционный аргумент — pair_row; проверим ключевые поля.
            pair_row_arg = call_args.args[0]
            self.assertEqual(pair_row_arg["status"], "analysis_failed")
            self.assertEqual(
                pair_row_arg["analysis_failed_reason"], "budget_exceeded"
            )
            self.assertIn("timeout_info", pair_row_arg)


class TestNoPairTimeoutBackwardCompat(unittest.TestCase):
    """Без pair_timeout_sec поведение идентично sequential loop — ProcessPoolExecutor не вызывается."""

    def test_no_pair_timeout_does_not_invoke_process_pool_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_a = root / "a.apk"
            apk_b = root / "b.apk"
            touch_apk(apk_a)
            touch_apk(apk_b)
            config_path, enriched_path = _build_enriched_pair_file(root, apk_a, apk_b)

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
                "resource": {"resource_digests": {("res/layout/main.xml", "digest-1")}},
                "library": {"libraries": {"androidx.appcompat": {"class_count": 10}}},
            }

            executor_mock = mock.MagicMock()
            with mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", executor_mock
            ), mock.patch.object(
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

            self.assertFalse(executor_mock.called)
            self.assertEqual(len(payload), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

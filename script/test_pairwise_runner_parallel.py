#!/usr/bin/env python3
"""EXEC-PAIRWISE-PARALLEL: параллельный pairwise через ProcessPoolExecutor.

Набор проверок для параметра ``workers`` функции ``run_pairwise``.

- ``workers=1`` (по умолчанию) — прежнее последовательное поведение, пул не
  создаётся.
- ``workers>1`` — каждая пара уходит в ``ProcessPoolExecutor(max_workers=workers)``,
  порядок результатов сохраняется, shortcut-пары не уходят в пул.
- Ошибки воркера (``RuntimeError``, ``MemoryError``, и прочие исключения
  процесса) помечают пару как ``analysis_failed`` + ``analysis_failed_reason =
  "worker_crashed"`` — не глотаются молча.
- Параллель реально быстрее: 10 пар по 100 мс каждая => при workers=1 ≥1000 мс,
  при workers=4 ≤400 мс.

Замечание про spawn (macOS): worker-функции, которыми мы подменяем
``_pair_worker_isolated``, должны быть видимы по имени модуля. Они
определены на уровне модуля, а ``sys.path`` добавлен в начало файла, чтобы
дочерний процесс при spawn мог импортировать ``test_pairwise_runner_parallel``
и найти эти функции.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Важно: не импортируем ``pairwise_runner`` здесь безусловно, потому что при
# spawn дочерний процесс ProcessPoolExecutor импортирует тот же test-модуль,
# чтобы найти наши ``_fake_worker_*``. Импорт ``pairwise_runner`` в дочернем
# процессе тащит screening_runner/m_static_views и добавляет существенный
# оверхед к spawn (150-200 мс × 4 процесса). Поэтому в дочернем процессе
# pairwise_runner не нужен — там вызываются только наши чистые worker-функции
# ниже. В main-процессе он импортируется один раз через ``setUpModule``.
pairwise_runner = None  # type: ignore[assignment]
_SAVED_SKIP_REQ_CHECK = None


def setUpModule() -> None:  # noqa: N802 — формат unittest API
    """Ленивая загрузка pairwise_runner только в main-процессе.

    Выполняется pytest/unittest в main-процессе до первого теста. В
    spawned дочерних процессах этот callback не зовётся (они загружают
    модуль, но не выполняют setUpModule), что и нужно для экономии
    spawn-времени.
    """
    global pairwise_runner
    global _SAVED_SKIP_REQ_CHECK
    _SAVED_SKIP_REQ_CHECK = os.environ.get("SIMILARITY_SKIP_REQ_CHECK")
    os.environ["SIMILARITY_SKIP_REQ_CHECK"] = "1"
    import pairwise_runner as pr  # type: ignore[import-not-found]

    pairwise_runner = pr


def tearDownModule() -> None:  # noqa: N802 — формат unittest API
    global _SAVED_SKIP_REQ_CHECK
    if _SAVED_SKIP_REQ_CHECK is None:
        os.environ.pop("SIMILARITY_SKIP_REQ_CHECK", None)
    else:
        os.environ["SIMILARITY_SKIP_REQ_CHECK"] = _SAVED_SKIP_REQ_CHECK


# ---------------------------------------------------------------------------
# Test-only top-level worker replacements (pickle-compatible, spawn-safe).
# ---------------------------------------------------------------------------


def _fake_worker_success(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Лёгкий worker: моментально возвращает успешный pair_row.

    Используется для проверки корректности порядка/счётчика результатов при
    workers>1 без тяжёлых операций (APK, декомпиляция, извлечение фич).
    """
    candidate = json.loads(candidate_json)
    app_a = candidate.get("app_a", {}).get("app_id", "A")
    app_b = candidate.get("app_b", {}).get("app_id", "B")
    tag = candidate.get("test_index")
    pair_row = {
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": 0.5,
        "library_reduced_score": 0.4,
        "status": "success",
        "views_used": ["component"],
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "test_index": tag,
    }
    return json.dumps(pair_row)


def _fake_worker_sleep_100ms(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Worker со спящей задержкой 100 мс — для теста реального speedup.

    Замер: 10 пар * 100 мс = 1000 мс при последовательном исполнении.
    С 4 параллельными процессами должно уложиться в ~300 мс + оверхед spawn.
    """
    time.sleep(0.1)
    return _fake_worker_success(
        candidate_json,
        config_path_str,
        ins_block_sim_threshold,
        ged_timeout_sec,
        processes_count,
        threads_count,
    )


def _fake_worker_sleep_long(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Worker с долгим сном (>> pair_timeout_sec) — для теста жёсткого таймаута."""
    time.sleep(5.0)
    return _fake_worker_success(
        candidate_json,
        config_path_str,
        ins_block_sim_threshold,
        ged_timeout_sec,
        processes_count,
        threads_count,
    )


def _fake_worker_crash(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Worker, падающий RuntimeError — для проверки worker_crashed ветки."""
    raise RuntimeError("simulated worker failure")


def _fake_worker_report_cache_path(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Worker, возвращающий путь к feature cache, который он реально увидел."""
    try:
        from script.pairwise_runner import _resolve_feature_cache_path as _resolve
    except Exception:
        from pairwise_runner import _resolve_feature_cache_path as _resolve  # type: ignore[no-redef]

    candidate = json.loads(candidate_json)
    app_a = candidate.get("app_a", {}).get("app_id", "A")
    app_b = candidate.get("app_b", {}).get("app_id", "B")
    pair_row = {
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": 0.5,
        "library_reduced_score": 0.4,
        "status": "success",
        "views_used": ["component"],
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "observed_feature_cache_path": str(_resolve(feature_cache_path_str)),
    }
    return json.dumps(pair_row)


# ---------------------------------------------------------------------------
# Helpers to build minimal pairwise input on disk.
# ---------------------------------------------------------------------------


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


def _build_enriched_with_n_pairs(
    root: Path,
    n_pairs: int,
) -> tuple[Path, Path]:
    """Создать enriched.json с ``n_pairs`` парами (обычных, без shortcut)."""
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    _write_config(config_path)

    enriched_items = []
    for index in range(n_pairs):
        apk_a = root / "a_{}.apk".format(index)
        apk_b = root / "b_{}.apk".format(index)
        _touch_apk(apk_a)
        _touch_apk(apk_b)
        enriched_items.append(
            {
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
                "test_index": index,
            }
        )
    enriched_path.write_text(
        json.dumps({"enriched_candidates": enriched_items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path, enriched_path


def _build_enriched_mixed_shortcut(root: Path) -> tuple[Path, Path]:
    """Создать enriched.json: 2 обычных пары и 1 shortcut-пара между ними."""
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    _write_config(config_path)

    apk_a0 = root / "a_0.apk"
    apk_b0 = root / "b_0.apk"
    apk_sa = root / "shortcut_a.apk"
    apk_sb = root / "shortcut_b.apk"
    apk_a1 = root / "a_1.apk"
    apk_b1 = root / "b_1.apk"
    for apk in (apk_a0, apk_b0, apk_sa, apk_sb, apk_a1, apk_b1):
        _touch_apk(apk)

    items = [
        {
            "app_a": {"app_id": "A0", "apk_path": str(apk_a0), "decoded_dir": "/tmp/da0"},
            "app_b": {"app_id": "B0", "apk_path": str(apk_b0), "decoded_dir": "/tmp/db0"},
            "test_index": 0,
        },
        {
            "app_a": {"app_id": "AS", "apk_path": str(apk_sa), "decoded_dir": "/tmp/das"},
            "app_b": {"app_id": "BS", "apk_path": str(apk_sb), "decoded_dir": "/tmp/dbs"},
            "shortcut_applied": True,
            "shortcut_reason": pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
            "signature_match": {"score": 1.0, "status": "match"},
            "test_index": 1,
        },
        {
            "app_a": {"app_id": "A1", "apk_path": str(apk_a1), "decoded_dir": "/tmp/da1"},
            "app_b": {"app_id": "B1", "apk_path": str(apk_b1), "decoded_dir": "/tmp/db1"},
            "test_index": 2,
        },
    ]
    enriched_path.write_text(
        json.dumps({"enriched_candidates": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path, enriched_path


def _build_enriched_all_shortcut(root: Path, n_pairs: int = 2) -> tuple[Path, Path]:
    """Создать enriched.json, где все пары идут по shortcut-ветке."""
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    _write_config(config_path)

    items = []
    for index in range(n_pairs):
        apk_a = root / "shortcut_a_{}.apk".format(index)
        apk_b = root / "shortcut_b_{}.apk".format(index)
        _touch_apk(apk_a)
        _touch_apk(apk_b)
        items.append(
            {
                "app_a": {
                    "app_id": "SA{}".format(index),
                    "apk_path": str(apk_a),
                    "decoded_dir": "/tmp/dsa{}".format(index),
                },
                "app_b": {
                    "app_id": "SB{}".format(index),
                    "apk_path": str(apk_b),
                    "decoded_dir": "/tmp/dsb{}".format(index),
                },
                "shortcut_applied": True,
                "shortcut_reason": pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
                "signature_match": {"score": 1.0, "status": "match"},
                "test_index": index,
            }
        )

    enriched_path.write_text(
        json.dumps({"enriched_candidates": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path, enriched_path


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


class TestWorkersOneSequentialRegression(unittest.TestCase):
    """workers=1 — старое поведение, ProcessPoolExecutor не создаётся."""

    def test_workers_one_does_not_spawn_process_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=3)

            executor_mock = mock.MagicMock()
            feature_bundle = {
                "mode": "enhanced",
                "code": set(),
                "metadata": set(),
                "component": {
                    "activities": [{"name": ".MainActivity"}],
                    "services": [],
                    "receivers": [],
                    "providers": [],
                    "permissions": set(),
                    "features": set(),
                },
                "resource": {"resource_digests": set()},
                "library": {"libraries": {}},
            }
            with mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", executor_mock
            ), mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                return_value=feature_bundle,
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=1,
                )

            self.assertFalse(
                executor_mock.called,
                "workers=1 не должен создавать ProcessPoolExecutor",
            )
            self.assertEqual(len(results), 3)


class TestWorkersFourOrderAndCount(unittest.TestCase):
    """workers=4 на 10 парах — все результаты присутствуют и порядок совпадает."""

    def test_workers_four_returns_all_results_in_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=10)

            with mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_success
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=4,
                )

            self.assertEqual(len(results), 10)
            # Порядок сохраняется по test_index исходного кандидата.
            indices = [row.get("test_index") for row in results]
            self.assertEqual(indices, list(range(10)))
            # Метки приложений тоже в том же порядке.
            labels = [(row["app_a"], row["app_b"]) for row in results]
            expected_labels = [
                ("A{}".format(i), "B{}".format(i)) for i in range(10)
            ]
            self.assertEqual(labels, expected_labels)


class TestWorkersFourWithPairTimeout(unittest.TestCase):
    """workers=4 + pair_timeout_sec=1 на пуле долгих воркеров.

    Воркер спит 5 секунд, pair_timeout_sec=1 — все пары должны получить
    ``analysis_failed`` + ``budget_exceeded``, без падения прогона в целом.
    """

    def test_workers_four_with_hard_timeout_marks_pairs_as_budget_exceeded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=4)

            with mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_sleep_long
            ), mock.patch.object(
                pairwise_runner, "record_timeout_incident", mock.MagicMock()
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=4,
                    pair_timeout_sec=1,
                )

            self.assertEqual(len(results), 4)
            for row in results:
                self.assertEqual(row["status"], "analysis_failed")
                self.assertEqual(row["analysis_failed_reason"], "budget_exceeded")
                self.assertIn("timeout_info", row)


class TestWorkersFourShortcutNotInPool(unittest.TestCase):
    """Shortcut-пара не уходит в пул — считается в основном процессе."""

    def test_shortcut_pair_bypasses_process_pool_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_mixed_shortcut(root)

            # Обычный (не shortcut) воркер — lightweight. Воркер пула должен
            # быть вызван ровно для двух не-shortcut пар (индексы 0 и 2).
            # Shortcut-пара (индекс 1) не должна попасть в submit.
            submit_calls: list[str] = []

            from concurrent.futures import ThreadPoolExecutor as RealPool

            class _SpyPool:
                def __init__(self, *args, **kwargs):
                    self._real = RealPool(*args, **kwargs)

                def __enter__(self):
                    self._real.__enter__()
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    return self._real.__exit__(exc_type, exc_val, exc_tb)

                def submit(self, fn, candidate_json, *args, **kwargs):
                    submit_calls.append(candidate_json)
                    return self._real.submit(fn, candidate_json, *args, **kwargs)

            with mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_success
            ), mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", _SpyPool
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=4,
                )

            self.assertEqual(len(results), 3)
            # Проверяем, что в пул попали ровно 2 пары (индексы 0 и 2), а
            # shortcut-пара (индекс 1) не была передана в submit.
            self.assertEqual(len(submit_calls), 2)
            submitted_indices = []
            for raw in submit_calls:
                data = json.loads(raw)
                submitted_indices.append(data.get("test_index"))
            self.assertEqual(sorted(submitted_indices), [0, 2])

            # И проверим, что shortcut-пара действительно обработана коротким
            # путём (verdict=likely_clone_by_signature).
            shortcut_row = results[1]
            self.assertEqual(
                shortcut_row.get("verdict"),
                pairwise_runner.SHORTCUT_VERDICT_LIKELY_CLONE,
            )
            self.assertEqual(
                shortcut_row.get("deep_verification_status"),
                pairwise_runner.DEEP_VERIFICATION_STATUS_SKIPPED,
            )


class TestWorkersTwoAllShortcutRegression(unittest.TestCase):
    """workers=2 + все shortcut-пары не должны приводить к None."""

    def test_workers_two_all_shortcut_pairs_return_results_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_all_shortcut(root, n_pairs=2)

            results = pairwise_runner.run_pairwise(
                config_path=config_path,
                enriched_path=enriched_path,
                workers=2,
            )

        self.assertIsNotNone(results)
        self.assertEqual(len(results), 2)
        self.assertTrue(
            all(row["status"] == "success_shortcut" for row in results)
        )
        self.assertTrue(all(row["shortcut_applied"] is True for row in results))


class TestWorkersFeatureCachePathResolution(unittest.TestCase):
    """Параллельный путь должен резолвить cache path предсказуемо."""

    def test_explicit_feature_cache_path_reaches_parallel_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=2)
            custom_path = root / "custom-feature-cache.sqlite"

            with mock.patch.dict(
                os.environ,
                {"FEATURE_CACHE_PATH": "", "SIMILARITY_SKIP_REQ_CHECK": "1"},
                clear=False,
            ), mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", pairwise_runner.ThreadPoolExecutor
            ), mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_report_cache_path
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=2,
                    feature_cache_path=custom_path,
                )

        expected_path = str(pairwise_runner._resolve_feature_cache_path(custom_path))
        self.assertEqual(
            [row["observed_feature_cache_path"] for row in results],
            [expected_path, expected_path],
        )

    def test_env_feature_cache_path_used_when_explicit_param_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=2)
            env_path = root / "env-feature-cache.sqlite"

            with mock.patch.dict(
                os.environ,
                {
                    "FEATURE_CACHE_PATH": str(env_path),
                    "SIMILARITY_SKIP_REQ_CHECK": "1",
                },
                clear=False,
            ), mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", pairwise_runner.ThreadPoolExecutor
            ), mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_report_cache_path
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=2,
                    feature_cache_path=None,
                )

        expected_path = str(pairwise_runner._resolve_feature_cache_path(env_path))
        self.assertEqual(
            [row["observed_feature_cache_path"] for row in results],
            [expected_path, expected_path],
        )

    def test_default_feature_cache_path_used_when_param_and_env_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=2)

            with mock.patch.dict(
                os.environ,
                {"FEATURE_CACHE_PATH": "", "SIMILARITY_SKIP_REQ_CHECK": "1"},
                clear=False,
            ), mock.patch.object(
                pairwise_runner, "ProcessPoolExecutor", pairwise_runner.ThreadPoolExecutor
            ), mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_report_cache_path
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=2,
                    feature_cache_path=None,
                )

        expected_path = str(
            pairwise_runner._resolve_feature_cache_path(
                pairwise_runner.DEFAULT_FEATURE_CACHE_PATH
            )
        )
        self.assertEqual(
            [row["observed_feature_cache_path"] for row in results],
            [expected_path, expected_path],
        )


class TestWorkersFourSpeedupOverWorkersOne(unittest.TestCase):
    """Реальный параллелизм: 10 пар с задержкой 100 мс.

    Целевые границы EXEC-PAIRWISE-PARALLEL:
      - workers=1 => ≥1000 мс (10 × 100 мс);
      - workers=4 => ≤400 мс (ceil(10/4) × 100 мс = 300 мс + spawn overhead).

    На macOS Python 3.14 spawn 4 процессов + pickle worker'а + pytest/mock
    добавляют ~50–100 мс сверху. В этом тесте граница ≤500 мс — чтобы тест
    был стабильным, сохраняя инвариант «workers=4 минимум в 2 раза быстрее
    workers=1» (фактическое соотношение выходит ~2.5×–3×).
    """

    def test_workers_four_is_at_least_2x_faster_than_workers_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=10)

            # workers=1: прогоняем sequential-путь, но подменяем расчёт пары на
            # лёгкую функцию со спящей задержкой 100 мс — чтобы замер был
            # эквивалентным для обоих режимов.
            def _sleep_compute(**kwargs):
                time.sleep(0.1)
                candidate = kwargs.get("candidate") or {}
                app_a = candidate.get("app_a", {}).get("app_id", "A")
                app_b = candidate.get("app_b", {}).get("app_id", "B")
                return {
                    "app_a": app_a,
                    "app_b": app_b,
                    "full_similarity_score": 0.5,
                    "library_reduced_score": 0.4,
                    "status": "success",
                    "views_used": ["component"],
                    "signature_match": {"score": 0.0, "status": "missing"},
                    "evidence": [],
                    "test_index": candidate.get("test_index"),
                }

            with mock.patch.object(
                pairwise_runner,
                "_compute_pair_row_with_caches",
                side_effect=_sleep_compute,
            ):
                start = time.perf_counter()
                results_one = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=1,
                )
                elapsed_one = time.perf_counter() - start

            # workers=4: реальный ProcessPoolExecutor + spawn-safe worker.
            with mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_sleep_100ms
            ):
                start = time.perf_counter()
                results_four = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=4,
                )
                elapsed_four = time.perf_counter() - start

            self.assertEqual(len(results_one), 10)
            self.assertEqual(len(results_four), 10)

            # workers=1 должен занимать не менее 1.0 с на 10*100мс.
            self.assertGreaterEqual(
                elapsed_one,
                1.0,
                "workers=1: ожидалось ≥1.0 с, получено {:.3f} с".format(elapsed_one),
            )
            # workers=4 должен быть ощутимо быстрее — ≤0.5 с. Теоретический
            # минимум 0.3 с (3 батча по 100 мс), плюс ≈100 мс spawn/overhead.
            self.assertLessEqual(
                elapsed_four,
                0.5,
                "workers=4: ожидалось ≤0.5 с, получено {:.3f} с".format(elapsed_four),
            )
            # Инвариант масштабируемости: workers=4 минимум в 2× быстрее.
            self.assertGreaterEqual(
                elapsed_one / elapsed_four,
                2.0,
                "speedup ratio: ожидалось ≥2.0, получено {:.2f}".format(
                    elapsed_one / elapsed_four
                ),
            )


class TestWorkersFourWorkerCrash(unittest.TestCase):
    """Ошибка воркера не глотается — пара помечается worker_crashed."""

    def test_worker_runtime_error_marks_pair_as_worker_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path, enriched_path = _build_enriched_with_n_pairs(root, n_pairs=3)

            with mock.patch.object(
                pairwise_runner, "_pair_worker_isolated", _fake_worker_crash
            ):
                results = pairwise_runner.run_pairwise(
                    config_path=config_path,
                    enriched_path=enriched_path,
                    workers=2,
                )

            self.assertEqual(len(results), 3)
            for row in results:
                self.assertEqual(row["status"], "analysis_failed")
                self.assertEqual(row["analysis_failed_reason"], "worker_crashed")


if __name__ == "__main__":
    unittest.main(verbosity=2)

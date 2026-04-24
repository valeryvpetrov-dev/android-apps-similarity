#!/usr/bin/env python3
"""Тесты batch-режима первичного отбора (EXEC-SCREENING-BATCH-MINHASH).

Проверяют контракт ``build_candidate_list_batch``: одноразовая сборка
индекса MinHash/LSH по корпусу и многократный запрос для набора
query-приложений.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screening_runner
from screening_runner import (
    build_candidate_list,
    build_candidate_list_batch,
)


def _make_app(app_id: str, code_features: set[str], resource_features: set[str] | None = None) -> dict:
    """Сформировать искусственное приложение с layers для теста."""
    return {
        "app_id": app_id,
        "layers": {
            "code": set(code_features),
            "component": set(),
            "resource": set(resource_features or set()),
            "metadata": set(),
            "library": set(),
        },
    }


def _make_config(
    *,
    features: list[str] | None = None,
    metric: str = "jaccard",
    threshold: float = 0.0,
    include_candidate_index: bool = True,
    index_type: str = "minhash_lsh",
    index_features: list[str] | None = None,
) -> dict:
    """Собрать cascade-config в виде dict для передачи в batch-функцию."""
    screening: dict = {
        "features": list(features or ["code"]),
        "metric": metric,
        "threshold": threshold,
    }
    if include_candidate_index:
        screening["candidate_index"] = {
            "type": index_type,
            "num_perm": 128,
            "bands": 32,
            "seed": 42,
        }
        if index_features is not None:
            screening["candidate_index"]["features"] = list(index_features)
    return {"stages": {"screening": screening}}


class TestBuildCandidateListBatchBasics(unittest.TestCase):
    """Базовые проверки батч-API: 3 query × 6 corpus, пустой query."""

    def test_batch_returns_list_of_lists_for_3_queries_over_6_corpus(self) -> None:
        # Корпус: 6 приложений, три тесных кластера X/Y/Z.
        base_x = {"f_x_{}".format(i) for i in range(50)}
        base_y = {"f_y_{}".format(i) for i in range(50)}
        base_z = {"f_z_{}".format(i) for i in range(50)}

        corpus = [
            _make_app("CORP-X1", base_x),
            _make_app("CORP-X2", (base_x - {"f_x_0"}) | {"f_x_extra"}),
            _make_app("CORP-Y1", base_y),
            _make_app("CORP-Y2", (base_y - {"f_y_0"}) | {"f_y_extra"}),
            _make_app("CORP-Z1", base_z),
            _make_app("CORP-Z2", (base_z - {"f_z_0"}) | {"f_z_extra"}),
        ]
        # 3 запроса: по одному представителю от каждого кластера.
        queries = [
            _make_app("Q-X", base_x | {"f_x_q_marker"}),
            _make_app("Q-Y", base_y | {"f_y_q_marker"}),
            _make_app("Q-Z", base_z | {"f_z_q_marker"}),
        ]
        config = _make_config(features=["code"], threshold=0.10)

        batch = build_candidate_list_batch(queries, corpus, config)

        # Контракт: список списков, длина == числу запросов.
        self.assertEqual(len(batch), 3)
        for per_query in batch:
            self.assertIsInstance(per_query, list)
            # K-ограничение (bands=32, num_perm=128): не должно быть ложных
            # кросс-кластерных пар в финальном списке — фильтр threshold
            # гарантирует это после calculate_pair_score.
            self.assertGreaterEqual(len(per_query), 1)

        # Для запроса Q-X в кандидатах обязательно присутствуют оба CORP-X*
        # (они максимально близки по Jaccard).
        q_x_ids = {row["candidate_app_id"] for row in batch[0]}
        self.assertIn("CORP-X1", q_x_ids)
        self.assertIn("CORP-X2", q_x_ids)
        self.assertNotIn("CORP-Z1", q_x_ids)
        self.assertNotIn("CORP-Z2", q_x_ids)

        # Поля контракта присутствуют в любой строке результата.
        sample_row = batch[0][0]
        for required in (
            "query_app_id",
            "candidate_app_id",
            "screening_status",
            "app_a_apk_path",
            "app_b_apk_path",
            "retrieval_score",
            "screening_cost_ms",
            "retrieval_rank",
            "per_view_scores",
            "signature_match",
            "shortcut_applied",
        ):
            self.assertIn(required, sample_row, msg=required)
        self.assertNotIn("app_a", sample_row)
        self.assertNotIn("app_b", sample_row)

    def test_empty_query_returns_empty_list(self) -> None:
        corpus = [
            _make_app("C1", {"a", "b", "c"}),
            _make_app("C2", {"a", "b", "d"}),
        ]
        config = _make_config(threshold=0.0)
        self.assertEqual(build_candidate_list_batch([], corpus, config), [])


class TestBatchVsSequentialEquivalence(unittest.TestCase):
    """На одном query батч и последовательный вариант дают одинаковые кандидаты."""

    def test_batch_single_query_matches_sequential(self) -> None:
        base_x = {"f_x_{}".format(i) for i in range(40)}
        base_y = {"f_y_{}".format(i) for i in range(40)}
        corpus = [
            _make_app("CORP-X1", base_x),
            _make_app("CORP-X2", (base_x - {"f_x_0"}) | {"f_extra"}),
            _make_app("CORP-Y1", base_y),
            _make_app("CORP-Y2", (base_y - {"f_y_0"}) | {"f_extra_y"}),
        ]
        query = _make_app("Q-X", base_x | {"f_q"})

        config = _make_config(features=["code"], threshold=0.10)
        batch = build_candidate_list_batch([query], corpus, config)
        self.assertEqual(len(batch), 1)
        batch_rows = batch[0]

        # Sequential: кладём query + corpus в один массив для текущей функции.
        combined = [query] + corpus
        seq_rows = build_candidate_list(
            app_records=combined,
            selected_layers=["code"],
            metric="jaccard",
            threshold=0.10,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params={
                "type": "minhash_lsh",
                "num_perm": 128,
                "bands": 32,
                "seed": 42,
                "features": ["code"],
            },
        )
        seq_rows_filtered = [
            row for row in seq_rows
            if row["query_app_id"] == "Q-X" or row["candidate_app_id"] == "Q-X"
        ]

        # Сравниваем по множеству пар и их retrieval_score: ранк может
        # отличаться (в batch ранг локальный для query, в sequential —
        # глобальный), но содержательно набор кандидатов должен совпадать.
        batch_index = {
            (row["query_app_id"], row["candidate_app_id"]): row["retrieval_score"]
            for row in batch_rows
        }
        seq_index: dict[tuple[str, str], float] = {}
        for row in seq_rows_filtered:
            if row["query_app_id"] == "Q-X":
                key = (row["query_app_id"], row["candidate_app_id"])
            else:
                # В sequential query_app_id = app_a по контракту
                # (app_a/app_b упорядочены по app_id). Учитываем, что
                # Q-X может оказаться как app_a, так и app_b.
                key = (row["candidate_app_id"], row["query_app_id"])
            seq_index[key] = row["retrieval_score"]

        self.assertEqual(set(batch_index.keys()), set(seq_index.keys()))
        for key, score in batch_index.items():
            self.assertAlmostEqual(score, seq_index[key], places=6)


class TestBatchFallbackForExactIndex(unittest.TestCase):
    """При candidate_index.type != minhash_lsh используется fallback."""

    def test_fallback_when_candidate_index_absent(self) -> None:
        """Без блока candidate_index — fallback, вызывается build_candidate_list."""
        corpus = [
            _make_app("C1", {"a", "b", "c"}),
            _make_app("C2", {"a", "b", "d"}),
        ]
        queries = [_make_app("Q1", {"a", "b"})]
        config = _make_config(include_candidate_index=False, threshold=0.0)

        original = screening_runner.build_candidate_list
        with mock.patch.object(
            screening_runner,
            "build_candidate_list",
            wraps=original,
        ) as wrapped:
            results = build_candidate_list_batch(queries, corpus, config)

        self.assertEqual(len(results), 1)
        # Fallback обязан дернуть последовательный build_candidate_list
        # ровно один раз на каждый query.
        self.assertEqual(wrapped.call_count, 1)
        # Проверяем, что в результатах действительно только строки с Q1.
        for row in results[0]:
            self.assertTrue(
                row["query_app_id"] == "Q1" or row["candidate_app_id"] == "Q1"
            )

    def test_fallback_raises_when_unsupported_index_type(self) -> None:
        """``type=exact`` сейчас не принимается парсером и корректно падает."""
        corpus = [_make_app("C1", {"a"}), _make_app("C2", {"a"})]
        queries = [_make_app("Q1", {"a"})]
        config = _make_config(index_type="exact")
        with self.assertRaises(ValueError):
            build_candidate_list_batch(queries, corpus, config)


class TestBatchSpeedup(unittest.TestCase):
    """Батч должен быть ≥3× быстрее последовательного вызова при 5×10."""

    def test_batch_is_at_least_3x_faster_than_sequential(self) -> None:
        # Корпус из 10 приложений двух кластеров + 5 запросов.
        base_a = {"t_a_{}".format(i) for i in range(60)}
        base_b = {"t_b_{}".format(i) for i in range(60)}
        corpus: list[dict] = []
        for i in range(5):
            corpus.append(_make_app("CORP-A{}".format(i), base_a | {"a_mark_{}".format(i)}))
        for i in range(5):
            corpus.append(_make_app("CORP-B{}".format(i), base_b | {"b_mark_{}".format(i)}))

        queries: list[dict] = []
        for i in range(5):
            # 3 запроса в кластере A, 2 в кластере B.
            if i < 3:
                queries.append(_make_app("Q-A{}".format(i), base_a | {"qa_{}".format(i)}))
            else:
                queries.append(_make_app("Q-B{}".format(i), base_b | {"qb_{}".format(i)}))

        config = _make_config(features=["code"], threshold=0.20)

        # Разгоняем JIT/hashlib кешы одним «холостым» прогоном.
        build_candidate_list_batch(queries[:1], corpus, config)

        start = time.perf_counter()
        batch_results = build_candidate_list_batch(queries, corpus, config)
        batch_elapsed = time.perf_counter() - start

        # Последовательный вариант: отдельный build_candidate_list на каждый
        # query_app, индекс пересобирается каждый раз.
        start = time.perf_counter()
        seq_results: list[list[dict]] = []
        for query_app in queries:
            combined = [query_app] + corpus
            per = build_candidate_list(
                app_records=combined,
                selected_layers=["code"],
                metric="jaccard",
                threshold=0.20,
                ins_block_sim_threshold=0.80,
                ged_timeout_sec=30,
                processes_count=1,
                threads_count=2,
                candidate_index_params={
                    "type": "minhash_lsh",
                    "num_perm": 128,
                    "bands": 32,
                    "seed": 42,
                    "features": ["code"],
                },
            )
            filtered = [
                row for row in per
                if row["query_app_id"] == query_app["app_id"]
                or row["candidate_app_id"] == query_app["app_id"]
            ]
            seq_results.append(filtered)
        seq_elapsed = time.perf_counter() - start

        # Проверяем, что количество найденных пар совпадает: speedup без
        # потери покрытия.
        batch_pairs_total = sum(len(rows) for rows in batch_results)
        seq_pairs_total = sum(len(rows) for rows in seq_results)
        # Допустимо небольшое расхождение из-за локальной сортировки, но
        # общее число пар должно совпадать (те же кандидаты).
        self.assertEqual(batch_pairs_total, seq_pairs_total)

        # Основное требование: batch ≥3× быстрее sequential.
        speedup = seq_elapsed / max(batch_elapsed, 1e-9)
        self.assertGreaterEqual(
            speedup,
            3.0,
            msg="batch speedup {:.2f}× < 3×: batch={:.4f}s sequential={:.4f}s".format(
                speedup, batch_elapsed, seq_elapsed
            ),
        )


if __name__ == "__main__":
    unittest.main()

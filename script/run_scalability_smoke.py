#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "SYS-INT-22-WORKERS-SCALABILITY"
    / "report.json"
)
DEFAULT_METHOD_FIX_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "SYS-INT-26-SCALABILITY-METHOD-FIX"
    / "report.json"
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from script import pairwise_runner
except Exception:
    import pairwise_runner  # type: ignore[no-redef]


def parse_workers_grid(raw: str) -> list[int]:
    workers: list[int] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        parsed = int(value)
        if parsed < 1:
            raise ValueError("workers_grid values must be positive integers")
        if parsed not in workers:
            workers.append(parsed)
    if not workers:
        raise ValueError("workers_grid must contain at least one value")
    return workers


def discover_apks(corpus_dir: str | Path) -> list[Path]:
    root = Path(corpus_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.apk") if path.is_file())


def build_pairs(apk_paths: list[Path]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, (apk_a, apk_b) in enumerate(itertools.combinations(apk_paths, 2), start=1):
        pairs.append(
            {
                "pair_id": "SCALABILITY-{:06d}".format(index),
                "app_a": {
                    "app_id": apk_a.stem,
                    "apk_path": str(apk_a),
                },
                "app_b": {
                    "app_id": apk_b.stem,
                    "apk_path": str(apk_b),
                },
            }
        )
    return pairs


def _write_pairwise_inputs(root: Path, pairs: list[dict[str, Any]]) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    enriched_path = root / "enriched.json"
    config_path.write_text(
        """
stages:
  pairwise:
    features: [code, metadata]
    metric: cosine
    threshold: 0.10
""".strip(),
        encoding="utf-8",
    )
    enriched_path.write_text(
        json.dumps({"enriched_candidates": pairs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path, enriched_path


def run_pairwise_for_pairs(
    pairs: list[dict[str, Any]],
    workers: int,
    pair_timeout_sec: int,
) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="scalability-smoke-") as tmpdir:
        config_path, enriched_path = _write_pairwise_inputs(Path(tmpdir), pairs)
        return pairwise_runner.run_pairwise(
            config_path=config_path,
            enriched_path=enriched_path,
            pair_timeout_sec=pair_timeout_sec,
            workers=workers,
        )


def _select_optimal_workers(per_workers: list[dict[str, Any]]) -> int:
    if not per_workers:
        return 0

    optimal = int(per_workers[0]["workers"])
    previous_speedup = float(per_workers[0]["speedup"])
    for row in per_workers[1:]:
        speedup = float(row["speedup"])
        if speedup >= previous_speedup * 1.10:
            optimal = int(row["workers"])
        previous_speedup = speedup
    return optimal


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _build_run_plan(
    workers_grid: list[int],
    n_repeats: int,
    cold_runs: int,
    randomize_order: bool,
) -> list[dict[str, Any]]:
    run_plan: list[dict[str, Any]] = []
    for repeat_index in range(n_repeats):
        round_plan = [
            {
                "workers": workers,
                "repeat_index": repeat_index,
                "run_type": "cold" if repeat_index < cold_runs else "warm",
            }
            for workers in workers_grid
        ]
        if randomize_order:
            random.shuffle(round_plan)
        run_plan.extend(round_plan)
    return run_plan


def _validate_v2_methodology(
    workers_grid: list[int],
    n_repeats: int,
    cold_runs: int,
    warm_runs: int,
) -> None:
    if not workers_grid:
        raise ValueError("workers_grid must contain at least one value")
    if any(workers < 1 for workers in workers_grid):
        raise ValueError("workers_grid values must be positive integers")
    if n_repeats < 5:
        raise ValueError("n_repeats must be >= 5")
    if cold_runs < 1:
        raise ValueError("cold_runs must be >= 1")
    if warm_runs < 1:
        raise ValueError("warm_runs must be >= 1")
    if cold_runs + warm_runs != n_repeats:
        raise ValueError("cold_runs + warm_runs must equal n_repeats")


def _select_optimal_workers_v2(per_workers: list[dict[str, Any]]) -> int:
    if not per_workers:
        return 0

    optimal = int(per_workers[0]["workers"])
    previous_speedup = float(per_workers[0]["speedup_median"])
    for row in per_workers[1:]:
        speedup = float(row["speedup_median"])
        if speedup >= previous_speedup * 1.10:
            optimal = int(row["workers"])
        previous_speedup = speedup
    return optimal


def _feature_cache_env(feature_cache_path: Path):
    class FeatureCacheEnv:
        def __enter__(self):
            self.previous = os.environ.get("FEATURE_CACHE_PATH")
            os.environ["FEATURE_CACHE_PATH"] = str(feature_cache_path)
            return feature_cache_path

        def __exit__(self, exc_type, exc, tb):
            if self.previous is None:
                os.environ.pop("FEATURE_CACHE_PATH", None)
            else:
                os.environ["FEATURE_CACHE_PATH"] = self.previous
            return False

    return FeatureCacheEnv()


def run_scalability_smoke_v2(
    pairs: list[dict[str, Any]],
    workers_grid: list[int] | None = None,
    n_repeats: int = 5,
    randomize_order: bool = True,
    cold_runs: int = 1,
    warm_runs: int = 4,
    pair_timeout_sec: int = 30,
) -> dict[str, Any]:
    if workers_grid is None:
        workers_grid = [1, 2, 4, 8]
    workers_grid = list(workers_grid)
    _validate_v2_methodology(
        workers_grid=workers_grid,
        n_repeats=n_repeats,
        cold_runs=cold_runs,
        warm_runs=warm_runs,
    )

    n_pairs = len(pairs)
    if n_pairs == 0:
        return {
            "methodology": "n-repeat-randomized-cold-warm-v2",
            "workers_grid": workers_grid,
            "n_repeats": n_repeats,
            "randomize_order": randomize_order,
            "cold_runs": cold_runs,
            "warm_runs": warm_runs,
            "per_workers": [],
            "run_order": [],
            "optimal_workers": 0,
            "n_pairs": 0,
            "warning": "empty pairs input",
        }

    run_plan = _build_run_plan(
        workers_grid=workers_grid,
        n_repeats=n_repeats,
        cold_runs=cold_runs,
        randomize_order=randomize_order,
    )
    runs_by_workers: dict[int, list[dict[str, Any]]] = {workers: [] for workers in workers_grid}

    with tempfile.TemporaryDirectory(prefix="scalability-method-fix-cache-") as cache_tmpdir:
        cache_root = Path(cache_tmpdir)
        cache_paths = {
            workers: cache_root / "workers-{}.sqlite".format(workers)
            for workers in workers_grid
        }

        for item in run_plan:
            workers = int(item["workers"])
            cache_path = cache_paths[workers]
            if item["run_type"] == "cold" and cache_path.exists():
                cache_path.unlink()

            started_at = time.perf_counter()
            with _feature_cache_env(cache_path):
                results = run_pairwise_for_pairs(
                    pairs,
                    workers=workers,
                    pair_timeout_sec=pair_timeout_sec,
                )
            total_time_s = time.perf_counter() - started_at
            runs_by_workers[workers].append(
                {
                    "repeat_index": int(item["repeat_index"]),
                    "run_type": item["run_type"],
                    "time_s": _round_metric(total_time_s),
                    "results_count": len(results),
                }
            )

    raw_stats: dict[int, dict[str, float]] = {}
    for workers in workers_grid:
        run_times = [float(run["time_s"]) for run in runs_by_workers[workers]]
        cold_times = [float(run["time_s"]) for run in runs_by_workers[workers] if run["run_type"] == "cold"]
        warm_times = [float(run["time_s"]) for run in runs_by_workers[workers] if run["run_type"] == "warm"]
        raw_stats[workers] = {
            "median_time_s": statistics.median(run_times),
            "p95_time_s": _percentile_nearest_rank(run_times, 95.0),
            "min_time_s": min(run_times),
            "max_time_s": max(run_times),
            "cold_time_s": statistics.mean(cold_times),
            "mean_warm_time_s": statistics.mean(warm_times),
        }

    baseline_workers = workers_grid[0]
    baseline_median = raw_stats[baseline_workers]["median_time_s"]
    baseline_p95 = raw_stats[baseline_workers]["p95_time_s"]
    per_workers: list[dict[str, Any]] = []
    for workers in workers_grid:
        stats = raw_stats[workers]
        median_time_s = stats["median_time_s"]
        p95_time_s = stats["p95_time_s"]
        throughput = (n_pairs / median_time_s) if median_time_s > 0 else 0.0
        per_workers.append(
            {
                "workers": workers,
                "runs": runs_by_workers[workers],
                "median_time_s": _round_metric(median_time_s),
                "p95_time_s": _round_metric(p95_time_s),
                "min_time_s": _round_metric(stats["min_time_s"]),
                "max_time_s": _round_metric(stats["max_time_s"]),
                "cold_time_s": _round_metric(stats["cold_time_s"]),
                "mean_warm_time_s": _round_metric(stats["mean_warm_time_s"]),
                "throughput_median": _round_metric(throughput),
                "speedup_median": _round_metric((baseline_median / median_time_s) if median_time_s > 0 else 0.0),
                "speedup_p95": _round_metric((baseline_p95 / p95_time_s) if p95_time_s > 0 else 0.0),
            }
        )

    return {
        "methodology": "n-repeat-randomized-cold-warm-v2",
        "workers_grid": workers_grid,
        "n_repeats": n_repeats,
        "randomize_order": randomize_order,
        "cold_runs": cold_runs,
        "warm_runs": warm_runs,
        "per_workers": per_workers,
        "run_order": [int(item["workers"]) for item in run_plan],
        "optimal_workers": _select_optimal_workers_v2(per_workers),
        "n_pairs": n_pairs,
    }


def run_scalability_smoke(
    pairs: list[dict[str, Any]],
    workers_grid: list[int] | None = None,
    pair_timeout_sec: int = 30,
) -> dict[str, Any]:
    if workers_grid is None:
        workers_grid = [1, 2, 4, 8]
    workers_grid = list(workers_grid)
    n_pairs = len(pairs)

    if n_pairs == 0:
        return {
            "workers_grid": workers_grid,
            "per_workers": [],
            "optimal_workers": 0,
            "n_pairs": 0,
            "warning": "empty pairs input",
        }

    per_workers: list[dict[str, Any]] = []
    baseline_time_s: float | None = None

    for workers in workers_grid:
        started_at = time.perf_counter()
        run_pairwise_for_pairs(
            pairs,
            workers=workers,
            pair_timeout_sec=pair_timeout_sec,
        )
        total_time_s = time.perf_counter() - started_at
        if baseline_time_s is None or workers == 1:
            baseline_time_s = total_time_s

        speedup = (baseline_time_s / total_time_s) if total_time_s > 0 else 0.0
        throughput = (n_pairs / total_time_s) if total_time_s > 0 else 0.0
        per_workers.append(
            {
                "workers": workers,
                "total_time_s": round(total_time_s, 6),
                "throughput": round(throughput, 6),
                "speedup": round(speedup, 6),
            }
        )

    return {
        "workers_grid": workers_grid,
        "per_workers": per_workers,
        "optimal_workers": _select_optimal_workers(per_workers),
        "n_pairs": n_pairs,
    }


def write_report(report: dict[str, Any], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure pairwise scalability on a fixed mini-corpus.",
    )
    parser.add_argument("--corpus_dir", default="apk/")
    parser.add_argument("--out", default=str(DEFAULT_METHOD_FIX_ARTIFACT_PATH))
    parser.add_argument("--workers_grid", default="1,2,4,8")
    parser.add_argument("--pair_timeout_sec", type=int, default=30)
    parser.add_argument("--n_repeats", type=int, default=5)
    parser.add_argument("--randomize_order", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cold_runs", type=int, default=1)
    parser.add_argument("--warm_runs", type=int, default=4)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use the wave 22 one-shot smoke method instead of the v2 methodology.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    workers_grid = parse_workers_grid(args.workers_grid)
    apk_paths = discover_apks(args.corpus_dir)
    pairs = build_pairs(apk_paths)
    if args.legacy:
        report = run_scalability_smoke(
            pairs,
            workers_grid=workers_grid,
            pair_timeout_sec=args.pair_timeout_sec,
        )
    else:
        report = run_scalability_smoke_v2(
            pairs,
            workers_grid=workers_grid,
            n_repeats=args.n_repeats,
            randomize_order=args.randomize_order,
            cold_runs=args.cold_runs,
            warm_runs=args.warm_runs,
            pair_timeout_sec=args.pair_timeout_sec,
        )
    report["corpus_dir"] = str(args.corpus_dir)
    report["apk_count"] = len(apk_paths)
    report["apk_paths"] = [str(path) for path in apk_paths]
    report_path = write_report(report, args.out)
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

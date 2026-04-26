#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
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
    parser.add_argument("--out", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--workers_grid", default="1,2,4,8")
    parser.add_argument("--pair_timeout_sec", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    workers_grid = parse_workers_grid(args.workers_grid)
    apk_paths = discover_apks(args.corpus_dir)
    pairs = build_pairs(apk_paths)
    report = run_scalability_smoke(
        pairs,
        workers_grid=workers_grid,
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

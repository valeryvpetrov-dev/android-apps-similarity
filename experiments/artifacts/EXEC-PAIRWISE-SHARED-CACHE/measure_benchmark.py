#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any


ARTIFACT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ARTIFACT_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script.feature_cache_sqlite import FeatureCacheSqlite
from script import pairwise_runner


UNIQUE_APKS = 5
REPEATS_PER_PATTERN = 4
WORKERS = 2
EXTRACT_SLEEP_SECONDS = 0.12
PAIR_PATTERN = (
    (0, 1),
    (2, 3),
    (4, 0),
    (1, 2),
    (3, 4),
)


def _build_synthetic_feature_bundle(apk_path: str, unpacked_dir: str | None) -> dict[str, Any]:
    del unpacked_dir
    time.sleep(EXTRACT_SLEEP_SECONDS)
    digest = pairwise_runner._sha256_of_file(Path(apk_path))
    token = digest[:12]
    return {
        "mode": "synthetic-benchmark",
        "code": {"code:{}".format(token)},
        "metadata": {"meta:{}".format(token[:6])},
        "component": {
            "activities": [{"name": ".Synthetic{}".format(token[:4])}],
            "services": [],
            "receivers": [],
            "providers": [],
            "permissions": {"permission.{}".format(token[:6])},
            "features": set(),
        },
        "resource": {
            "resource_digests": {
                ("res/layout/main.xml", digest),
            },
        },
        "library": {
            "libraries": {
                "lib.{}".format(token[:8]): {"class_count": 1},
            },
        },
    }


def _prepare_worker_module():
    import script.pairwise_runner as pr

    pr.extract_all_features = _build_synthetic_feature_bundle
    pr.compare_signatures = None
    pr.extract_apk_signature_hash = None
    return pr


def _worker_without_cache(candidate_json: str, config_path_str: str) -> str:
    pr = _prepare_worker_module()
    candidate = json.loads(candidate_json)
    config = pr.load_config(Path(config_path_str))
    selected_layers, metric, threshold = pr.parse_pairwise_stage(config)
    row = pr._compute_pair_row_with_caches(
        candidate=candidate,
        selected_layers=selected_layers,
        metric=metric,
        threshold=threshold,
        ins_block_sim_threshold=0.8,
        ged_timeout_sec=30,
        processes_count=1,
        threads_count=2,
        layer_cache={},
        code_cache={},
        apk_discovery_cache={},
        feature_cache=None,
    )
    return json.dumps(row)


def _worker_with_cache(
    candidate_json: str,
    config_path_str: str,
    cache_path_str: str,
) -> str:
    pr = _prepare_worker_module()
    candidate = json.loads(candidate_json)
    config = pr.load_config(Path(config_path_str))
    selected_layers, metric, threshold = pr.parse_pairwise_stage(config)
    feature_cache = FeatureCacheSqlite(Path(cache_path_str))
    try:
        row = pr._compute_pair_row_with_caches(
            candidate=candidate,
            selected_layers=selected_layers,
            metric=metric,
            threshold=threshold,
            ins_block_sim_threshold=0.8,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            layer_cache={},
            code_cache={},
            apk_discovery_cache={},
            feature_cache=feature_cache,
        )
    finally:
        feature_cache.close()
    return json.dumps(row)


def _write_config(path: Path) -> None:
    payload = {
        "stages": {
            "pairwise": {
                "features": ["component", "resource", "library"],
                "metric": "cosine",
                "threshold": 0.1,
            }
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_candidates(root: Path) -> tuple[Path, list[str]]:
    config_path = root / "benchmark-config.json"
    _write_config(config_path)

    apk_paths = []
    for index in range(UNIQUE_APKS):
        apk_path = root / "synthetic-{}.apk".format(index)
        apk_path.write_bytes(("synthetic-apk-{:02d}".format(index) * 4096).encode("utf-8"))
        apk_paths.append(apk_path)

    candidates = []
    for repeat in range(REPEATS_PER_PATTERN):
        for left_index, right_index in PAIR_PATTERN:
            candidates.append(
                {
                    "pair_id": "pair-{:02d}-{:02d}".format(repeat, len(candidates)),
                    "app_a": {
                        "app_id": "APK-{}".format(left_index),
                        "apk_path": str(apk_paths[left_index]),
                        "decoded_dir": "/synthetic/decoded/{}".format(left_index),
                    },
                    "app_b": {
                        "app_id": "APK-{}".format(right_index),
                        "apk_path": str(apk_paths[right_index]),
                        "decoded_dir": "/synthetic/decoded/{}".format(right_index),
                    },
                }
            )
    return config_path, [json.dumps(candidate) for candidate in candidates]


def _run_pool_without_cache(candidate_jsons: list[str], config_path: Path) -> float:
    started = time.perf_counter()
    with pairwise_runner._process_pool_sysconf_workaround(), ProcessPoolExecutor(
        max_workers=WORKERS
    ) as executor:
        futures = [
            executor.submit(_worker_without_cache, candidate_json, str(config_path))
            for candidate_json in candidate_jsons
        ]
        for future in futures:
            future.result()
    return time.perf_counter() - started


def _run_pool_with_cache(candidate_jsons: list[str], config_path: Path, cache_path: Path) -> float:
    started = time.perf_counter()
    with pairwise_runner._process_pool_sysconf_workaround(), ProcessPoolExecutor(
        max_workers=WORKERS
    ) as executor:
        futures = [
            executor.submit(_worker_with_cache, candidate_json, str(config_path), str(cache_path))
            for candidate_json in candidate_jsons
        ]
        for future in futures:
            future.result()
    return time.perf_counter() - started


def main() -> None:
    report_path = ARTIFACT_DIR / "benchmark-report.json"
    with tempfile.TemporaryDirectory(prefix="exec-pairwise-shared-cache-") as tmpdir:
        root = Path(tmpdir)
        cache_path = root / "feature-cache.sqlite"
        config_path, candidate_jsons = _build_candidates(root)

        time_before = _run_pool_without_cache(candidate_jsons, config_path)
        time_after = _run_pool_with_cache(candidate_jsons, config_path, cache_path)

        with sqlite3.connect(cache_path) as conn:
            cache_rows = conn.execute("SELECT COUNT(*) FROM feature_cache").fetchone()[0]

    report = {
        "artifact_id": "EXEC-PAIRWISE-SHARED-CACHE",
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workload": {
            "workers": WORKERS,
            "unique_apks": UNIQUE_APKS,
            "pair_count": len(candidate_jsons),
            "pair_pattern": [list(item) for item in PAIR_PATTERN],
            "repeats_per_pattern": REPEATS_PER_PATTERN,
            "synthetic_extract_sleep_seconds": EXTRACT_SLEEP_SECONDS,
        },
        "time_before_seconds": round(time_before, 6),
        "time_after_seconds": round(time_after, 6),
        "speedup_ratio": round(time_before / time_after, 6),
        "cache_rows_after_run": cache_rows,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

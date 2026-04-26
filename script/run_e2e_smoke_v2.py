#!/usr/bin/env python3
"""SYS-25-E2E-RUNNER-CONTRACT smoke for pairwise_runner.

The harness keeps external APK tooling out of the critical path, but calls the
real pairwise_runner.run_pairwise API with workers, hard timeout, SQLite feature
cache, mixed regular/shortcut pairs and two cascade configs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "SYS-25-E2E-RUNNER"
    / "report.json"
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from script import pairwise_runner  # noqa: E402


class _SyntheticFeatureExtractor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.phase = "cold"
        self.calls_by_phase: Counter[str] = Counter()
        self.calls_by_apk: Counter[str] = Counter()

    def __call__(self, apk_path: str, unpacked_dir: str | None = None) -> dict[str, Any]:
        stem = Path(apk_path).stem
        with self._lock:
            self.calls_by_phase[self.phase] += 1
            self.calls_by_apk[stem] += 1

        code_group = stem
        metadata_group = stem
        if stem.startswith("normal_1_"):
            code_group = "normal-1-code"
            metadata_group = "normal-1-meta"
        elif stem.startswith("normal_2_"):
            code_group = "normal-2-code"
            metadata_group = "normal-2-meta-a" if stem.endswith("_a") else "normal-2-meta-b"
        elif stem.startswith("normal_3_"):
            code_group = "normal-3-code"
            metadata_group = "normal-3-meta"
        elif stem.startswith("hang_"):
            code_group = "hang-code"
            metadata_group = "hang-meta"

        return {
            "mode": "enhanced",
            "code": {code_group},
            "metadata": {metadata_group},
            "component": {
                "activities": [],
                "services": [],
                "receivers": [],
                "providers": [],
                "permissions": set(),
                "features": set(),
            },
            "resource": {"resource_digests": set()},
            "library": {"libraries": {}},
        }


class _PairWorkerTimeoutOnce:
    def __init__(self, original_worker: Any, sleep_sec: float) -> None:
        self._original_worker = original_worker
        self._sleep_sec = sleep_sec
        self._lock = threading.Lock()
        self._timeout_consumed = False

    def __call__(
        self,
        candidate_json: str,
        config_path_str: str,
        ins_block_sim_threshold: float,
        ged_timeout_sec: int,
        processes_count: int,
        threads_count: int,
        feature_cache_path_str: str | None = None,
    ) -> str:
        candidate = json.loads(candidate_json)
        should_sleep = bool(candidate.get("force_timeout_once"))
        if should_sleep:
            with self._lock:
                should_sleep = not self._timeout_consumed
                if should_sleep:
                    self._timeout_consumed = True
        if should_sleep:
            time.sleep(self._sleep_sec)
            return json.dumps(
                {
                    "pair_id": candidate.get("pair_id"),
                    "app_a": candidate["app_a"]["app_id"],
                    "app_b": candidate["app_b"]["app_id"],
                    "full_similarity_score": 0.0,
                    "library_reduced_score": 0.0,
                    "status": "late_after_timeout",
                    "views_used": [],
                    "signature_match": {"score": 0.0, "status": "missing"},
                    "evidence": [],
                }
            )

        return self._original_worker(
            candidate_json,
            config_path_str,
            ins_block_sim_threshold,
            ged_timeout_sec,
            processes_count,
            threads_count,
            feature_cache_path_str,
        )


def _write_config(path: Path, *, name: str) -> None:
    if name == "baseline":
        features = "[code]"
    elif name == "multi_view":
        features = "[code, metadata]"
    else:
        raise ValueError("unknown config: {}".format(name))
    path.write_text(
        """
stages:
  pairwise:
    features: {}
    metric: jaccard
    threshold: 0.90
""".format(features).strip(),
        encoding="utf-8",
    )


def _touch_apk(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", "<manifest package='{}'/>".format(path.stem))
        archive.writestr("classes.dex", b"dex\n035\x00" + path.stem.encode("utf-8"))
        archive.writestr("META-INF/CERT.RSA", ("cert-{}".format(path.stem)).encode("utf-8"))


def _app(apk_path: Path) -> dict[str, str]:
    return {
        "app_id": apk_path.stem,
        "apk_path": str(apk_path),
    }


def _shortcut_pair(pair_id: str, left: Path, right: Path) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "app_a": _app(left),
        "app_b": _app(right),
        "shortcut_applied": True,
        "shortcut_reason": pairwise_runner.SHORTCUT_REASON_HIGH_CONFIDENCE,
        "signature_match": {"score": 1.0, "status": "match"},
    }


def _build_pairs(corpus_dir: Path) -> list[dict[str, Any]]:
    names = [
        "normal_1_a",
        "normal_1_b",
        "normal_2_a",
        "normal_2_b",
        "normal_3_a",
        "normal_3_b",
        "shortcut_1_a",
        "shortcut_1_b",
        "shortcut_2_a",
        "shortcut_2_b",
        "hang_a",
        "hang_b",
    ]
    paths = {name: corpus_dir / "{}.apk".format(name) for name in names}
    for path in paths.values():
        _touch_apk(path)

    return [
        {
            "pair_id": "SYS25-NORMAL-001",
            "app_a": _app(paths["normal_1_a"]),
            "app_b": _app(paths["normal_1_b"]),
        },
        {
            "pair_id": "SYS25-NORMAL-002",
            "app_a": _app(paths["normal_2_a"]),
            "app_b": _app(paths["normal_2_b"]),
        },
        {
            "pair_id": "SYS25-NORMAL-003",
            "app_a": _app(paths["normal_3_a"]),
            "app_b": _app(paths["normal_3_b"]),
        },
        _shortcut_pair("SYS25-SHORTCUT-001", paths["shortcut_1_a"], paths["shortcut_1_b"]),
        _shortcut_pair("SYS25-SHORTCUT-002", paths["shortcut_2_a"], paths["shortcut_2_b"]),
        {
            "pair_id": "SYS25-TIMEOUT-001",
            "app_a": _app(paths["hang_a"]),
            "app_b": _app(paths["hang_b"]),
            "force_timeout_once": True,
        },
    ]


def _write_enriched(path: Path, pairs: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"enriched_candidates": pairs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _compact_row(pair: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "pair_id": pair.get("pair_id"),
        "app_a": row.get("app_a"),
        "app_b": row.get("app_b"),
        "status": row.get("status"),
        "analysis_failed_reason": row.get("analysis_failed_reason"),
        "full_similarity_score": row.get("full_similarity_score"),
        "library_reduced_score": row.get("library_reduced_score"),
    }
    for key in (
        "timeout_info",
        "shortcut_status",
        "deep_verification_status",
        "verdict",
    ):
        if key in row:
            compact[key] = row[key]
    return compact


def _summarize_config(
    *,
    config_name: str,
    rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    elapsed_s: float,
) -> dict[str, Any]:
    per_pair_status = [
        _compact_row(pair, row)
        for pair, row in zip(pairs, rows)
    ]
    status_counts = Counter(row["status"] for row in per_pair_status)
    timeout_count = sum(
        1
        for row in per_pair_status
        if row.get("analysis_failed_reason") == "budget_exceeded"
    )
    return {
        "config_name": config_name,
        "total_time_s": round(elapsed_s, 6),
        "status_counts": dict(status_counts),
        "timeout_count": timeout_count,
        "per_pair_status": per_pair_status,
    }


def _run_pairwise_config(
    *,
    config_path: Path,
    enriched_path: Path,
    pairs: list[dict[str, Any]],
    config_name: str,
    workers: int,
    pair_timeout_sec: int,
    feature_cache_path: Path,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    rows = pairwise_runner.run_pairwise(
        config_path=config_path,
        enriched_path=enriched_path,
        workers=workers,
        pair_timeout_sec=pair_timeout_sec,
        feature_cache_path=feature_cache_path,
    )
    elapsed_s = time.perf_counter() - started_at
    return _summarize_config(
        config_name=config_name,
        rows=rows,
        pairs=pairs,
        elapsed_s=elapsed_s,
    )


def _patch_pairwise_for_synthetic_run(
    extractor: _SyntheticFeatureExtractor,
    timeout_sleep_sec: float,
):
    original_make_executor = pairwise_runner._make_parallel_executor
    original_extract = pairwise_runner.extract_all_features
    original_worker = pairwise_runner._pair_worker_isolated
    timeout_worker = _PairWorkerTimeoutOnce(
        original_worker=original_worker,
        sleep_sec=timeout_sleep_sec,
    )

    pairwise_runner._make_parallel_executor = ThreadPoolExecutor
    pairwise_runner.extract_all_features = extractor
    pairwise_runner._pair_worker_isolated = timeout_worker

    def restore() -> None:
        pairwise_runner._make_parallel_executor = original_make_executor
        pairwise_runner.extract_all_features = original_extract
        pairwise_runner._pair_worker_isolated = original_worker

    return restore


def run_contract_smoke(
    *,
    out_path: str | Path = DEFAULT_ARTIFACT_PATH,
    workers: int = 4,
    pair_timeout_sec: int = 10,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    feature_cache_path = out.parent / "feature-cache.sqlite"
    if feature_cache_path.exists():
        feature_cache_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(feature_cache_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    previous_skip = os.environ.get("SIMILARITY_SKIP_REQ_CHECK")
    os.environ["SIMILARITY_SKIP_REQ_CHECK"] = "1"

    with tempfile.TemporaryDirectory(prefix="sys25-e2e-runner-") as tmpdir:
        root = Path(tmpdir)
        corpus_dir = root / "corpus"
        corpus_dir.mkdir()
        pairs = _build_pairs(corpus_dir)
        regular_and_shortcut_pairs = [
            pair
            for pair in pairs
            if pair.get("pair_id") != "SYS25-TIMEOUT-001"
        ]

        baseline_config = root / "baseline.yaml"
        multi_view_config = root / "multi_view.yaml"
        full_enriched = root / "enriched-full.json"
        warm_enriched = root / "enriched-warm.json"
        _write_config(baseline_config, name="baseline")
        _write_config(multi_view_config, name="multi_view")
        _write_enriched(full_enriched, pairs)
        _write_enriched(warm_enriched, regular_and_shortcut_pairs)

        extractor = _SyntheticFeatureExtractor()
        restore = _patch_pairwise_for_synthetic_run(
            extractor,
            timeout_sleep_sec=float(pair_timeout_sec) + 0.5,
        )
        try:
            extractor.phase = "cold"
            baseline_summary = _run_pairwise_config(
                config_path=baseline_config,
                enriched_path=full_enriched,
                pairs=pairs,
                config_name="baseline",
                workers=workers,
                pair_timeout_sec=pair_timeout_sec,
                feature_cache_path=feature_cache_path,
            )

            cold_extract_calls = int(extractor.calls_by_phase["cold"])
            extractor.phase = "warm"
            _run_pairwise_config(
                config_path=baseline_config,
                enriched_path=warm_enriched,
                pairs=regular_and_shortcut_pairs,
                config_name="baseline_warm_cache_probe",
                workers=workers,
                pair_timeout_sec=pair_timeout_sec,
                feature_cache_path=feature_cache_path,
            )
            warm_extract_calls = int(extractor.calls_by_phase["warm"])

            extractor.phase = "multi_view"
            multi_view_summary = _run_pairwise_config(
                config_path=multi_view_config,
                enriched_path=full_enriched,
                pairs=pairs,
                config_name="multi_view",
                workers=workers,
                pair_timeout_sec=pair_timeout_sec,
                feature_cache_path=feature_cache_path,
            )
        finally:
            restore()
            if previous_skip is None:
                os.environ.pop("SIMILARITY_SKIP_REQ_CHECK", None)
            else:
                os.environ["SIMILARITY_SKIP_REQ_CHECK"] = previous_skip

        if cold_extract_calls > 0:
            cache_hit_rate = max(0.0, 1.0 - (warm_extract_calls / cold_extract_calls))
        else:
            cache_hit_rate = 0.0

        report = {
            "corpus_size": 12,
            "n_pairs": len(pairs),
            "workers": workers,
            "pair_timeout_sec": pair_timeout_sec,
            "total_time_s": round(time.perf_counter() - total_started_at, 6),
            "per_pair_status": baseline_summary["per_pair_status"],
            "cache_hit_rate": round(cache_hit_rate, 6),
            "timeout_count": int(baseline_summary["timeout_count"]),
            "configs_compared": [baseline_summary, multi_view_summary],
            "cache_trace": {
                "feature_cache_path": str(feature_cache_path),
                "cold_extract_calls": cold_extract_calls,
                "warm_extract_calls": warm_extract_calls,
                "calls_by_phase": dict(extractor.calls_by_phase),
                "calls_by_apk": dict(extractor.calls_by_apk),
            },
        }

    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run SYS-25 e2e pairwise_runner contract smoke.",
    )
    parser.add_argument("--out", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pair-timeout-sec", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_contract_smoke(
        out_path=args.out,
        workers=args.workers,
        pair_timeout_sec=args.pair_timeout_sec,
    )
    print(
        json.dumps(
            {
                "report": str(Path(args.out)),
                "total_time_s": report["total_time_s"],
                "cache_hit_rate": report["cache_hit_rate"],
                "timeout_count": report["timeout_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure NoiseCache recall on repeated LIBLOOM noise detection passes."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script import libloom_adapter
    from script import noise_profile_envelope
    from script.noise_cache import NoiseCache
except ImportError:  # pragma: no cover - standalone script fallback
    import libloom_adapter  # type: ignore[no-redef]
    import noise_profile_envelope  # type: ignore[no-redef]
    from noise_cache import NoiseCache  # type: ignore[no-redef]


RUN_ID = "NOISE-27-NOISE-CACHE-RECALL"
DEFAULT_CORPUS_DIR = (
    Path.home()
    / "Library"
    / "Caches"
    / "phd-shared"
    / "datasets"
    / "fdroid-corpus-v2-apks"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / RUN_ID
    / "report.json"
)
MINI_CORPUS_SIZE = 5
PROFILE_VERSION = "noise-cache-recall-v1"


def discover_apks(corpus_dir: str | Path) -> list[Path]:
    root = Path(corpus_dir).expanduser()
    if not root.is_dir():
        raise ValueError(f"corpus_dir not found: {root}")
    apk_paths = sorted(path for path in root.rglob("*.apk") if path.is_file())
    if not apk_paths:
        raise ValueError(f"no APK files found under: {root}")
    return apk_paths


def _write_mini_corpus(root: Path, n_apks: int = MINI_CORPUS_SIZE) -> list[Path]:
    corpus_dir = root / "mini-corpus-apks"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    apk_paths: list[Path] = []
    for index in range(n_apks):
        apk_path = corpus_dir / f"mini_{index}.apk"
        apk_path.write_bytes(f"mini-apk-{index}".encode("utf-8"))
        apk_paths.append(apk_path)
    return apk_paths


def _sha256_file(path: Path) -> str | None:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as apk_file:
            for chunk in iter(lambda: apk_file.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def _empty_envelope(apk_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "nc-v1",
        "status": "success",
        "apk_name": apk_path.name,
    }


def _run_pass(
    apk_paths: Sequence[Path],
    cache: NoiseCache,
    *,
    timeout_sec: int,
) -> dict[str, Any]:
    total_time_s = 0.0
    cache_hits = 0

    for apk_path in apk_paths:
        apk_sha256 = _sha256_file(apk_path)
        if apk_sha256 is not None and cache.get(apk_sha256) is not None:
            cache_hits += 1

        started = time.perf_counter()
        noise_profile_envelope.apply_libloom_detection(
            apk_path=str(apk_path),
            apkid_result={"gate_status": "clean"},
            libloom_jar_path=None,
            libs_profile_dir=None,
            envelope=_empty_envelope(apk_path),
            timeout_sec=timeout_sec,
            cache=cache,
        )
        total_time_s += time.perf_counter() - started

    avg_time_s = total_time_s / len(apk_paths) if apk_paths else 0.0
    return {
        "avg_time_s": avg_time_s,
        "total_time_s": total_time_s,
        "cache_hits": cache_hits,
    }


def _write_report(report: dict[str, Any], output_path: str | Path) -> None:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_report(
    *,
    apk_paths: Sequence[Path],
    requested_corpus_dir: Path,
    resolved_corpus_dir: Path | None,
    corpus_source: str,
    libloom_runtime: dict[str, Any],
    warnings: list[str],
    pass_metrics: list[dict[str, Any]],
    n_iterations: int,
) -> dict[str, Any]:
    pass_1 = pass_metrics[0] if pass_metrics else {
        "avg_time_s": 0.0,
        "total_time_s": 0.0,
        "cache_hits": 0,
    }
    pass_2 = pass_metrics[1] if len(pass_metrics) > 1 else {
        "avg_time_s": 0.0,
        "total_time_s": 0.0,
        "cache_hits": 0,
    }
    cache_hit_ratio = (
        pass_2["cache_hits"] / len(apk_paths)
        if apk_paths and len(pass_metrics) > 1
        else 0.0
    )
    speedup_factor = (
        pass_1["avg_time_s"] / pass_2["avg_time_s"]
        if pass_2["avg_time_s"] > 0
        else 0.0
    )
    report: dict[str, Any] = {
        "run_id": RUN_ID,
        "status": "ok",
        "corpus_size": len(apk_paths),
        "corpus_source": corpus_source,
        "requested_corpus_dir": str(requested_corpus_dir),
        "corpus_dir": str(resolved_corpus_dir) if resolved_corpus_dir else None,
        "n_iterations": n_iterations,
        "pass_1": pass_1,
        "pass_2": pass_2,
        "cache_hit_ratio": cache_hit_ratio,
        "avg_first_pass_s": pass_1["avg_time_s"],
        "avg_second_pass_s": pass_2["avg_time_s"],
        "speedup_factor": speedup_factor,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "warnings": warnings,
        "source": {
            "script": "script/run_noise_cache_recall.py",
            "libloom_runtime": libloom_runtime,
        },
    }
    for index, metrics in enumerate(pass_metrics[2:], start=3):
        report[f"pass_{index}"] = metrics
    return report


def run_recall(
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    output_path: str | Path = DEFAULT_OUTPUT,
    n_iterations: int = 2,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    if n_iterations < 2:
        raise ValueError("n_iterations must be >= 2")

    requested_corpus_dir = Path(corpus_dir).expanduser()
    warnings: list[str] = []
    corpus_source = "fdroid_v2"
    resolved_corpus_dir: Path | None = None

    with tempfile.TemporaryDirectory(prefix="noise-cache-recall-") as tmp:
        tmp_root = Path(tmp)
        try:
            apk_paths = discover_apks(requested_corpus_dir)
            resolved_corpus_dir = requested_corpus_dir.resolve()
        except ValueError as exc:
            warnings.append("fallback_mini_corpus_used")
            warnings.append(str(exc))
            apk_paths = _write_mini_corpus(tmp_root)
            corpus_source = "mini_corpus"

        libloom_runtime = libloom_adapter.verify_libloom_setup()
        if not libloom_runtime.get("available"):
            warnings.append(
                "libloom_unavailable: {}".format(
                    libloom_runtime.get("reason") or "unknown"
                )
            )

        cache = NoiseCache(tmp_root / "noise-cache", profile_version=PROFILE_VERSION)
        pass_metrics = [
            _run_pass(apk_paths, cache, timeout_sec=timeout_sec)
            for _ in range(n_iterations)
        ]
        report = _build_report(
            apk_paths=apk_paths,
            requested_corpus_dir=requested_corpus_dir,
            resolved_corpus_dir=resolved_corpus_dir,
            corpus_source=corpus_source,
            libloom_runtime=libloom_runtime,
            warnings=warnings,
            pass_metrics=pass_metrics,
            n_iterations=n_iterations,
        )

    _write_report(report, output_path)
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure NoiseCache cache-hit recall on repeated APK passes."
    )
    parser.add_argument(
        "--corpus_dir",
        "--corpus-dir",
        default=str(DEFAULT_CORPUS_DIR),
        help="APK corpus directory (default: F-Droid v2 cache)",
    )
    parser.add_argument(
        "--out",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"output report path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--n_iterations",
        "--n-iterations",
        type=int,
        default=2,
        help="number of repeated passes; pass 1 warms cache, pass 2 measures recall",
    )
    parser.add_argument(
        "--timeout_sec",
        "--timeout-sec",
        type=int,
        default=600,
        help="LIBLOOM timeout passed through to apply_libloom_detection",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        report = run_recall(
            corpus_dir=args.corpus_dir,
            output_path=args.out,
            n_iterations=args.n_iterations,
            timeout_sec=args.timeout_sec,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for warning in report.get("warnings", []):
        print(f"WARNING: {warning}", file=sys.stderr)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

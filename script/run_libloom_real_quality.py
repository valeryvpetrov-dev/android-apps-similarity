#!/usr/bin/env python3
"""NOISE-26 real LIBLOOM quality runner for the F-Droid v2 corpus."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

try:
    from script import libloom_adapter
    from script import library_view_v2
except ImportError:  # pragma: no cover - standalone script fallback
    import libloom_adapter  # type: ignore[no-redef]
    import library_view_v2  # type: ignore[no-redef]


RUN_ID = "NOISE-26-LIBLOOM-REAL"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
SOURCE_PATH = "script/run_libloom_real_quality.py"


def canonicalize_tpl(name: str) -> str:
    raw = str(name).strip().lower().replace("_", "-")
    aliases = {
        "okhttp": "okhttp3",
        "okhttp3": "okhttp3",
        "com.squareup.okhttp3": "okhttp3",
        "retrofit": "retrofit2",
        "retrofit2": "retrofit2",
        "com.squareup.retrofit2": "retrofit2",
        "com.google.gson": "gson",
        "gson": "gson",
        "kotlinx.coroutines": "kotlinx-coroutines",
        "kotlinx-coroutines": "kotlinx-coroutines",
    }
    if raw in aliases:
        return aliases[raw]
    for prefix, normalized in (
        ("com.squareup.okhttp3.", "okhttp3"),
        ("com.squareup.retrofit2.", "retrofit2"),
        ("com.google.gson.", "gson"),
        ("com.bumptech.glide.", "glide"),
        ("kotlinx.coroutines.", "kotlinx-coroutines"),
    ):
        if raw.startswith(prefix):
            return normalized
    for suffix in ("-android", "-jvm", "-runtime", "-core"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return aliases.get(raw, raw)


def compute_precision_recall(tp: int, fp: int, fn: int) -> tuple[float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def score_tpls(predicted: list[str], ground_truth: list[str]) -> dict[str, Any]:
    predicted_set = {canonicalize_tpl(name) for name in predicted if name}
    ground_truth_set = {canonicalize_tpl(name) for name in ground_truth if name}
    tp_names = predicted_set & ground_truth_set
    fp_names = predicted_set - ground_truth_set
    fn_names = ground_truth_set - predicted_set
    precision, recall = compute_precision_recall(
        len(tp_names),
        len(fp_names),
        len(fn_names),
    )
    return {
        "tp": len(tp_names),
        "fp": len(fp_names),
        "fn": len(fn_names),
        "precision": precision,
        "recall": recall,
        "tp_names": sorted(tp_names),
        "fp_names": sorted(fp_names),
        "fn_names": sorted(fn_names),
    }


def discover_apks(corpus_dir: str | Path, limit: int | None = None) -> list[Path]:
    root = Path(corpus_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"corpus_dir not found: {root}")
    apk_paths = sorted(path for path in root.rglob("*.apk") if path.is_file())
    if limit is not None:
        apk_paths = apk_paths[:limit]
    if not apk_paths:
        raise ValueError(f"no APK files found under: {root}")
    return apk_paths


def _decoded_root_for_corpus(
    apk_paths: Sequence[Path],
    decoded_root: str | Path | None,
) -> Path | None:
    if decoded_root is not None:
        candidate = Path(decoded_root).expanduser().resolve()
        return candidate if candidate.is_dir() else None

    configured = os.environ.get("SIMILARITY_DECODED_CORPUS_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if candidate.is_dir() else None

    if not apk_paths:
        return None
    corpus_root = apk_paths[0].parent
    if corpus_root.name.endswith("-apks"):
        candidate = corpus_root.with_name(corpus_root.name[:-5] + "-decoded")
        if candidate.is_dir():
            return candidate
    return None


def _smali_path_to_package(decoded_dir: Path, smali_path: Path) -> str | None:
    try:
        rel_parts = smali_path.relative_to(decoded_dir).parts
    except ValueError:
        return None
    if len(rel_parts) < 3:
        return None
    smali_root = rel_parts[0]
    if smali_root != "smali" and not smali_root.startswith("smali_classes"):
        return None
    package_parts = rel_parts[1:-1]
    if not package_parts:
        return None
    return ".".join(part for part in package_parts if part)


def _extract_packages_from_decoded_dir(decoded_dir: Path) -> frozenset[str]:
    packages: set[str] = set()
    for smali_root in sorted(decoded_dir.iterdir()):
        if not smali_root.is_dir():
            continue
        if smali_root.name != "smali" and not smali_root.name.startswith("smali_classes"):
            continue
        for smali_path in smali_root.rglob("*.smali"):
            package_name = _smali_path_to_package(decoded_dir, smali_path)
            if package_name:
                packages.add(package_name)
    return frozenset(packages)


def build_synthetic_labels(
    apk_paths: Sequence[Path],
    decoded_root: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    resolved_decoded_root = _decoded_root_for_corpus(apk_paths, decoded_root)
    labels: dict[str, dict[str, Any]] = {}
    for apk_path in apk_paths:
        decoded_dir = (
            resolved_decoded_root / apk_path.stem
            if resolved_decoded_root is not None
            else None
        )
        if decoded_dir is None or not decoded_dir.is_dir():
            labels[apk_path.name] = {
                "ground_truth": [],
                "label_source": "missing-decoded",
                "decoded_dir": str(decoded_dir) if decoded_dir is not None else None,
                "package_count": 0,
            }
            continue

        packages = _extract_packages_from_decoded_dir(decoded_dir)
        detections = library_view_v2.detect_tpl_in_packages(packages)
        ground_truth = sorted(
            {
                canonicalize_tpl(tpl_id)
                for tpl_id, info in detections.items()
                if isinstance(info, dict) and info.get("detected")
            }
        )
        labels[apk_path.name] = {
            "ground_truth": ground_truth,
            "label_source": "decoded-library_view_v2",
            "decoded_dir": str(decoded_dir),
            "package_count": len(packages),
        }
    return labels


def _write_report(report: dict[str, Any], output_path: str | Path) -> None:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _source_payload(
    corpus_dir: Path,
    decoded_root: str | Path | None,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "script": SOURCE_PATH,
        "corpus_dir": str(corpus_dir),
        "decoded_root": str(decoded_root) if decoded_root is not None else None,
        "libloom_home": os.environ.get(libloom_adapter.LIBLOOM_HOME_ENV_VAR),
        "jar_path": runtime.get("jar_path"),
        "libs_profile_dir": runtime.get("libs_profile_dir"),
        "version": runtime.get("version"),
    }


def _blocked_report(
    apk_paths: Sequence[Path],
    corpus_dir: Path,
    decoded_root: str | Path | None,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    reason = str(runtime.get("reason") or "LIBLOOM is unavailable")
    per_apk_results = [
        {
            "apk_path": str(apk_path),
            "apk_name": apk_path.name,
            "ground_truth": [],
            "label_source": "not_evaluated_libloom_blocked",
            "detected_tpls": [],
            "libloom_status": "blocked",
            "libloom_error_reason": reason,
            "libloom_elapsed_sec": 0.0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "precision": 0.0,
            "recall": 0.0,
            "tp_names": [],
            "fp_names": [],
            "fn_names": [],
        }
        for apk_path in apk_paths
    ]
    return {
        "run_id": RUN_ID,
        "status": "libloom_blocked",
        "reason": reason,
        "corpus_size": len(apk_paths),
        "n_apks_with_tpl": 0,
        "precision": 0.0,
        "recall": 0.0,
        "coverage": 0.0,
        "per_apk_results": per_apk_results,
        "top_detected_tpl": [],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "warnings": [reason],
        "source": _source_payload(corpus_dir, decoded_root, runtime),
    }


def _top_detected_tpl(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"tpl": tpl, "count": count}
        for tpl, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def run_quality(
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    output_path: str | Path = DEFAULT_OUTPUT,
    decoded_root: str | Path | None = None,
    timeout_sec: int = libloom_adapter.DEFAULT_TIMEOUT_SEC,
    java_heap_mb: int = libloom_adapter.DEFAULT_JAVA_HEAP_MB,
    limit: int | None = None,
) -> dict[str, Any]:
    apk_paths = discover_apks(corpus_dir, limit=limit)
    resolved_corpus_dir = Path(corpus_dir).expanduser().resolve()
    runtime = libloom_adapter.verify_libloom_setup()
    if not runtime.get("available"):
        report = _blocked_report(apk_paths, resolved_corpus_dir, decoded_root, runtime)
        _write_report(report, output_path)
        return report

    labels = build_synthetic_labels(apk_paths, decoded_root=decoded_root)
    total_tp = 0
    total_fp = 0
    total_fn = 0
    n_apks_with_tpl = 0
    detected_counter: Counter[str] = Counter()
    per_apk_results: list[dict[str, Any]] = []

    for apk_path in apk_paths:
        label_entry = labels.get(apk_path.name, {})
        ground_truth = list(label_entry.get("ground_truth", []))
        detection = libloom_adapter.detect_libraries(
            apk_path=str(apk_path),
            jar_path=str(runtime["jar_path"]),
            libs_profile_dir=str(runtime["libs_profile_dir"]),
            timeout_sec=timeout_sec,
            java_heap_mb=java_heap_mb,
        )
        detected_tpls = sorted(
            {
                canonicalize_tpl(str(lib.get("name")))
                for lib in detection.get("libraries", []) or []
                if isinstance(lib, dict) and lib.get("name")
            }
        )
        if detected_tpls:
            n_apks_with_tpl += 1
            detected_counter.update(detected_tpls)
        scored = score_tpls(detected_tpls, ground_truth)
        total_tp += scored["tp"]
        total_fp += scored["fp"]
        total_fn += scored["fn"]
        per_apk_results.append(
            {
                "apk_path": str(apk_path),
                "apk_name": apk_path.name,
                "ground_truth": sorted(
                    {canonicalize_tpl(name) for name in ground_truth if name}
                ),
                "label_source": label_entry.get("label_source", "unknown"),
                "decoded_dir": label_entry.get("decoded_dir"),
                "detected_tpls": detected_tpls,
                "libloom_status": detection.get("status"),
                "libloom_error_reason": detection.get("error_reason"),
                "libloom_elapsed_sec": detection.get("elapsed_sec", 0.0),
                **scored,
            }
        )

    precision, recall = compute_precision_recall(total_tp, total_fp, total_fn)
    coverage = n_apks_with_tpl / len(apk_paths) if apk_paths else 0.0
    report = {
        "run_id": RUN_ID,
        "status": "ok",
        "reason": None,
        "corpus_size": len(apk_paths),
        "n_apks_with_tpl": n_apks_with_tpl,
        "precision": precision,
        "recall": recall,
        "coverage": coverage,
        "per_apk_results": per_apk_results,
        "top_detected_tpl": _top_detected_tpl(detected_counter),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "warnings": [],
        "source": _source_payload(resolved_corpus_dir, decoded_root, runtime),
    }
    _write_report(report, output_path)
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run real LIBLOOM TPL quality measurement on F-Droid v2 APKs."
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(DEFAULT_CORPUS_DIR),
        help="APK corpus directory (default: F-Droid v2 cache)",
    )
    parser.add_argument(
        "--decoded-root",
        default=None,
        help="decoded corpus root for synthetic labels",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"output report path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=libloom_adapter.DEFAULT_TIMEOUT_SEC,
        help="LIBLOOM timeout per phase",
    )
    parser.add_argument(
        "--java-heap-mb",
        type=int,
        default=libloom_adapter.DEFAULT_JAVA_HEAP_MB,
        help="JVM heap size for LIBLOOM",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="optional APK limit for mini-corpus/debug runs",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        report = run_quality(
            corpus_dir=args.corpus_dir,
            output_path=args.output,
            decoded_root=args.decoded_root,
            timeout_sec=args.timeout_sec,
            java_heap_mb=args.java_heap_mb,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

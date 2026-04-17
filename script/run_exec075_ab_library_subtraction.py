#!/usr/bin/env python3
"""EXEC-075: A/B sweep comparing code_view_v2 TLSH with and without library subtraction.

Usage:
    python3 script/run_exec075_ab_library_subtraction.py \\
        --input <path to fdroid-v2-v2tlsh-results.csv> \\
        --apk-dir <path to F-Droid corpus APK dir> \\
        --output <path to compare CSV>

For each pair in input CSV, computes:
  * full_score  — current TLSH screening score (v2_score from input, kept as-is)
  * app_only_score  — TLSH with library methods subtracted
  * delta  — app_only_score - full_score
At the end prints FPR / Recall / F1 for both modes over a threshold sweep.

Output schema (CSV):
    pair_id,apk1,apk2,label,full_score,app_only_score,delta,status

This script does NOT touch the feature_cache. It recomputes both hashes on-fly
to keep the comparison fair. Expect ~1-3 sec per APK (androguard parse).
Typical runtime for 202 pairs: 10-30 min on a single core.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from code_view_v2 import compare_code_v2, extract_opcode_ngram_tlsh  # noqa: E402

# Silence loguru (used by androguard 4.x) — it floods stderr with XREF DEBUG.
try:
    from loguru import logger as _loguru_logger  # type: ignore[import]
    _loguru_logger.remove()
    _loguru_logger.add(sys.stderr, level="WARNING")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Also mute noisy stdlib loggers from androguard / its deps.
for _mod in ("androguard", "androguard.core", "androguard.analysis"):
    logging.getLogger(_mod).setLevel(logging.WARNING)

logger = logging.getLogger("exec075-ab")


def _compute_hash_pair(
    apk_path: Path,
    cache_full: dict,
    cache_app: dict,
) -> tuple[Optional[str], Optional[str]]:
    """Return (full_hash, app_only_hash), using per-APK cache to avoid recompute."""
    stem = apk_path.name
    if stem not in cache_full:
        cache_full[stem] = extract_opcode_ngram_tlsh(apk_path, app_only=False)
    if stem not in cache_app:
        cache_app[stem] = extract_opcode_ngram_tlsh(apk_path, app_only=True)
    return cache_full[stem], cache_app[stem]


def _resolve_apk(apk_dir: Path, apk_name: str) -> Optional[Path]:
    """Resolve APK by file name under apk_dir."""
    p = apk_dir / apk_name
    if p.exists() and p.is_file():
        return p
    return None


def run(input_csv: Path, apk_dir: Path, output_csv: Path) -> None:
    rows_in: list[dict] = []
    with input_csv.open(newline="", encoding="utf-8") as f:
        rows_in = list(csv.DictReader(f))
    logger.info("Loaded %d pairs from %s", len(rows_in), input_csv)

    cache_full: dict = {}
    cache_app: dict = {}

    rows_out: list[dict] = []
    for idx, row in enumerate(rows_in, 1):
        pair_id = row.get("pair_id", "")
        apk1_name = row.get("apk1", "")
        apk2_name = row.get("apk2", "")
        label = row.get("label", "")

        apk1 = _resolve_apk(apk_dir, apk1_name)
        apk2 = _resolve_apk(apk_dir, apk2_name)
        if apk1 is None or apk2 is None:
            rows_out.append({
                "pair_id": pair_id,
                "apk1": apk1_name,
                "apk2": apk2_name,
                "label": label,
                "full_score": "",
                "app_only_score": "",
                "delta": "",
                "status": "apk_missing",
            })
            logger.warning("[%d/%d] %s: APK missing", idx, len(rows_in), pair_id)
            continue

        h1_full, h1_app = _compute_hash_pair(apk1, cache_full, cache_app)
        h2_full, h2_app = _compute_hash_pair(apk2, cache_full, cache_app)

        full = compare_code_v2(h1_full, h2_full)
        app = compare_code_v2(h1_app, h2_app)
        full_score = full["score"]
        app_score = app["score"]
        delta = round(app_score - full_score, 6) if (
            full["status"] == "tlsh_ok" and app["status"] == "tlsh_ok"
        ) else ""

        status = "both_ok"
        if full["status"] != "tlsh_ok" and app["status"] != "tlsh_ok":
            status = "both_empty"
        elif full["status"] != "tlsh_ok":
            status = "full_empty"
        elif app["status"] != "tlsh_ok":
            status = "app_only_empty"

        rows_out.append({
            "pair_id": pair_id,
            "apk1": apk1_name,
            "apk2": apk2_name,
            "label": label,
            "full_score": full_score,
            "app_only_score": app_score,
            "delta": delta,
            "status": status,
        })
        logger.info(
            "[%d/%d] %s label=%s full=%.3f app=%.3f delta=%s status=%s",
            idx, len(rows_in), pair_id, label, full_score, app_score, delta, status,
        )

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "pair_id", "apk1", "apk2", "label",
            "full_score", "app_only_score", "delta", "status",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    logger.info("Wrote %d rows to %s", len(rows_out), output_csv)

    _summary(rows_out)


def _summary(rows: list[dict]) -> None:
    """Print threshold-sweep F1/FPR/Recall for both modes."""
    valid = [
        r for r in rows
        if r["status"] == "both_ok"
        and r["label"] in ("clone", "non_clone")
    ]
    if not valid:
        logger.warning("No valid rows for summary")
        return

    thresholds = [0.2, 0.25, 0.28, 0.3, 0.35, 0.4, 0.45, 0.5]
    print("\n=== EXEC-075 A/B Summary ===")
    print(f"valid pairs: {len(valid)}")
    print(f"  clones:     {sum(1 for r in valid if r['label'] == 'clone')}")
    print(f"  non_clones: {sum(1 for r in valid if r['label'] == 'non_clone')}")
    print()
    print(f"{'mode':<10} {'t':<6} {'TP':<4} {'FP':<4} {'FN':<4} {'TN':<4} "
          f"{'Recall':<8} {'FPR':<8} {'F1':<8}")
    for mode in ("full", "app_only"):
        score_key = "full_score" if mode == "full" else "app_only_score"
        for t in thresholds:
            tp = fp = fn = tn = 0
            for r in valid:
                try:
                    score = float(r[score_key])
                except (ValueError, TypeError):
                    continue
                is_clone = r["label"] == "clone"
                above = score >= t
                if is_clone and above:
                    tp += 1
                elif is_clone and not above:
                    fn += 1
                elif not is_clone and above:
                    fp += 1
                else:
                    tn += 1
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            fpr = fp / (fp + tn) if (fp + tn) else 0.0
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            print(f"{mode:<10} {t:<6.2f} {tp:<4} {fp:<4} {fn:<4} {tn:<4} "
                  f"{recall:<8.4f} {fpr:<8.4f} {f1:<8.4f}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="EXEC-075 library-subtraction A/B sweep")
    ap.add_argument("--input", required=True, type=Path,
                    help="input CSV (e.g. experiments/artifacts/E-FDROID-004/fdroid-v2-v2tlsh-results.csv)")
    ap.add_argument("--apk-dir", required=True, type=Path,
                    help="directory with APK files referenced by input CSV")
    ap.add_argument("--output", required=True, type=Path,
                    help="output CSV with full_score, app_only_score, delta")
    args = ap.parse_args()

    if not args.input.exists():
        logger.error("Input CSV not found: %s", args.input)
        return 1
    if not args.apk_dir.is_dir():
        logger.error("APK dir not found: %s", args.apk_dir)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(args.input, args.apk_dir, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

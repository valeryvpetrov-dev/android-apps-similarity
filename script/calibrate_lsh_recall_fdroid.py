#!/usr/bin/env python3
"""Calibrate MinHash LSH shortlist recall for SCREENING-25 on F-Droid v2."""

from __future__ import annotations

import argparse
from pathlib import Path

from calibrate_lsh_recall import (
    DEFAULT_BASELINE_BANDS,
    DEFAULT_BASELINE_NUM_PERM,
    DEFAULT_FDROID_V2_CORPUS_DIR,
    DEFAULT_MAX_SHORTLIST_PAIR_RATIO,
    _parse_int_grid,
    build_fdroid_report,
    write_report,
)

DEFAULT_OUT = Path("experiments/artifacts/SCREENING-25-LSH-FDROID/report.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SCREENING-25 LSH recall calibration on the F-Droid v2 APK corpus."
    )
    parser.add_argument(
        "--corpus_dir",
        default=str(DEFAULT_FDROID_V2_CORPUS_DIR),
        help="F-Droid v2 APK corpus directory.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output report.json path.",
    )
    parser.add_argument(
        "--num_perm_grid",
        type=_parse_int_grid,
        default=[64, 128, 256],
        help="Comma-separated num_perm values. Default: 64,128,256.",
    )
    parser.add_argument(
        "--bands_grid",
        type=_parse_int_grid,
        default=[16, 32, 64],
        help="Comma-separated bands values. Default: 16,32,64.",
    )
    parser.add_argument("--thresh", type=float, default=0.28)
    parser.add_argument("--clone_threshold", type=float, default=0.50)
    parser.add_argument("--baseline_num_perm", type=int, default=DEFAULT_BASELINE_NUM_PERM)
    parser.add_argument("--baseline_bands", type=int, default=DEFAULT_BASELINE_BANDS)
    parser.add_argument(
        "--max_shortlist_pair_ratio",
        type=float,
        default=DEFAULT_MAX_SHORTLIST_PAIR_RATIO,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_fdroid_report(
        corpus_dir=args.corpus_dir,
        num_perm_grid=args.num_perm_grid,
        bands_grid=args.bands_grid,
        thresh=args.thresh,
        clone_threshold=args.clone_threshold,
        baseline_num_perm=args.baseline_num_perm,
        baseline_bands=args.baseline_bands,
        max_shortlist_pair_ratio=args.max_shortlist_pair_ratio,
    )
    report_path = write_report(args.out, report)
    print(report_path)


if __name__ == "__main__":
    main()

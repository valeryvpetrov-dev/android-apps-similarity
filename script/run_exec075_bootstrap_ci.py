#!/usr/bin/env python3
"""EXEC-075 post-hoc: bootstrap CI for A/B FPR/Recall/F1 delta.

Takes the A/B compare CSV (from run_exec075_ab_library_subtraction.py) and
computes bootstrap 95% CIs for the difference (app_only − full) across
B resamples over pairs.

Honest closure when N(non_clone) is small: если CI на delta пересекает 0,
вывод "worse" нельзя делать с уверенностью.

Usage:
    python3 script/run_exec075_bootstrap_ci.py \\
        --input <compare csv> \\
        --threshold 0.28 \\
        --iters 2000
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path
from statistics import median


def _load(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return [
            r for r in csv.DictReader(f)
            if r.get("status") == "both_ok" and r.get("label") in ("clone", "non_clone")
        ]


def _metrics(rows: list[dict], score_key: str, t: float) -> dict:
    tp = fp = fn = tn = 0
    for r in rows:
        try:
            s = float(r[score_key])
        except (ValueError, TypeError):
            continue
        is_clone = r["label"] == "clone"
        above = s >= t
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
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": recall, "fpr": fpr, "f1": f1}


def _ci(values: list[float], alpha: float = 0.05) -> tuple[float, float]:
    s = sorted(values)
    n = len(s)
    lo = s[int(alpha / 2 * n)]
    hi = s[int((1 - alpha / 2) * n) - 1]
    return lo, hi


def bootstrap(rows: list[dict], t: float, iters: int = 2000, seed: int = 1) -> dict:
    rng = random.Random(seed)
    n = len(rows)
    deltas_fpr: list[float] = []
    deltas_recall: list[float] = []
    deltas_f1: list[float] = []
    for _ in range(iters):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        m_full = _metrics(sample, "full_score", t)
        m_app = _metrics(sample, "app_only_score", t)
        deltas_fpr.append(m_app["fpr"] - m_full["fpr"])
        deltas_recall.append(m_app["recall"] - m_full["recall"])
        deltas_f1.append(m_app["f1"] - m_full["f1"])
    return {
        "delta_fpr":    {"median": median(deltas_fpr),    "ci95": _ci(deltas_fpr)},
        "delta_recall": {"median": median(deltas_recall), "ci95": _ci(deltas_recall)},
        "delta_f1":     {"median": median(deltas_f1),     "ci95": _ci(deltas_f1)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="EXEC-075 bootstrap CI on A/B delta")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--threshold", type=float, default=0.28)
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rows = _load(args.input)
    clones = sum(1 for r in rows if r["label"] == "clone")
    non_clones = sum(1 for r in rows if r["label"] == "non_clone")
    print(f"Loaded {len(rows)} valid pairs: {clones} clone / {non_clones} non_clone")

    point_full = _metrics(rows, "full_score", args.threshold)
    point_app = _metrics(rows, "app_only_score", args.threshold)
    print(f"\n=== Point estimate at t={args.threshold} ===")
    print(f"full:     FPR={point_full['fpr']:.4f}  Recall={point_full['recall']:.4f}  F1={point_full['f1']:.4f}")
    print(f"app_only: FPR={point_app['fpr']:.4f}  Recall={point_app['recall']:.4f}  F1={point_app['f1']:.4f}")

    print(f"\n=== Bootstrap 95% CI on delta (app_only − full), N={len(rows)}, iters={args.iters} ===")
    boot = bootstrap(rows, args.threshold, iters=args.iters, seed=args.seed)
    for metric in ("delta_fpr", "delta_recall", "delta_f1"):
        d = boot[metric]
        lo, hi = d["ci95"]
        crosses_zero = lo <= 0 <= hi
        flag = "  [CI crosses 0 → not statistically significant]" if crosses_zero else ""
        print(f"{metric:14s}: median={d['median']:+.4f}  CI95=[{lo:+.4f}, {hi:+.4f}]{flag}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

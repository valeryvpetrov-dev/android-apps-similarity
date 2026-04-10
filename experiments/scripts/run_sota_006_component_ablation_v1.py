#!/usr/bin/env python3
"""SOTA-006: Ablation study of R_component weight configurations.

Tests 4 weight configurations against labeled APK pairs to determine
the optimal weight vector for the component Jaccard aggregate score.

Usage (from submodule root):
    .venv/bin/python3 experiments/scripts/run_sota_006_component_ablation_v1.py

Output:
    experiments/artifacts/E-SOTA-006/component-ablation-v1-results.csv
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup: add submodule root to sys.path so `script` package is importable
# ---------------------------------------------------------------------------
SUBMODULE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SUBMODULE_ROOT))

# Suppress androguard debug noise
logging.getLogger("androguard").setLevel(logging.ERROR)
logging.disable(logging.WARNING)

from androguard.core.apk import APK  # noqa: E402

# ---------------------------------------------------------------------------
# Weight configurations to test
# ---------------------------------------------------------------------------
WEIGHT_CONFIGS: dict[str, dict[str, float]] = {
    "current": {
        "activities": 0.4,
        "services": 0.2,
        "receivers": 0.2,
        "providers": 0.1,
        "permissions": 0.1,
    },
    "equal": {
        "activities": 0.2,
        "services": 0.2,
        "receivers": 0.2,
        "providers": 0.2,
        "permissions": 0.2,
    },
    "activity_heavy": {
        "activities": 0.6,
        "services": 0.1,
        "receivers": 0.1,
        "providers": 0.1,
        "permissions": 0.1,
    },
    "permission_heavy": {
        "activities": 0.2,
        "services": 0.2,
        "receivers": 0.1,
        "providers": 0.1,
        "permissions": 0.4,
    },
}

# ---------------------------------------------------------------------------
# APK pair definitions (labels: 1=clone, 0=non-clone)
# ---------------------------------------------------------------------------
APK_BASE = SUBMODULE_ROOT / "apk"

LABELED_PAIRS: list[dict[str, Any]] = [
    {
        "pair_id": "nonopt_vs_opt",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseNonOptimized.apk",
        "apk_b": APK_BASE / "simple_app" / "simple_app-releaseOptimized.apk",
        "label": 1,
        "label_str": "clone",
    },
    {
        "pair_id": "nonopt_vs_rename",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseNonOptimized.apk",
        "apk_b": APK_BASE / "simple_app" / "simple_app-releaseRename.apk",
        "label": 1,
        "label_str": "clone",
    },
    {
        "pair_id": "opt_vs_rename",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseOptimized.apk",
        "apk_b": APK_BASE / "simple_app" / "simple_app-releaseRename.apk",
        "label": 1,
        "label_str": "clone",
    },
    {
        "pair_id": "nonopt_vs_snake",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseNonOptimized.apk",
        "apk_b": APK_BASE / "snake" / "snake.apk",
        "label": 0,
        "label_str": "non_clone",
    },
    {
        "pair_id": "rename_vs_snake",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseRename.apk",
        "apk_b": APK_BASE / "snake" / "snake.apk",
        "label": 0,
        "label_str": "non_clone",
    },
    {
        "pair_id": "nonopt_vs_empty",
        "apk_a": APK_BASE / "simple_app" / "simple_app-releaseNonOptimized.apk",
        "apk_b": APK_BASE / "simple_app" / "simple_app-empty.apk",
        "label": 0,
        "label_str": "non_clone",
    },
]


# ---------------------------------------------------------------------------
# Feature extraction via androguard (no apktool required)
# ---------------------------------------------------------------------------

def extract_features_from_apk(apk_path: Path) -> dict:
    """Extract component features directly from APK using androguard."""
    apk = APK(str(apk_path))
    return {
        "package": apk.get_package(),
        "activities": set(apk.get_activities()),
        "services": set(apk.get_services()),
        "receivers": set(apk.get_receivers()),
        "providers": set(apk.get_providers()),
        "permissions": set(apk.get_permissions()),
    }


# ---------------------------------------------------------------------------
# Weighted Jaccard comparison (wrapper — component_view.compare_components
# uses global WEIGHTS, so we reimplement the aggregate here)
# ---------------------------------------------------------------------------

def _jaccard(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def compare_weighted(features_a: dict, features_b: dict, weights: dict[str, float]) -> dict[str, float]:
    """Compute per-type Jaccard scores and weighted aggregate."""
    keys = ("activities", "services", "receivers", "providers", "permissions")
    per_type: dict[str, float] = {}
    for k in keys:
        set_a = set(features_a.get(k, set()))
        set_b = set(features_b.get(k, set()))
        per_type[k] = _jaccard(set_a, set_b)

    aggregate = sum(weights[k] * per_type[k] for k in weights)
    return {"score": aggregate, **{f"j_{k}": per_type[k] for k in keys}}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(
    scores: list[float],
    labels: list[int],
    threshold: float = 0.5,
) -> dict[str, float]:
    """Compute F1, precision, recall, margin at given threshold."""
    tp = fp = fn = tn = 0
    for score, label in zip(scores, labels):
        pred = 1 if score >= threshold else 0
        if pred == 1 and label == 1:
            tp += 1
        elif pred == 1 and label == 0:
            fp += 1
        elif pred == 0 and label == 1:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    clone_scores = [s for s, l in zip(scores, labels) if l == 1]
    non_clone_scores = [s for s, l in zip(scores, labels) if l == 0]
    margin = (sum(clone_scores) / len(clone_scores) if clone_scores else 0.0) - \
             (sum(non_clone_scores) / len(non_clone_scores) if non_clone_scores else 0.0)

    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "margin": round(margin, 4),
        "mean_clone_score": round(sum(clone_scores) / len(clone_scores), 4) if clone_scores else 0.0,
        "mean_non_clone_score": round(sum(non_clone_scores) / len(non_clone_scores), 4) if non_clone_scores else 0.0,
    }


def find_optimal_threshold(scores: list[float], labels: list[int]) -> float:
    """Grid search for threshold maximizing F1."""
    best_f1 = -1.0
    best_threshold = 0.5
    candidates = sorted(set(scores))
    # Add midpoints between consecutive scores
    thresholds = [0.0]
    for i in range(len(candidates) - 1):
        thresholds.append((candidates[i] + candidates[i + 1]) / 2)
    thresholds.extend(candidates)
    thresholds.append(1.0)

    for t in thresholds:
        m = compute_metrics(scores, labels, threshold=t)
        if m["f1"] > best_f1:
            best_f1 = m["f1"]
            best_threshold = t
    return best_threshold


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    artifact_dir = SUBMODULE_ROOT / "experiments" / "artifacts" / "E-SOTA-006"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    csv_path = artifact_dir / "component-ablation-v1-results.csv"

    # 1. Extract features for all unique APKs
    print("Extracting features from APKs...")
    apk_features: dict[Path, dict] = {}
    unique_apks: set[Path] = set()
    for pair in LABELED_PAIRS:
        unique_apks.add(pair["apk_a"])
        unique_apks.add(pair["apk_b"])

    for apk_path in sorted(unique_apks):
        print(f"  {apk_path.name}")
        apk_features[apk_path] = extract_features_from_apk(apk_path)
        pkg = apk_features[apk_path]["package"]
        acts = len(apk_features[apk_path]["activities"])
        svcs = len(apk_features[apk_path]["services"])
        rcvs = len(apk_features[apk_path]["receivers"])
        prvs = len(apk_features[apk_path]["providers"])
        perms = len(apk_features[apk_path]["permissions"])
        print(f"    pkg={pkg} acts={acts} svcs={svcs} rcvs={rcvs} prvs={prvs} perms={perms}")

    # 2. Compute scores for all pairs × all configs
    print("\nComputing scores...")
    rows: list[dict] = []
    config_scores: dict[str, list[float]] = {cfg: [] for cfg in WEIGHT_CONFIGS}
    labels_list: list[int] = [pair["label"] for pair in LABELED_PAIRS]

    for pair in LABELED_PAIRS:
        fa = apk_features[pair["apk_a"]]
        fb = apk_features[pair["apk_b"]]
        row: dict[str, Any] = {
            "pair_id": pair["pair_id"],
            "apk_a": pair["apk_a"].name,
            "apk_b": pair["apk_b"].name,
            "label": pair["label"],
            "label_str": pair["label_str"],
        }
        for cfg_name, weights in WEIGHT_CONFIGS.items():
            result = compare_weighted(fa, fb, weights)
            score = result["score"]
            row[f"score_{cfg_name}"] = round(score, 4)
            for k in ("activities", "services", "receivers", "providers", "permissions"):
                row[f"j_{k}"] = round(result[f"j_{k}"], 4)
            config_scores[cfg_name].append(score)
        rows.append(row)

    # 3. Print per-pair scores
    print("\n=== Per-pair scores ===")
    header_cols = ["pair_id", "label_str"] + [f"score_{c}" for c in WEIGHT_CONFIGS]
    print("  ".join(f"{c:20s}" for c in header_cols))
    for row in rows:
        vals = [row["pair_id"], row["label_str"]] + [str(row[f"score_{c}"]) for c in WEIGHT_CONFIGS]
        print("  ".join(f"{v:20s}" for v in vals))

    # 4. Compute metrics per config (threshold=0.5 and optimal)
    print("\n=== Metrics per config ===")
    print(f"{'Config':20s} {'Threshold':>10s} {'F1':>8s} {'Precision':>10s} {'Recall':>8s} {'Margin':>8s} {'MeanClone':>10s} {'MeanNonClone':>12s}")
    summary_rows: list[dict] = []
    for cfg_name in WEIGHT_CONFIGS:
        scores = config_scores[cfg_name]
        # At threshold=0.5
        m_05 = compute_metrics(scores, labels_list, threshold=0.5)
        # Optimal threshold
        opt_t = find_optimal_threshold(scores, labels_list)
        m_opt = compute_metrics(scores, labels_list, threshold=opt_t)

        print(f"{cfg_name:20s} {'0.50':>10s} {m_05['f1']:>8.4f} {m_05['precision']:>10.4f} {m_05['recall']:>8.4f} {m_05['margin']:>8.4f} {m_05['mean_clone_score']:>10.4f} {m_05['mean_non_clone_score']:>12.4f}")
        print(f"{'  (optimal)':20s} {opt_t:>10.4f} {m_opt['f1']:>8.4f} {m_opt['precision']:>10.4f} {m_opt['recall']:>8.4f} {m_opt['margin']:>8.4f}")

        summary_rows.append({
            "config": cfg_name,
            "threshold_fixed": 0.5,
            "f1_t050": m_05["f1"],
            "precision_t050": m_05["precision"],
            "recall_t050": m_05["recall"],
            "margin": m_05["margin"],
            "mean_clone_score": m_05["mean_clone_score"],
            "mean_non_clone_score": m_05["mean_non_clone_score"],
            "optimal_threshold": round(opt_t, 4),
            "f1_optimal": m_opt["f1"],
            "precision_optimal": m_opt["precision"],
            "recall_optimal": m_opt["recall"],
            **{f"w_{k}": WEIGHT_CONFIGS[cfg_name][k] for k in WEIGHT_CONFIGS[cfg_name]},
        })

    # 5. Save CSV (per-pair results)
    pair_csv = artifact_dir / "component-ablation-v1-results.csv"
    with open(pair_csv, "w", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(f"\nPer-pair CSV saved: {pair_csv}")

    # 6. Save summary CSV
    summary_csv = artifact_dir / "component-ablation-v1-summary.csv"
    with open(summary_csv, "w", newline="") as f:
        if summary_rows:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
    print(f"Summary CSV saved: {summary_csv}")

    # 7. Print Jaccard per-type for first pair (diagnostic)
    print("\n=== Per-type Jaccard (pair: nonopt_vs_opt) ===")
    pair = next(p for p in LABELED_PAIRS if p["pair_id"] == "nonopt_vs_opt")
    fa = apk_features[pair["apk_a"]]
    fb = apk_features[pair["apk_b"]]
    for k in ("activities", "services", "receivers", "providers", "permissions"):
        j = _jaccard(set(fa.get(k, set())), set(fb.get(k, set())))
        print(f"  {k:15s}: {j:.4f}")

    return summary_rows


if __name__ == "__main__":
    main()

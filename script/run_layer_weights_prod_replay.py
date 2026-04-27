"""DEEP-28-LAYER-WEIGHTS-PROD-APPLY: sanity replay on HINT-27 30-pair corpus.

Берёт 30 пар из EXEC-HINT-27-CHANNEL-COVERAGE/channel_dataset.json (где для
каждой пары уже посчитаны per-layer scores через `m_static_views.compare_all`)
и для каждой пары пересчитывает `full_similarity_score` дважды:

- со старыми весами DEEP-19 (`_LAYER_WEIGHTS_FALLBACK` из `m_static_views`);
- с новыми весами DEEP-27 (`LAYER_WEIGHTS`, читаются из canonical
  `experiments/artifacts/DEEP-22-LAYER-WEIGHTS-EXTERNALIZED/calibrated_weights.json`,
  куда DEEP-27 скопировал содержимое калибровки на F-Droid v2).

Результат — JSON-отчёт с per-pair дельтами и агрегированной статистикой
(mean / median / std / per-ground_truth разрезы), сохраняется в
`experiments/artifacts/DEEP-28-WEIGHTS-PROD-REPLAY/report.json`.

Это sanity-проверка: убедиться, что переход на новые веса даёт ожидаемый
эффект — clone-пары с высоким component/library остаются высокими, а
non-clone-пары, у которых только code совпадает (синтетический случай
вне корпуса, но реальный риск AndroZoo-обфускации), ослабевают.

Запуск:
    SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_layer_weights_prod_replay.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

# Делаем `script.*` импортируемым для прямого запуска CLI.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from script.m_static_views import (  # noqa: E402  (sys.path setup above)
    LAYER_WEIGHTS,
    _LAYER_WEIGHTS_FALLBACK,
)


def _full_score(per_layer_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Re-implement compare_all aggregation: sum_l(w_l * s_l) / sum_l(w_l).

    metadata-слой исключён из weighted-average по архитектурному решению
    (он tiebreaker), поэтому в `weights` его веса нет (LAYER_WEIGHTS его
    не содержит, _LAYER_WEIGHTS_FALLBACK тоже).
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for layer, score in per_layer_scores.items():
        weight = weights.get(layer)
        if weight is None or weight <= 0.0:
            continue
        weighted_sum += weight * score
        weight_total += weight
    return weighted_sum / weight_total if weight_total > 0.0 else 0.0


def _aggregate(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def replay(dataset_path: Path) -> dict[str, Any]:
    """Run sanity replay on HINT-27 30-pair dataset.

    Returns aggregated report dict ready for JSON dump.
    """
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    pairs = payload.get("per_pair") or []
    if not pairs:
        raise ValueError(f"no pairs in {dataset_path}")

    per_pair_records: list[dict[str, Any]] = []
    by_gt: dict[str, dict[str, list[float]]] = {}

    for pair in pairs:
        cmp_result = pair.get("full_metadata", {}).get("compare_result", {})
        per_layer = cmp_result.get("per_layer") or {}
        # Извлекаем per-layer score'ы (исключая metadata, как и compare_all).
        layer_scores: dict[str, float] = {}
        for layer, layer_data in per_layer.items():
            if layer == "metadata":
                continue
            score = layer_data.get("score")
            status = layer_data.get("status")
            if status == "both_empty":
                # _include_layer_in_weighted_score → layer выпадает.
                continue
            if score is None:
                continue
            layer_scores[layer] = float(score)

        old_score = _full_score(layer_scores, _LAYER_WEIGHTS_FALLBACK)
        new_score = _full_score(layer_scores, LAYER_WEIGHTS)
        delta = new_score - old_score

        gt = pair.get("ground_truth", "unknown")
        per_pair_records.append(
            {
                "pair_id": pair.get("pair_id"),
                "ground_truth": gt,
                "per_layer_scores": layer_scores,
                "full_similarity_score_old_deep19": old_score,
                "full_similarity_score_new_deep27": new_score,
                "delta_new_minus_old": delta,
                "stored_full_similarity_score": cmp_result.get("full_similarity_score"),
            }
        )
        bucket = by_gt.setdefault(
            gt, {"old": [], "new": [], "delta": [], "abs_delta": []}
        )
        bucket["old"].append(old_score)
        bucket["new"].append(new_score)
        bucket["delta"].append(delta)
        bucket["abs_delta"].append(abs(delta))

    overall_old = [r["full_similarity_score_old_deep19"] for r in per_pair_records]
    overall_new = [r["full_similarity_score_new_deep27"] for r in per_pair_records]
    overall_delta = [r["delta_new_minus_old"] for r in per_pair_records]

    # Доля пар, где |delta| >= 0.10 — насколько перевес заметен.
    n_total = len(per_pair_records)
    n_significant = sum(1 for d in overall_delta if abs(d) >= 0.10)

    report = {
        "artifact_id": "DEEP-28-WEIGHTS-PROD-REPLAY",
        "task_id": "DEEP-28-LAYER-WEIGHTS-PROD-APPLY",
        "source_dataset": str(dataset_path),
        "n_pairs": n_total,
        "old_weights_source": "DEEP-19 (_LAYER_WEIGHTS_FALLBACK in m_static_views)",
        "new_weights_source": "DEEP-27 (LAYER_WEIGHTS via CALIBRATED_WEIGHTS_PATH)",
        "old_weights": dict(_LAYER_WEIGHTS_FALLBACK),
        "new_weights": dict(LAYER_WEIGHTS),
        "aggregate": {
            "old_full_similarity_score": _aggregate(overall_old),
            "new_full_similarity_score": _aggregate(overall_new),
            "delta_new_minus_old": _aggregate(overall_delta),
            "n_pairs_with_abs_delta_ge_0_10": n_significant,
            "share_with_abs_delta_ge_0_10": (
                n_significant / n_total if n_total else 0.0
            ),
        },
        "per_ground_truth": {
            gt: {
                "old": _aggregate(b["old"]),
                "new": _aggregate(b["new"]),
                "delta": _aggregate(b["delta"]),
                "abs_delta": _aggregate(b["abs_delta"]),
            }
            for gt, b in sorted(by_gt.items())
        },
        "per_pair": per_pair_records,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_REPO_ROOT
        / "experiments"
        / "artifacts"
        / "EXEC-HINT-27-CHANNEL-COVERAGE"
        / "channel_dataset.json",
        help="Path to HINT-27 channel_dataset.json with per_pair compare_result.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT
        / "experiments"
        / "artifacts"
        / "DEEP-28-WEIGHTS-PROD-REPLAY"
        / "report.json",
        help="Where to write the replay report JSON.",
    )
    args = parser.parse_args()

    report = replay(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    agg = report["aggregate"]
    print(f"DEEP-28 replay: {report['n_pairs']} pairs")
    print(
        f"  old DEEP-19 mean = {agg['old_full_similarity_score']['mean']:.4f}, "
        f"new DEEP-27 mean = {agg['new_full_similarity_score']['mean']:.4f}, "
        f"delta mean = {agg['delta_new_minus_old']['mean']:+.4f}"
    )
    print(
        f"  n pairs with |delta| >= 0.10: "
        f"{agg['n_pairs_with_abs_delta_ge_0_10']}/{report['n_pairs']} "
        f"({100 * agg['share_with_abs_delta_ge_0_10']:.1f}%)"
    )
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

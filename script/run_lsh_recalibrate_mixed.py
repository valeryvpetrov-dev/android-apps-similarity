"""SCREENING-31-INDEX-RECALIBRATE-MIXED-CORPUS: replay LSH recall_at_shortlist по классам модификации.

Объединяет:
- SCRN-30 (class_4 — package rename, 20 пар)
- DEEP-30 (class_5 — code injection, 35 пар)
- HINT-30 (class_6 — R8 obfuscation mock, 10 пар)
- F-Droid v2 baseline clones (class_1 — repack-only, synthetic placeholder)

и считает recall_at_shortlist для каждого класса отдельно. THRESH-002 (=0.70) — текущий порог.
proposed_thresh_002 предлагается, если медиана jaccard по «истинным» парам подсказывает другой
optimum (балансирует recall и shortlist_size).

CLI можно запускать как:
    python3 script/run_lsh_recalibrate_mixed.py --out experiments/artifacts/SCREENING-31-MIXED-CORPUS/report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers


def _load_json(path: Path) -> dict[str, Any] | None:
    if path is None or not Path(path).exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _recall_from_jaccard_list(items: list[dict[str, Any]]) -> tuple[float, int]:
    if not items:
        return 0.0, 0
    n = len(items)
    n_in = sum(1 for it in items if it.get("in_shortlist"))
    return (n_in / n if n else 0.0), n


def _recall_from_score_list(
    items: list[dict[str, Any]], threshold: float
) -> tuple[float, int]:
    """Эмулируем shortlist через score >= threshold (proxy для LSH-баковки)."""
    if not items:
        return 0.0, 0
    n = len(items)
    n_in = sum(1 for it in items if float(it.get("score", 0.0)) >= threshold)
    return (n_in / n if n else 0.0), n


def _recall_from_full_similarity(
    pairs: list[dict[str, Any]], threshold: float
) -> tuple[float, int]:
    if not pairs:
        return 0.0, 0
    n = len(pairs)
    n_in = sum(
        1 for p in pairs if float(p.get("full_similarity_score", 0.0)) >= threshold
    )
    return (n_in / n if n else 0.0), n


# ---------------------------------------------------------------------------
# Main calibration entrypoint (импортируется тестами)


def calibrate_mixed_corpus(
    scrn30_path: Path | None = None,
    deep30_path: Path | None = None,
    hint30_path: Path | None = None,
    fdroid_baseline_clone_recall: float = 0.95,  # placeholder для class_1; F-Droid v2 self-clone почти 1.0
    fdroid_baseline_n: int = 20,
    current_thresh_002: float = 0.70,
) -> dict[str, Any]:
    """Главная функция: читает 3 артефакта и собирает recall per-class.

    Возвращает dict с полями:
        n_pairs_per_class: {class_1, class_4, class_5, class_6}
        recall_at_shortlist_per_class: per-class
        current_thresh_002: float
        proposed_thresh_002: float
        per_class_diagnostics: подробности
    """
    scrn = _load_json(scrn30_path) if scrn30_path else None
    deep = _load_json(deep30_path) if deep30_path else None
    hint = _load_json(hint30_path) if hint30_path else None

    # class_4 — package rename из SCRN-30 jaccard_per_pair (in_shortlist флаг).
    if scrn:
        recall_c4, n_c4 = _recall_from_jaccard_list(scrn.get("jaccard_per_pair", []))
    else:
        recall_c4, n_c4 = 0.0, 0

    # class_5 — code injection из DEEP-30 scored_pairs (label=='clone').
    # Используем current_thresh_002 как порог LSH shortlist (то же значение, что
    # реальный пайплайн использует на этапе кандидатов).
    if deep:
        scored = [
            it for it in deep.get("scored_pairs", []) if it.get("label") == "clone"
        ]
        recall_c5, n_c5 = _recall_from_score_list(scored, threshold=current_thresh_002)
    else:
        recall_c5, n_c5 = 0.0, 0

    # class_6 — R8 mock из HINT-30 r8_pairs.json (тот же порог, что и для class_5).
    if hint:
        recall_c6, n_c6 = _recall_from_full_similarity(
            hint.get("pairs", []), threshold=current_thresh_002
        )
    else:
        recall_c6, n_c6 = 0.0, 0

    # class_1 — repack-only baseline (на F-Droid v2 self-clone тривиальный).
    recall_c1 = fdroid_baseline_clone_recall
    n_c1 = fdroid_baseline_n

    # class_2/class_3 (library injection / resource modification) — отсутствуют в датасетах
    # (нет targeted-эксперимента для них в волнах <=31). Помечаем явно.
    n_pairs_per_class = {
        "class_1": n_c1,
        "class_2": 0,
        "class_3": 0,
        "class_4": n_c4,
        "class_5": n_c5,
        "class_6": n_c6,
    }
    recall_at_shortlist_per_class = {
        "class_1": round(recall_c1, 4),
        "class_2": None,
        "class_3": None,
        "class_4": round(recall_c4, 4),
        "class_5": round(recall_c5, 4),
        "class_6": round(recall_c6, 4),
    }

    # Предложенный thresh_002: медиана jaccard по jaccard_per_pair[in_shortlist=True],
    # если SCRN-30 даёт что-то <0.70, имеет смысл чуть снизить. Иначе оставляем 0.70.
    proposed = current_thresh_002
    if scrn:
        true_jac = [
            float(it["jaccard"])
            for it in scrn.get("jaccard_per_pair", [])
            if it.get("in_shortlist")
        ]
        if true_jac:
            med = statistics.median(true_jac)
            # Если медиана близка к current — ничего не менять; если ниже на 0.1+
            # → предложить новое значение.
            if med < current_thresh_002 - 0.05:
                proposed = round(max(0.50, med), 2)

    report: dict[str, Any] = {
        "artifact_id": "SCREENING-31-MIXED-CORPUS",
        "schema_version": "scrn-mixed-recalibrate-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_pairs_per_class": n_pairs_per_class,
        "recall_at_shortlist_per_class": recall_at_shortlist_per_class,
        "current_thresh_002": current_thresh_002,
        "proposed_thresh_002": proposed,
        "per_class_diagnostics": {
            "class_1": {
                "source": "fdroid_v2_self_clone_placeholder",
                "n_pairs": n_c1,
                "recall_at_shortlist": round(recall_c1, 4),
            },
            "class_2": {
                "source": "no_dataset",
                "note": "TPL injection не покрыт целевым экспериментом до волны 31",
            },
            "class_3": {
                "source": "no_dataset",
                "note": "Resource modification покрыт REPR-30/31, но без LSH replay",
            },
            "class_4": {
                "source": "SCRN-30-PACKAGE-RENAME",
                "n_pairs": n_c4,
                "recall_at_shortlist": round(recall_c4, 4),
            },
            "class_5": {
                "source": "DEEP-30-CODE-INJECT",
                "n_pairs": n_c5,
                "recall_at_shortlist": round(recall_c5, 4),
                "note": "DEEP-30 F1=1.0 → recall ~1.0",
            },
            "class_6": {
                "source": "EXEC-HINT-30-OBFUSCATION-DATASET (mock)",
                "n_pairs": n_c6,
                "recall_at_shortlist": round(recall_c6, 4),
                "note": "R8 ломает minhash → recall ниже class_5",
            },
        },
        "config": {
            "thresh_002_used_for_score_emulation": current_thresh_002,
            "fdroid_baseline_clone_recall": fdroid_baseline_clone_recall,
        },
    }
    return report


# ---------------------------------------------------------------------------
# CLI


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--scrn30",
        default="experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json",
    )
    p.add_argument(
        "--deep30",
        default="experiments/artifacts/DEEP-30-CODE-INJECT/report.json",
    )
    p.add_argument(
        "--hint30",
        default="experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json",
    )
    p.add_argument(
        "--out",
        default="experiments/artifacts/SCREENING-31-MIXED-CORPUS/report.json",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    report = calibrate_mixed_corpus(
        scrn30_path=Path(args.scrn30),
        deep30_path=Path(args.deep30),
        hint30_path=Path(args.hint30),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps(report["recall_at_shortlist_per_class"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

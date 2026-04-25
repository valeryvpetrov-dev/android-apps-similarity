#!/usr/bin/env python3
"""DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL: synthetic demo of run_shortcut_control.

Реальных shortcut-пар в артефактах волн 16–19 не было найдено
(``find experiments/artifacts -name '*.json' | xargs grep -l shortcut_applied``
вернул пусто на момент 2026-04-25), поэтому замер делаем на синтетике
из 50 shortcut-пар с управляемым распределением библиотек:
  - 10 пар (20%) — multi-app developer (один ключ, разные приложения,
    мало общих библиотек): library_reduced_score=0.30 < threshold=0.5;
  - 30 пар (60%) — настоящие клоны (одинаковые библиотеки, репакеджинг):
    library_reduced_score=0.85;
  - 10 пар (20%) — пограничные (частичный рефакторинг кода): score=0.55.

Цель — продемонстрировать сценарий, описанный критиком в разделе 1.3:
fpr должен показать ~20% (10 из 50 — multi-app developer false positives).

Output: report.json в той же директории.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "script"
sys.path.insert(0, str(SCRIPT_DIR))

from shortcut_control import run_shortcut_control  # noqa: E402


def _make_shortcut_pair(app_a: str, app_b: str, kind: str) -> dict:
    return {
        "app_a": app_a,
        "app_b": app_b,
        "shortcut_applied": True,
        "shortcut_reason": "high_confidence_signature_match",
        "signature_match": {"status": "match", "score": 1.0},
        "full_similarity_score": None,
        "library_reduced_score": None,
        "verdict": "likely_clone_by_signature",
        "_synthetic_kind": kind,
    }


def build_synthetic_corpus() -> list[dict]:
    """50 shortcut-пар: 10 multi-app dev (FP), 30 настоящих клонов, 10 пограничных."""
    pairs: list[dict] = []
    for i in range(10):
        pairs.append(_make_shortcut_pair(
            f"vk_app_{i}", f"vk_app_{i+1}", kind="multi_app_developer_fp",
        ))
    for i in range(30):
        pairs.append(_make_shortcut_pair(
            f"clone_a_{i}", f"clone_b_{i}", kind="real_clone",
        ))
    for i in range(10):
        pairs.append(_make_shortcut_pair(
            f"borderline_a_{i}", f"borderline_b_{i}", kind="borderline_partial",
        ))
    return pairs


def synthetic_scorer(pair: dict) -> float:
    """Возвращает library_reduced_score по типу пары (детерминирован)."""
    kind = pair.get("_synthetic_kind")
    if kind == "multi_app_developer_fp":
        return 0.30  # ниже threshold=0.5 → false_positive
    if kind == "real_clone":
        return 0.85
    if kind == "borderline_partial":
        return 0.55
    return 0.50


def main() -> dict:
    corpus = build_synthetic_corpus()
    report = run_shortcut_control(
        pairs=corpus,
        control_ratio=0.1,  # 10% от 50 = 5 пар
        threshold=0.5,
        scorer=synthetic_scorer,
        rng_seed=42,
    )

    # Также прогон на 100% выборке для теоретического FPR на полном корпусе.
    full_report = run_shortcut_control(
        pairs=corpus,
        control_ratio=1.0,
        threshold=0.5,
        scorer=synthetic_scorer,
        rng_seed=42,
    )

    out_dir = Path(__file__).resolve().parent
    payload = {
        "task_id": "DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL",
        "wave": 21,
        "team": "E",
        "context": (
            "Контрольный пересчёт library_reduced_score на shortcut-парах. "
            "Реальных shortcut-данных в артефактах волн 16–19 не было найдено, "
            "поэтому замер на синтетике из 50 пар (10 multi-app developer FP, "
            "30 настоящих клонов, 10 пограничных)."
        ),
        "synthetic_corpus": {
            "total_pairs": len(corpus),
            "kinds": {
                "multi_app_developer_fp": 10,
                "real_clone": 30,
                "borderline_partial": 10,
            },
            "scorer_rules": {
                "multi_app_developer_fp": 0.30,
                "real_clone": 0.85,
                "borderline_partial": 0.55,
            },
        },
        "control_ratio_10_percent": report,
        "control_ratio_100_percent": full_report,
        "interpretation": {
            "expected_fpr_on_full_corpus": 10 / 50,
            "actual_fpr_on_full_corpus": full_report["false_positive_rate"],
            "comment": (
                "На синтетике с 20% multi-app developer пар FPR=0.2 при threshold=0.5. "
                "На реальном корпусе F-Droid v2 (202 пары DEEP-003-SHORTLIST) ожидаемый "
                "FPR неизвестен — нужен прогон с реальным scorer'ом из pairwise_runner."
            ),
        },
    }
    out_path = out_dir / "report.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()

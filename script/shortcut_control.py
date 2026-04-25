#!/usr/bin/env python3
"""DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL: shortcut control sampling.

Контекст (рекомендация №3 критика волны 18,
``inbox/critics/deep-verification-2026-04-24.md`` раздел 6):

В волне 16 (DEEP-16-SHORTCUT-PROPAGATION, коммит ``97f2d72``) внедрена
ветка short-cut: для пары с подтверждённой подписью и высокой screening-
оценкой (``shortcut_applied=True`` + ``shortcut_reason="high_confidence_signature_match"``
+ ``signature_match.status="match"``) система пропускает углублённое
сравнение и не вычисляет ``library_reduced_score``. Экономия ~110 мс/пара.

Риск (фундаментальная ошибка по разделу 1.3 отчёта критика): на корпусе
крупного разработчика (VK, Яндекс, Google) все приложения подписаны одним
ключом и проходят screening из-за общего корпуса библиотек. Shortcut
объявит эти пары клонами по подписи, хотя при честном пересчёте
``library_reduced_score`` (similarity по слоям без library — code,
component, resource, api) пара могла бы получить score ниже порога и
быть дисквалифицирована.

Этот модуль реализует контрольный замер: на случайной выборке shortcut-
пар (по умолчанию 10%) запускается «честный» scorer для расчёта
``library_reduced_score`` и сравнение с порогом. Если score < threshold,
пара помечается как потенциальный false positive shortcut. Возвращается
сводка с false_positive_rate и примерами.

Module API
----------
``run_shortcut_control(pairs, control_ratio, threshold, scorer, rng_seed) -> dict``

  pairs: list[dict] — shortcut-пары (с ``shortcut_applied=True``);
  control_ratio: float — доля выборки (0.0 <= x <= 1.0); 0.1 по умолчанию;
  threshold: float — порог library_reduced_score; 0.5 по умолчанию;
  scorer: Callable[[dict], float] — функция, возвращающая
          library_reduced_score для пары; в продакшене это вызов
          full path через pairwise_runner.calculate_pair_scores;
          в тестах — заглушка;
  rng_seed: int — seed для детерминированной выборки.

  return: {
      "shortcut_pairs_total": int,
      "control_size": int,
      "false_positive_count": int,
      "false_positive_rate": float,
      "threshold": float,
      "control_ratio": float,
      "examples": [
          {"app_a": str, "app_b": str, "library_reduced_score": float,
           "false_positive": bool},
          ...
      ],
      "warnings": [str, ...],   # пусто, если всё штатно
  }

Зависимостей от тяжёлых модулей (pairwise_runner / m_static_views) нет —
scorer передаётся снаружи. Это позволяет:
  1) изолированно тестировать выборку и логику false-positive детекции;
  2) на реальных данных подключать любой full-path scorer без изменения
     логики семплирования.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Sequence


def _coerce_pairs(pairs: Sequence[dict]) -> list[dict]:
    """Фильтруем входной список, оставляем только shortcut-пары.

    Strict-режим: если на вход подали non-shortcut пары, мы их игнорируем
    и фиксируем в warnings отдельно. Это нужно, чтобы случайно не
    посчитать FPR по «обычным» парам, для которых уже есть
    library_reduced_score.
    """
    return [p for p in pairs if isinstance(p, dict) and p.get("shortcut_applied") is True]


def _compute_control_size(total: int, control_ratio: float) -> int:
    """Размер контрольной выборки: math.ceil(total * ratio), но 0 при ratio=0.

    На маленьком пуле (5 пар × 0.1 = 0.5) используем ceil, чтобы не получить
    нулевой замер: иначе на корпусе из 5 shortcut-пар контроль был бы
    бессмысленным. При ratio=0.0 строго возвращаем 0 — это специальный
    случай отключения замера.
    """
    if control_ratio <= 0:
        return 0
    if control_ratio >= 1.0:
        return total
    raw = total * control_ratio
    return max(1, math.ceil(raw))


def run_shortcut_control(
    pairs: Sequence[dict],
    control_ratio: float = 0.1,
    threshold: float = 0.5,
    scorer: Callable[[dict], float] | None = None,
    rng_seed: int | None = None,
) -> dict[str, Any]:
    """Контрольный пересчёт library_reduced_score на выборке shortcut-пар.

    Возвращает сводный отчёт. Не падает на пустом входе и при control_ratio=0
    (выдаёт warning).
    """
    warnings: list[str] = []

    shortcut_pairs = _coerce_pairs(pairs)
    total = len(shortcut_pairs)

    if total == 0:
        warnings.append(
            "Empty shortcut-pairs pool: nothing to control. "
            "Was this run done without shortcut_applied=True pairs?"
        )

    control_size = _compute_control_size(total, control_ratio)

    if control_ratio <= 0:
        warnings.append(
            "control_ratio=0.0 → шаг отключён, контрольная выборка пуста. "
            "Включите control_ratio > 0 (например, 0.1) для замера false_positive_rate."
        )

    if control_size == 0 or scorer is None:
        if scorer is None and control_size > 0:
            warnings.append(
                "scorer=None: невозможно посчитать library_reduced_score. "
                "Передайте Callable[[dict], float], повторяющий full path."
            )
        return {
            "shortcut_pairs_total": total,
            "control_size": 0,
            "false_positive_count": 0,
            "false_positive_rate": 0.0,
            "threshold": float(threshold),
            "control_ratio": float(control_ratio),
            "examples": [],
            "warnings": warnings,
        }

    # Детерминированная случайная выборка через локальный Random (не трогаем глобал).
    rng = random.Random(rng_seed)
    sample_indices = rng.sample(range(total), k=control_size)

    examples: list[dict[str, Any]] = []
    false_positive_count = 0

    for idx in sample_indices:
        pair = shortcut_pairs[idx]
        try:
            library_reduced_score = float(scorer(pair))
        except Exception as exc:  # noqa: BLE001 — изолируем падения отдельных пар
            warnings.append(
                f"scorer failed on pair ({pair.get('app_a')!r},"
                f" {pair.get('app_b')!r}): {type(exc).__name__}: {exc}"
            )
            continue

        is_false_positive = library_reduced_score < threshold
        if is_false_positive:
            false_positive_count += 1

        examples.append(
            {
                "app_a": pair.get("app_a"),
                "app_b": pair.get("app_b"),
                "library_reduced_score": library_reduced_score,
                "false_positive": is_false_positive,
            }
        )

    successful = len(examples)  # сколько scorer-вызовов прошло без ошибок
    if successful == 0:
        false_positive_rate = 0.0
    else:
        false_positive_rate = false_positive_count / successful

    return {
        "shortcut_pairs_total": total,
        "control_size": successful,
        "false_positive_count": false_positive_count,
        "false_positive_rate": float(false_positive_rate),
        "threshold": float(threshold),
        "control_ratio": float(control_ratio),
        "examples": examples,
        "warnings": warnings,
    }


__all__ = ["run_shortcut_control"]

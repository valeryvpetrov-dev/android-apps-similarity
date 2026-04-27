#!/usr/bin/env python3
"""DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: реальная калибровка LAYER_WEIGHTS.

Задача: заменить hard-coded LAYER_WEIGHTS (DEEP-19 нормировка) реально
откалиброванными по labelled-парам F-Droid v2 (350 APK).

Ground truth:
  * clone-пары: одинаковый package_name (например ``a2dp.Vol_137`` и
    ``a2dp.Vol_169``) — разные версии одного приложения. F-Droid v2
    содержит 175 таких пар (175 packages * 2 versions);
  * non-clone-пары: разные package_name (без префиксного совпадения).

Калибровка:
  * метод — grid-search по симплексу 4 активных слоёв
    ``code/component/resource/library`` с шагом ``grid_step`` (по
    умолчанию 0.05);
  * критерий — F1 по бинарному классификатору
    ``score >= optimal_threshold``;
  * threshold выбирается на train; F1 оценивается на test.

Train/test split — стратифицированный 70/30 с фиксированным seed=42.

Без androguard слой ``api`` не извлекаем — оставляем вес ``api=0.0`` в
финальном артефакте с явным пояснением. Вес ``metadata=0.0`` сохраняется
(metadata — tiebreaker, не входит в weighted score). Веса ``code_v4``,
``code_v4_shingled``, ``resource_v2`` остаются ``0.0`` (не активированы).

CLI:
  python3 script/calibrate_layer_weights_fdroid.py \
      --corpus_dir /Users/.../fdroid-corpus-v2-apks \
      --out experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json \
      --grid_step 0.05 --seed 42 --test_size 0.3
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable, Iterator

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

ACTIVE_LAYERS: tuple[str, ...] = ("code", "component", "resource", "library")

GROUND_TRUTH_LABEL_CLONE = 1
GROUND_TRUTH_LABEL_NON_CLONE = 0

DEFAULT_FDROID_V2_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_OUTPUT_PATH = (
    _PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "DEEP-27-LAYER-WEIGHTS-FDROID"
    / "calibrated_weights.json"
)

# Полный набор слоёв (для финального JSON-артефакта). Слои с нулевым
# весом не участвуют в weighted score, но регистрируются для совместимости
# с _LAYER_WEIGHTS_FALLBACK / _load_layer_weights в m_static_views.
ALL_REGISTERED_LAYERS: tuple[str, ...] = (
    "code", "component", "resource", "library", "api",
    "code_v4", "code_v4_shingled", "resource_v2",
)


# ---------------------------------------------------------------------------
# Grid generation: симплекс с шагом grid_step и sum=1.0.
# ---------------------------------------------------------------------------

def iter_grid_weights(grid_step: float = 0.05,
                      n_layers: int = 4) -> Iterator[tuple[float, ...]]:
    """Итерация по точкам ``n_layers``-мерного симплекса с шагом
    ``grid_step`` и суммой ``1.0``.

    Для grid_step=0.05, n_layers=4 даёт 1771 точку. Для grid_step=0.1 —
    286 точек. Для grid_step=0.25 — 35 точек.

    Точки строятся как все комбинации целочисленных индексов
    ``(i1, i2, i3, i4)`` с ``i_k >= 0`` и ``sum i_k == n_steps``,
    где ``n_steps = round(1.0 / grid_step)``. Веса:
    ``w_k = i_k / n_steps``. Это даёт точную сумму ``1.0`` в
    floating-point до знака ``1e-9``.
    """
    if grid_step <= 0 or grid_step > 1:
        raise ValueError(f"grid_step must be in (0, 1], got {grid_step}")
    n_steps = int(round(1.0 / grid_step))
    if abs(n_steps * grid_step - 1.0) > 1e-6:
        raise ValueError(
            f"grid_step={grid_step} must divide 1.0 evenly "
            f"(n_steps={n_steps}, residual={n_steps * grid_step - 1.0})"
        )

    def _walk(remaining: int, slots: int) -> Iterator[tuple[int, ...]]:
        if slots == 1:
            yield (remaining,)
            return
        for take in range(remaining + 1):
            for tail in _walk(remaining - take, slots - 1):
                yield (take,) + tail

    for combo in _walk(n_steps, n_layers):
        yield tuple(idx / n_steps for idx in combo)


# ---------------------------------------------------------------------------
# Per-pair scoring: единая формула.
# ---------------------------------------------------------------------------

def _jaccard(left: set, right: set) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def score_pair_with_weights(pair: dict, weights: dict[str, float]) -> float:
    """Вычислить ``full_similarity_score``-подобный score на одной паре.

    ``pair`` — словарь ``{"a": {layer: set, ...}, "b": {layer: set, ...}}``.
    ``weights`` — ``{layer: weight}`` для активных слоёв.

    Формула: ``sum_l (w_l * jaccard(a_l, b_l)) / sum_l w_l``,
    где сумма берётся по слоям с ``w_l > 0`` и непустым ``a_l|b_l``.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    a = pair["a"]
    b = pair["b"]
    for layer, weight in weights.items():
        if weight <= 0:
            continue
        layer_a = a.get(layer, set())
        layer_b = b.get(layer, set())
        if not layer_a and not layer_b:
            continue
        weighted_sum += weight * _jaccard(layer_a, layer_b)
        weight_total += weight
    if weight_total <= 0:
        return 0.0
    return weighted_sum / weight_total


# ---------------------------------------------------------------------------
# Ground truth construction.
# ---------------------------------------------------------------------------

@dataclass
class _AppRecord:
    app_id: str
    package: str
    layers: dict[str, set]


def _split_package_versioncode(app_id: str) -> tuple[str, str | None]:
    """Разбить ``app_id`` вида ``a2dp.Vol_137`` на (package, versioncode).

    Если разбиение не удалось — вернуть ``(app_id, None)``.
    """
    if "_" not in app_id:
        return app_id, None
    package, _, version = app_id.rpartition("_")
    if not version.isdigit():
        return app_id, None
    return package, version


def load_corpus_records(corpus_dir: Path) -> list[_AppRecord]:
    """Загрузить APK-records из corpus_dir через discover_app_records_from_apk_root."""
    from screening_runner import discover_app_records_from_apk_root

    raw = discover_app_records_from_apk_root(corpus_dir)
    out: list[_AppRecord] = []
    for rec in raw:
        package, _ = _split_package_versioncode(str(rec["app_id"]))
        layers = rec.get("layers", {})
        out.append(_AppRecord(
            app_id=str(rec["app_id"]),
            package=package,
            layers={k: set(v) if isinstance(v, (set, list, tuple)) else set()
                    for k, v in layers.items()},
        ))
    return out


def build_ground_truth_pairs(
    corpus_dir: Path,
    *,
    n_clone_max: int | None = None,
    n_non_clone_max: int | None = None,
    seed: int = 42,
) -> list[tuple[dict, int]]:
    """Сформировать labelled-пары (clone / non-clone) из F-Droid v2.

    clone-пара: два APK с одинаковым ``package`` (разные version-codes).
    non-clone-пара: два APK с разными ``package`` (выбираем ровно
    столько же, сколько clone-пар, чтобы датасет был сбалансирован).

    Возвращает список ``[(pair_dict, label), ...]`` в детерминированном
    порядке (отсортирован по ключу пары).
    """
    records = load_corpus_records(corpus_dir)
    rng = random.Random(seed)

    # Группировка по package.
    by_package: dict[str, list[_AppRecord]] = {}
    for r in records:
        by_package.setdefault(r.package, []).append(r)

    # Clone-пары: для каждого пакета с >= 2 версиями берём
    # (v_min, v_max). Не берём все C(k,2), чтобы не получить
    # коррелированные дубликаты.
    clone_pairs: list[tuple[dict, int]] = []
    for package, versions in sorted(by_package.items()):
        if len(versions) < 2:
            continue
        # Сортируем по app_id для детерминизма.
        versions_sorted = sorted(versions, key=lambda r: r.app_id)
        a, b = versions_sorted[0], versions_sorted[-1]
        clone_pairs.append((
            {
                "a": a.layers,
                "b": b.layers,
                "_a_id": a.app_id,
                "_b_id": b.app_id,
                "_label_source": "same_package",
            },
            GROUND_TRUTH_LABEL_CLONE,
        ))

    if n_clone_max is not None and len(clone_pairs) > n_clone_max:
        rng.shuffle(clone_pairs)
        clone_pairs = clone_pairs[:n_clone_max]
        clone_pairs.sort(key=lambda x: (x[0]["_a_id"], x[0]["_b_id"]))

    # Non-clone: случайные пары с разными packages.
    target_non_clone = (
        n_non_clone_max if n_non_clone_max is not None else len(clone_pairs)
    )
    all_packages = sorted(by_package.keys())
    non_clone_pairs: list[tuple[dict, int]] = []
    seen: set[tuple[str, str]] = set()
    max_attempts = target_non_clone * 50
    attempts = 0
    while len(non_clone_pairs) < target_non_clone and attempts < max_attempts:
        attempts += 1
        pkg_a, pkg_b = rng.sample(all_packages, 2)
        # Различие пакетов гарантировано sample(unique).
        # Берём канонически первую версию каждого пакета.
        ra = sorted(by_package[pkg_a], key=lambda r: r.app_id)[0]
        rb = sorted(by_package[pkg_b], key=lambda r: r.app_id)[0]
        key = tuple(sorted((ra.app_id, rb.app_id)))
        if key in seen:
            continue
        seen.add(key)
        non_clone_pairs.append((
            {
                "a": ra.layers,
                "b": rb.layers,
                "_a_id": ra.app_id,
                "_b_id": rb.app_id,
                "_label_source": "different_package",
            },
            GROUND_TRUTH_LABEL_NON_CLONE,
        ))

    non_clone_pairs.sort(key=lambda x: (x[0]["_a_id"], x[0]["_b_id"]))
    pairs = clone_pairs + non_clone_pairs
    return pairs


# ---------------------------------------------------------------------------
# Calibration core.
# ---------------------------------------------------------------------------

def _stratified_split(
    pairs: list[tuple[dict, int]],
    *,
    test_size: float,
    seed: int,
) -> tuple[list[tuple[dict, int]], list[tuple[dict, int]]]:
    """Стратифицированный split по label с фиксированным seed."""
    rng = random.Random(seed)
    by_label: dict[int, list[tuple[dict, int]]] = {}
    for pair, label in pairs:
        by_label.setdefault(label, []).append((pair, label))
    train: list[tuple[dict, int]] = []
    test: list[tuple[dict, int]] = []
    for label, items in by_label.items():
        items_sorted = sorted(items, key=lambda x: (x[0].get("_a_id", ""),
                                                     x[0].get("_b_id", "")))
        rng.shuffle(items_sorted)
        n_test = max(1, int(round(len(items_sorted) * test_size))) if items_sorted else 0
        test.extend(items_sorted[:n_test])
        train.extend(items_sorted[n_test:])
    return train, test


def _f1_score(scores: list[float], labels: list[int],
              threshold: float) -> tuple[float, float, float]:
    """F1 по бинарному классификатору ``score >= threshold``.

    Возвращает (F1, precision, recall).
    """
    tp = fp = fn = 0
    for s, l in zip(scores, labels):
        pred = 1 if s >= threshold else 0
        if pred == 1 and l == 1:
            tp += 1
        elif pred == 1 and l == 0:
            fp += 1
        elif pred == 0 and l == 1:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)
    return f1, precision, recall


def _best_threshold_and_f1(scores: list[float],
                            labels: list[int]) -> tuple[float, float]:
    """Для фиксированных score выбрать threshold, максимизирующий F1.

    Перебираем уникальные значения scores как кандидатов в threshold.
    """
    if not scores:
        return 0.5, 0.0
    candidates = sorted(set(scores))
    # Также добавим midpoints между соседними значениями — стандартная
    # эвристика для устойчивости.
    midpoints = [(candidates[i] + candidates[i + 1]) / 2
                 for i in range(len(candidates) - 1)]
    candidates_full = sorted(set(candidates + midpoints + [0.0, 0.5, 1.0]))
    best_f1 = -1.0
    best_thr = 0.5
    for thr in candidates_full:
        f1, _, _ = _f1_score(scores, labels, thr)
        if f1 > best_f1:
            best_f1 = f1
            best_thr = thr
    return best_thr, max(0.0, best_f1)


def calibrate_layer_weights_grid(
    pairs: list[tuple[dict, int]],
    *,
    grid_step: float = 0.05,
    seed: int = 42,
    test_size: float = 0.3,
    layers: tuple[str, ...] = ACTIVE_LAYERS,
) -> dict:
    """Grid-search калибровка LAYER_WEIGHTS на labelled-парах.

    Алгоритм:
      1. Стратифицированный split pairs → train (70%) + test (30%).
      2. Для каждой пары — pre-compute per-layer Jaccard scores
         (один раз, не зависит от весов).
      3. Перебор всех точек симплекса с шагом grid_step. Для каждой
         точки:
           * вычислить full_score = sum_l(w_l * j_l(pair)) для всех
             train-пар;
           * выбрать threshold, максимизирующий train F1;
           * запомнить (weights, train_F1, threshold).
      4. Лучшие веса по train F1 → проверяем на test, получаем test_F1.

    Возвращает dict с ключами:
      ``weights`` (dict layer→weight для всех ACTIVE_LAYERS, sum=1),
      ``train_F1``, ``test_F1``, ``threshold``,
      ``n_train_pairs``, ``n_test_pairs``,
      ``calibration_method`` (= "grid-search"),
      ``grid_step``, ``seed``,
      ``best_train_precision``, ``best_train_recall``,
      ``best_test_precision``, ``best_test_recall``.
    """
    train, test = _stratified_split(pairs, test_size=test_size, seed=seed)

    # Pre-compute per-layer Jaccard scores для каждой train- и test-пары.
    def _per_layer_jaccards(pair: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for layer in layers:
            out[layer] = _jaccard(pair["a"].get(layer, set()),
                                  pair["b"].get(layer, set()))
        return out

    train_per_layer = [_per_layer_jaccards(p) for p, _ in train]
    train_labels = [l for _, l in train]
    test_per_layer = [_per_layer_jaccards(p) for p, _ in test]
    test_labels = [l for _, l in test]

    best_train_f1 = -1.0
    best_weights: tuple[float, ...] | None = None
    best_threshold = 0.5

    for w_tuple in iter_grid_weights(grid_step=grid_step, n_layers=len(layers)):
        # Вычислить full_score на train.
        weight_total = sum(w_tuple)
        train_scores: list[float] = []
        for pl in train_per_layer:
            wsum = 0.0
            wtot = 0.0
            for layer, w in zip(layers, w_tuple):
                if w <= 0:
                    continue
                wsum += w * pl[layer]
                wtot += w
            train_scores.append(wsum / wtot if wtot > 0 else 0.0)
        thr, f1 = _best_threshold_and_f1(train_scores, train_labels)
        if f1 > best_train_f1:
            best_train_f1 = f1
            best_weights = w_tuple
            best_threshold = thr

    if best_weights is None:
        # Fallback на равномерное распределение, если grid пустой.
        best_weights = tuple([1.0 / len(layers)] * len(layers))
        best_threshold = 0.5
        best_train_f1 = 0.0

    # Test F1.
    test_scores: list[float] = []
    for pl in test_per_layer:
        wsum = 0.0
        wtot = 0.0
        for layer, w in zip(layers, best_weights):
            if w <= 0:
                continue
            wsum += w * pl[layer]
            wtot += w
        test_scores.append(wsum / wtot if wtot > 0 else 0.0)
    test_f1, test_p, test_r = _f1_score(test_scores, test_labels, best_threshold)
    train_scores: list[float] = []
    for pl in train_per_layer:
        wsum = 0.0
        wtot = 0.0
        for layer, w in zip(layers, best_weights):
            if w <= 0:
                continue
            wsum += w * pl[layer]
            wtot += w
        train_scores.append(wsum / wtot if wtot > 0 else 0.0)
    _, train_p, train_r = _f1_score(train_scores, train_labels, best_threshold)

    weights_dict = {layer: float(w) for layer, w in zip(layers, best_weights)}

    return {
        "weights": weights_dict,
        "train_F1": float(best_train_f1),
        "test_F1": float(test_f1),
        "threshold": float(best_threshold),
        "n_train_pairs": len(train),
        "n_test_pairs": len(test),
        "n_train_clone": sum(1 for l in train_labels if l == 1),
        "n_train_non_clone": sum(1 for l in train_labels if l == 0),
        "n_test_clone": sum(1 for l in test_labels if l == 1),
        "n_test_non_clone": sum(1 for l in test_labels if l == 0),
        "calibration_method": "grid-search",
        "grid_step": float(grid_step),
        "seed": int(seed),
        "best_train_precision": float(train_p),
        "best_train_recall": float(train_r),
        "best_test_precision": float(test_p),
        "best_test_recall": float(test_r),
        "layers_calibrated": list(layers),
    }


# ---------------------------------------------------------------------------
# Output: build calibrated_weights.json в формате DEEP-22.
# ---------------------------------------------------------------------------

def build_calibrated_weights_payload(calibration: dict, *,
                                       n_documents: int) -> dict:
    """Сформировать JSON-payload с метаданными для calibrated_weights.json.

    Раскладывает откалиброванные веса по полному набору
    ``ALL_REGISTERED_LAYERS``. Слой ``api`` остаётся ``0.0`` (без
    androguard на корпусе F-Droid v2 markov chains не извлекаемы).
    """
    full_weights = {layer: 0.0 for layer in ALL_REGISTERED_LAYERS}
    for layer, w in calibration["weights"].items():
        full_weights[layer] = float(w)

    # Sanity-check: сумма активных = 1.0.
    active_sum = sum(v for v in full_weights.values() if v > 0)
    assert 0.99 <= active_sum <= 1.01, (
        f"sum of active weights = {active_sum} != 1.0"
    )

    payload = {
        "schema_version": "layer-weights-v1",
        "snapshot_id": "DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE",
        "source": (
            "calibrated on F-Droid v2 corpus (350 APK, 175 packages × 2 "
            "versioncode pairs vs random different-package pairs) at "
            + datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "calibration_method": calibration["calibration_method"],
        "grid_step": calibration["grid_step"],
        "seed": calibration["seed"],
        "real_calibration_status": "calibrated",
        "real_calibration_blocked_by": None,
        "real_calibration_task": "DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE",
        "n_documents": int(n_documents),
        "n_train_pairs": calibration["n_train_pairs"],
        "n_test_pairs": calibration["n_test_pairs"],
        "train_F1": calibration["train_F1"],
        "test_F1": calibration["test_F1"],
        "threshold": calibration["threshold"],
        "best_train_precision": calibration["best_train_precision"],
        "best_train_recall": calibration["best_train_recall"],
        "best_test_precision": calibration["best_test_precision"],
        "best_test_recall": calibration["best_test_recall"],
        "layers_calibrated": calibration["layers_calibrated"],
        "weights": full_weights,
        "note": (
            "Веса откалиброваны grid-search по симплексу 4 активных слоёв "
            "(code/component/resource/library) на F-Droid v2. "
            "Слой 'api' пока остаётся 0.0: extract_api_markov требует "
            "androguard, который недоступен в текущем окружении. "
            "После установки androguard и повторной калибровки 'api' "
            "может быть включён в активные слои. Слои 'metadata', "
            "'code_v4', 'code_v4_shingled', 'resource_v2' остаются 0.0 "
            "по архитектуре (metadata — tiebreaker; v4/v2 — не "
            "активированы)."
        ),
    }
    return payload


def write_calibrated_weights(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False)
                     + "\n",
                     encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate LAYER_WEIGHTS on F-Droid v2 labelled pairs."
    )
    parser.add_argument(
        "--corpus_dir",
        default=str(DEFAULT_FDROID_V2_CORPUS_DIR),
        help="F-Droid v2 APK corpus directory.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output calibrated_weights.json path.",
    )
    parser.add_argument(
        "--grid_step",
        type=float,
        default=0.05,
        help="Grid step for simplex (default: 0.05).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for split/non-clone sampling (default: 42).",
    )
    parser.add_argument(
        "--test_size",
        type=float,
        default=0.3,
        help="Test split fraction (default: 0.3).",
    )
    parser.add_argument(
        "--n_clone_max",
        type=int,
        default=None,
        help="Optional cap on number of clone pairs (default: all).",
    )
    parser.add_argument(
        "--n_non_clone_max",
        type=int,
        default=None,
        help=("Optional cap on number of non-clone pairs (default: equal "
              "to clone count)."),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_dir = Path(args.corpus_dir).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    print(f"[DEEP-27] loading corpus from {corpus_dir} ...", flush=True)
    pairs = build_ground_truth_pairs(
        corpus_dir,
        n_clone_max=args.n_clone_max,
        n_non_clone_max=args.n_non_clone_max,
        seed=args.seed,
    )
    n_clone = sum(1 for _, l in pairs if l == 1)
    n_non = len(pairs) - n_clone
    print(f"[DEEP-27] ground truth: {n_clone} clone, {n_non} non-clone",
          flush=True)
    print(f"[DEEP-27] grid-search step={args.grid_step} ...", flush=True)
    calibration = calibrate_layer_weights_grid(
        pairs,
        grid_step=args.grid_step,
        seed=args.seed,
        test_size=args.test_size,
    )
    n_documents = 0
    try:
        # Считаем уникальные APK в корпусе как n_documents.
        n_documents = sum(1 for p in corpus_dir.rglob("*.apk"))
    except Exception:
        pass

    payload = build_calibrated_weights_payload(
        calibration, n_documents=n_documents,
    )
    write_calibrated_weights(payload, out_path)
    print(f"[DEEP-27] wrote {out_path}", flush=True)
    print(f"[DEEP-27] weights: {calibration['weights']}", flush=True)
    print(f"[DEEP-27] train_F1={calibration['train_F1']:.4f} "
          f"test_F1={calibration['test_F1']:.4f} "
          f"threshold={calibration['threshold']:.4f}", flush=True)


if __name__ == "__main__":
    main()

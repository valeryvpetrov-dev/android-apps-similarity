#!/usr/bin/env python3
"""DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED: пересчёт LAYER_WEIGHTS на
расширенном корпусе F-Droid v2 + DEEP-30 inject-пары (опц. HINT-30 R8).

Гипотеза DEEP-31: вес слоя ``code`` в LAYER_WEIGHTS должен подняться
с DEEP-27 0.05 до >0.10, потому что в DEEP-27 train-set не было
inject-примеров — только version-пары F-Droid v2, на которых code-слой
тривиально совпадает (одинаковые DEX-имена). DEEP-30 показал, что
``code_view_v4_shingled`` достигает F1=1.0 на сценарии code-injection,
но это не было видно в калибровке. DEEP-31 закрывает эту дыру.

Калибровочный train-set:
  * F-Droid v2: 175 clone-пар (одинаковый package, разные version-codes)
    + 175 non-clone (разные packages);
  * DEEP-30: 35 inject-пар ``(original.apk, original__inject.apk)`` —
    как clone (apktool сохраняет class/method-id, инжект — 4 const-string
    no-op в onCreate первой Activity);
  * HINT-30 R8 mock-пары — **не используются** (synthetic-режим без
    реальных code/component/library токенов; явно зафиксировано в README
    DEEP-31). Если в будущем `r8_pairs.json` сменит ``mode`` на ``real``
    и будет содержать реальные APK-пары, нужно расширить
    ``load_hint30_r8_pairs``.

Per-pair feature extraction:
  * code: ``set(method_ids)`` из ``code_view_v4._collect_methods_from_apk``
    (требует androguard). Это даёт реальный сигнал для inject-пары
    (Jaccard ≈ 1.0) и реальный сигнал для F-Droid version-пары
    (частичное совпадение, обычно 0.6–0.95);
  * component / resource / library — те же токены, что и в DEEP-27
    (через ``screening_runner.extract_layers_from_apk``). Это сохраняет
    совместимость с DEEP-22 / DEEP-27 формулой `full_similarity_score`.

Калибровка: grid-search по симплексу 4 активных слоёв
``(code, component, resource, library)`` с шагом 0.05, sum=1.0,
F1-max с tie-break по Youden's J.

Train/test 70/30 со стратификацией по label, seed=42.

CLI:
  python3 script/calibrate_layer_weights_mixed.py \
      --corpus_dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
      --inject_dir /tmp/wave30-deep-corpus/rebuilt \
      --inject_report experiments/artifacts/DEEP-30-CODE-INJECT/report.json \
      --out experiments/artifacts/DEEP-31-LAYER-WEIGHTS-MIXED/calibrated_weights.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Переиспользуем DEEP-27 grid + scoring + split + threshold logic.
from calibrate_layer_weights_fdroid import (  # noqa: E402
    ALL_REGISTERED_LAYERS,
    GROUND_TRUTH_LABEL_CLONE,
    GROUND_TRUTH_LABEL_NON_CLONE,
    _f1_score,
    _stratified_split,
    iter_grid_weights,
    _split_package_versioncode,
)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

ACTIVE_LAYERS_MIXED: tuple[str, ...] = ("code", "component", "resource",
                                          "library")

# Ссылочные веса DEEP-27 (для weight_delta_vs_deep27 и интерпретации).
# Источник: experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json
DEEP27_WEIGHTS_REFERENCE: dict[str, float] = {
    "code": 0.05,
    "component": 0.60,
    "resource": 0.0,
    "library": 0.35,
}

DEFAULT_FDROID_V2_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_INJECT_DIR = Path("/tmp/wave30-deep-corpus/rebuilt")
DEFAULT_INJECT_REPORT = (
    _PROJECT_ROOT
    / "experiments" / "artifacts" / "DEEP-30-CODE-INJECT" / "report.json"
)
DEFAULT_OUTPUT_PATH = (
    _PROJECT_ROOT
    / "experiments" / "artifacts" / "DEEP-31-LAYER-WEIGHTS-MIXED"
    / "calibrated_weights.json"
)
DEFAULT_COMPARISON_PATH = (
    _PROJECT_ROOT
    / "experiments" / "artifacts" / "DEEP-31-LAYER-WEIGHTS-MIXED"
    / "comparison_with_deep27.json"
)
# Production-канонический LAYER_WEIGHTS-файл из DEEP-22 (его читает
# m_static_views при загрузке весов).
DEEP22_PROD_PATH = (
    _PROJECT_ROOT
    / "experiments" / "artifacts" / "DEEP-22-LAYER-WEIGHTS-EXTERNALIZED"
    / "calibrated_weights.json"
)
# Кэш method_ids per APK — чтобы повторный прогон не извлекал DEX заново.
DEFAULT_FEATURE_CACHE = Path("/tmp/wave31-deep-features-cache")


# ---------------------------------------------------------------------------
# Per-APK feature extraction
# ---------------------------------------------------------------------------

def _extract_screening_layers(apk_path: Path) -> dict[str, set[str]]:
    """Извлечь component/resource/library/metadata через
    screening_runner.extract_layers_from_apk (точно как DEEP-27).

    Возвращает dict ``{layer: set[token]}``.
    """
    from screening_runner import extract_layers_from_apk

    return extract_layers_from_apk(apk_path)


def _extract_method_ids_set(apk_path: Path) -> set[str]:
    """Извлечь множество ``method_id`` через
    code_view_v4._collect_methods_from_apk (требует androguard).

    Возвращает ``set[str]``. Если androguard недоступен или DEX не
    парсится — возвращает пустой set (метрика deg=0 будет дальше
    отброшена в score_pair_with_weights).
    """
    try:
        from code_view_v4 import _collect_methods_from_apk
    except ImportError:
        return set()
    try:
        methods = _collect_methods_from_apk(apk_path)
    except Exception:
        return set()
    ids: set[str] = set()
    for method_id, _ in methods:
        ids.add(method_id)
    return ids


def _cache_path_for(apk_path: Path, cache_dir: Path) -> Path:
    """Путь к кэш-файлу для конкретного APK (по basename + size).

    Имя файла кэша зависит и от ``apk_path.stem`` и от ``size`` —
    защита от коллизий, если одно имя APK использовалось дважды
    (например, original vs inject в разных каталогах с одинаковым
    stem).
    """
    try:
        size = apk_path.stat().st_size
    except OSError:
        size = -1
    safe_stem = apk_path.stem.replace("/", "_")
    return cache_dir / f"{safe_stem}__{size}.json"


def _serialise_layers_to_json(layers: dict[str, set[str]]) -> dict:
    return {layer: sorted(tokens) for layer, tokens in layers.items()}


def _deserialise_layers_from_json(payload: dict) -> dict[str, set[str]]:
    return {layer: set(tokens) for layer, tokens in payload.items()}


def extract_apk_features(apk_path: Path,
                          *,
                          cache_dir: Path | None = None) -> dict[str, set[str]]:
    """Извлечь все 4 активных слоя ``ACTIVE_LAYERS_MIXED`` из одного APK.

    code-слой использует method_ids (через androguard), не dex-имена
    (как в screening_runner). Это даёт DEEP-31 содержательный сигнал
    code-слоя.

    Если ``cache_dir`` задан и файл кэша существует — читает из кэша.
    Иначе — извлекает и пишет в кэш.
    """
    apk_path = Path(apk_path)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _cache_path_for(apk_path, cache_dir)
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                cached = _deserialise_layers_from_json(payload)
                # Фильтруем до ACTIVE_LAYERS_MIXED.
                return {layer: cached.get(layer, set())
                        for layer in ACTIVE_LAYERS_MIXED}
            except Exception:
                pass  # cache corrupted → re-extract

    screening = _extract_screening_layers(apk_path)
    method_ids = _extract_method_ids_set(apk_path)
    layers = {
        "code": method_ids,
        "component": set(screening.get("component", set())),
        "resource": set(screening.get("resource", set())),
        "library": set(screening.get("library", set())),
    }

    if cache_dir is not None:
        cache_path = _cache_path_for(apk_path, cache_dir)
        try:
            cache_path.write_text(
                json.dumps(_serialise_layers_to_json(layers),
                           ensure_ascii=False, indent=0)
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass
    return layers


# ---------------------------------------------------------------------------
# Ground-truth pair builders for mixed corpus
# ---------------------------------------------------------------------------

def build_fdroid_v2_pairs(
    corpus_dir: Path,
    *,
    cache_dir: Path | None = None,
    n_clone_max: int | None = None,
    n_non_clone_max: int | None = None,
    seed: int = 42,
) -> list[tuple[dict, int]]:
    """F-Droid v2 clone (одинаковый package) + non-clone (разные package)
    пары, собранные с помощью method_ids-feature для слоя ``code``.

    Возвращает список ``(pair_dict, label)`` в детерминированном
    порядке.
    """
    import random

    rng = random.Random(seed)
    apk_files = sorted(corpus_dir.rglob("*.apk"))

    # Группировка по package_name.
    by_package: dict[str, list[Path]] = {}
    for apk_path in apk_files:
        package, _ = _split_package_versioncode(apk_path.stem)
        by_package.setdefault(package, []).append(apk_path)

    # Кэш фич: один раз на APK.
    feature_cache: dict[str, dict[str, set[str]]] = {}

    def _features(apk_path: Path) -> dict[str, set[str]]:
        key = str(apk_path.resolve())
        if key not in feature_cache:
            feature_cache[key] = extract_apk_features(
                apk_path, cache_dir=cache_dir,
            )
        return feature_cache[key]

    clone_pairs: list[tuple[dict, int]] = []
    for package, apks in sorted(by_package.items()):
        if len(apks) < 2:
            continue
        apks_sorted = sorted(apks, key=lambda p: p.stem)
        a, b = apks_sorted[0], apks_sorted[-1]
        clone_pairs.append((
            {
                "a": _features(a),
                "b": _features(b),
                "_a_id": a.stem,
                "_b_id": b.stem,
                "_label_source": "fdroid_v2_same_package",
            },
            GROUND_TRUTH_LABEL_CLONE,
        ))

    if n_clone_max is not None and len(clone_pairs) > n_clone_max:
        rng.shuffle(clone_pairs)
        clone_pairs = clone_pairs[:n_clone_max]
        clone_pairs.sort(key=lambda x: (x[0]["_a_id"], x[0]["_b_id"]))

    target_non_clone = (
        n_non_clone_max if n_non_clone_max is not None else len(clone_pairs)
    )
    all_packages = sorted(by_package.keys())
    non_clone_pairs: list[tuple[dict, int]] = []
    seen: set[tuple[str, str]] = set()
    max_attempts = max(target_non_clone * 50, 100)
    attempts = 0
    while len(non_clone_pairs) < target_non_clone and attempts < max_attempts:
        attempts += 1
        if len(all_packages) < 2:
            break
        pkg_a, pkg_b = rng.sample(all_packages, 2)
        a = sorted(by_package[pkg_a], key=lambda p: p.stem)[0]
        b = sorted(by_package[pkg_b], key=lambda p: p.stem)[0]
        key = tuple(sorted((a.stem, b.stem)))
        if key in seen:
            continue
        seen.add(key)
        non_clone_pairs.append((
            {
                "a": _features(a),
                "b": _features(b),
                "_a_id": a.stem,
                "_b_id": b.stem,
                "_label_source": "fdroid_v2_different_package",
            },
            GROUND_TRUTH_LABEL_NON_CLONE,
        ))

    non_clone_pairs.sort(key=lambda x: (x[0]["_a_id"], x[0]["_b_id"]))
    return clone_pairs + non_clone_pairs


def build_deep30_inject_pairs(
    inject_report: Path,
    *,
    corpus_dir: Path,
    inject_dir: Path,
    cache_dir: Path | None = None,
) -> list[tuple[dict, int]]:
    """Прочитать DEEP-30 ``report.json`` и собрать clone-пары
    ``(original_apk, inject_apk)``.

    inject-APK находятся в ``inject_dir`` под именем
    ``<original_stem>__inject.apk``. original-APK — в ``corpus_dir``.

    Возвращает список ``(pair_dict, GROUND_TRUTH_LABEL_CLONE)`` —
    inject-пары всегда clone (один и тот же APK с локальной no-op
    модификацией).
    """
    payload = json.loads(inject_report.read_text(encoding="utf-8"))
    pairs: list[tuple[dict, int]] = []
    feature_cache: dict[str, dict[str, set[str]]] = {}

    def _features(apk_path: Path) -> dict[str, set[str]]:
        key = str(apk_path.resolve())
        if key not in feature_cache:
            feature_cache[key] = extract_apk_features(
                apk_path, cache_dir=cache_dir,
            )
        return feature_cache[key]

    scored = payload.get("scored_pairs", [])
    for entry in scored:
        if entry.get("label") != "clone":
            continue
        apk_a = entry.get("apk_a")
        apk_b = entry.get("apk_b")
        if not apk_a or not apk_b:
            continue
        a_path = corpus_dir / apk_a
        b_path = inject_dir / apk_b
        if not a_path.exists() or not b_path.exists():
            continue
        pairs.append((
            {
                "a": _features(a_path),
                "b": _features(b_path),
                "_a_id": a_path.stem,
                "_b_id": b_path.stem,
                "_label_source": "deep30_inject_clone",
            },
            GROUND_TRUTH_LABEL_CLONE,
        ))

    pairs.sort(key=lambda x: (x[0]["_a_id"], x[0]["_b_id"]))
    return pairs


# ---------------------------------------------------------------------------
# Calibration core
# ---------------------------------------------------------------------------

def _full_score_for_pair(per_layer: dict[str, float],
                           weights: tuple[float, ...],
                           layers: tuple[str, ...]) -> float:
    """Вычислить full_similarity_score-подобный score из pre-computed
    per-layer Jaccard-ов."""
    wsum = 0.0
    wtot = 0.0
    for layer, w in zip(layers, weights):
        if w <= 0:
            continue
        wsum += w * per_layer[layer]
        wtot += w
    if wtot <= 0:
        return 0.0
    return wsum / wtot


def _jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _per_layer_jaccards(pair: dict,
                         layers: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for layer in layers:
        out[layer] = _jaccard(pair["a"].get(layer, set()),
                              pair["b"].get(layer, set()))
    return out


def _best_threshold_youden(
    scores: list[float],
    labels: list[int],
) -> tuple[float, float, float]:
    """Подобрать threshold, максимизирующий F1, с tie-break Youden's J.

    Возвращает ``(threshold, F1, youden_j)``.
    """
    if not scores:
        return 0.5, 0.0, 0.0
    candidates = sorted(set(scores))
    midpoints = [(candidates[i] + candidates[i + 1]) / 2
                 for i in range(len(candidates) - 1)]
    candidates_full = sorted(set(candidates + midpoints + [0.0, 0.5, 1.0]))
    best_f1 = -1.0
    best_youden = -1.0
    best_thr = 0.5
    n_pos = sum(1 for l in labels if l == 1)
    n_neg = len(labels) - n_pos
    for thr in candidates_full:
        f1, _, _ = _f1_score(scores, labels, thr)
        # youden_j = sensitivity + specificity - 1.
        tp = fp = fn = tn = 0
        for s, l in zip(scores, labels):
            pred = 1 if s >= thr else 0
            if pred == 1 and l == 1:
                tp += 1
            elif pred == 1 and l == 0:
                fp += 1
            elif pred == 0 and l == 1:
                fn += 1
            else:
                tn += 1
        sens = tp / max(1, n_pos) if n_pos > 0 else 0.0
        spec = tn / max(1, n_neg) if n_neg > 0 else 0.0
        youden = sens + spec - 1.0
        # Сравнение: F1 → tie-break по youden_j.
        if (f1 > best_f1) or (f1 == best_f1 and youden > best_youden):
            best_f1 = f1
            best_youden = youden
            best_thr = thr
    return best_thr, max(0.0, best_f1), best_youden


def calibrate_layer_weights_mixed(
    pairs: list[tuple[dict, int]],
    *,
    grid_step: float = 0.05,
    seed: int = 42,
    test_size: float = 0.3,
    layers: tuple[str, ...] = ACTIVE_LAYERS_MIXED,
    deep27_reference: dict[str, float] | None = None,
) -> dict:
    """Grid-search калибровка LAYER_WEIGHTS на mixed-corpus pairs.

    Аргументы:
        pairs: список ``(pair_dict, label)``;
        grid_step: шаг симплекс-сетки (по умолчанию 0.05);
        seed: random seed для split;
        test_size: доля тестовой выборки;
        layers: 4 активных слоя для калибровки;
        deep27_reference: словарь весов DEEP-27 для weight_delta_vs_deep27.

    Возвращает dict с ключами:
        ``weights`` (dict layer→weight, sum=1),
        ``train_F1``, ``test_F1``, ``threshold``,
        ``n_train_pairs``, ``n_test_pairs``,
        ``n_train_clone``, ``n_train_non_clone``,
        ``n_test_clone``, ``n_test_non_clone``,
        ``calibration_method``, ``grid_step``, ``seed``,
        ``best_train_precision``, ``best_train_recall``,
        ``best_test_precision``, ``best_test_recall``,
        ``best_train_youden``, ``best_test_youden``,
        ``layers_calibrated``,
        ``weight_delta_vs_deep27`` (dict layer → new - old).
    """
    if deep27_reference is None:
        deep27_reference = DEEP27_WEIGHTS_REFERENCE

    train, test = _stratified_split(pairs, test_size=test_size, seed=seed)

    train_per_layer = [_per_layer_jaccards(p, layers) for p, _ in train]
    train_labels = [l for _, l in train]
    test_per_layer = [_per_layer_jaccards(p, layers) for p, _ in test]
    test_labels = [l for _, l in test]

    best_train_f1 = -1.0
    best_train_youden = -1.0
    best_weights: tuple[float, ...] | None = None
    best_threshold = 0.5

    for w_tuple in iter_grid_weights(grid_step=grid_step, n_layers=len(layers)):
        train_scores = [_full_score_for_pair(pl, w_tuple, layers)
                        for pl in train_per_layer]
        thr, f1, youden = _best_threshold_youden(train_scores, train_labels)
        if (f1 > best_train_f1) or (
            f1 == best_train_f1 and youden > best_train_youden
        ):
            best_train_f1 = f1
            best_train_youden = youden
            best_weights = w_tuple
            best_threshold = thr

    if best_weights is None:
        # Fallback: равномерное распределение.
        best_weights = tuple([1.0 / len(layers)] * len(layers))
        best_threshold = 0.5
        best_train_f1 = 0.0
        best_train_youden = 0.0

    # Test F1 / threshold.
    test_scores = [_full_score_for_pair(pl, best_weights, layers)
                   for pl in test_per_layer]
    test_f1, test_p, test_r = _f1_score(test_scores, test_labels,
                                          best_threshold)
    train_scores_final = [_full_score_for_pair(pl, best_weights, layers)
                           for pl in train_per_layer]
    _, train_p, train_r = _f1_score(train_scores_final, train_labels,
                                      best_threshold)

    # Youden's J на test.
    n_pos_t = sum(1 for l in test_labels if l == 1)
    n_neg_t = len(test_labels) - n_pos_t
    tp = fp = fn = tn = 0
    for s, l in zip(test_scores, test_labels):
        pred = 1 if s >= best_threshold else 0
        if pred == 1 and l == 1:
            tp += 1
        elif pred == 1 and l == 0:
            fp += 1
        elif pred == 0 and l == 1:
            fn += 1
        else:
            tn += 1
    sens = tp / max(1, n_pos_t) if n_pos_t > 0 else 0.0
    spec = tn / max(1, n_neg_t) if n_neg_t > 0 else 0.0
    test_youden = sens + spec - 1.0

    weights_dict = {layer: float(w) for layer, w in zip(layers, best_weights)}

    weight_delta_vs_deep27 = {
        layer: float(weights_dict[layer]
                     - deep27_reference.get(layer, 0.0))
        for layer in layers
    }

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
        "calibration_method": "grid-search-youden-tiebreak",
        "grid_step": float(grid_step),
        "seed": int(seed),
        "best_train_precision": float(train_p),
        "best_train_recall": float(train_r),
        "best_test_precision": float(test_p),
        "best_test_recall": float(test_r),
        "best_train_youden": float(best_train_youden),
        "best_test_youden": float(test_youden),
        "layers_calibrated": list(layers),
        "weight_delta_vs_deep27": weight_delta_vs_deep27,
    }


# ---------------------------------------------------------------------------
# Output: build calibrated_weights.json в формате DEEP-22
# ---------------------------------------------------------------------------

def build_payload(calibration: dict, *,
                   n_documents: int,
                   train_set_composition: dict) -> dict:
    """Сформировать JSON-payload в формате DEEP-22 layer-weights-v1."""
    full_weights = {layer: 0.0 for layer in ALL_REGISTERED_LAYERS}
    for layer, w in calibration["weights"].items():
        full_weights[layer] = float(w)
    active_sum = sum(v for v in full_weights.values() if v > 0)
    assert 0.99 <= active_sum <= 1.01, (
        f"sum of active weights = {active_sum} != 1.0"
    )
    payload = {
        "schema_version": "layer-weights-v1",
        "snapshot_id": "DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED",
        "source": (
            "calibrated on F-Droid v2 + DEEP-30 inject-пары at "
            + datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "calibration_method": calibration["calibration_method"],
        "grid_step": calibration["grid_step"],
        "seed": calibration["seed"],
        "real_calibration_status": "calibrated",
        "real_calibration_blocked_by": None,
        "real_calibration_task": "DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED",
        "n_documents": int(n_documents),
        "n_train_pairs": calibration["n_train_pairs"],
        "n_test_pairs": calibration["n_test_pairs"],
        "n_train_clone": calibration["n_train_clone"],
        "n_train_non_clone": calibration["n_train_non_clone"],
        "n_test_clone": calibration["n_test_clone"],
        "n_test_non_clone": calibration["n_test_non_clone"],
        "train_F1": calibration["train_F1"],
        "test_F1": calibration["test_F1"],
        "threshold": calibration["threshold"],
        "best_train_precision": calibration["best_train_precision"],
        "best_train_recall": calibration["best_train_recall"],
        "best_test_precision": calibration["best_test_precision"],
        "best_test_recall": calibration["best_test_recall"],
        "best_train_youden": calibration["best_train_youden"],
        "best_test_youden": calibration["best_test_youden"],
        "layers_calibrated": calibration["layers_calibrated"],
        "weights": full_weights,
        "weight_delta_vs_deep27": calibration["weight_delta_vs_deep27"],
        "deep27_reference_weights": dict(DEEP27_WEIGHTS_REFERENCE),
        "train_set_composition": train_set_composition,
        "note": (
            "DEEP-31: пересчёт LAYER_WEIGHTS на расширенном корпусе. "
            "Слой 'code' использует method_ids из code_view_v4 "
            "(не dex-имена как в DEEP-27 screening_runner). "
            "DEEP-30 inject-пары добавлены в train-set как clone, что "
            "должно поднять вес 'code' по сравнению с DEEP-27 0.05. "
            "HINT-30 R8 mock-пары не использованы (synthetic-режим). "
            "Слой 'api' пока 0.0 (extract_api_markov требует androguard "
            "+ markov chain — отдельная задача)."
        ),
    }
    return payload


def write_payload(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    return path


def write_comparison_with_deep27(calibration: dict, path: Path) -> Path:
    """Сформировать JSON-сравнение DEEP-27 (только F-Droid v2) vs
    DEEP-31 (mixed) для прямого comparison-артефакта."""
    deep27_path = (
        _PROJECT_ROOT
        / "experiments" / "artifacts" / "DEEP-27-LAYER-WEIGHTS-FDROID"
        / "calibrated_weights.json"
    )
    deep27_payload = {}
    if deep27_path.exists():
        try:
            deep27_payload = json.loads(deep27_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    comparison = {
        "compared_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deep27_snapshot_id": deep27_payload.get("snapshot_id"),
        "deep31_snapshot_id": "DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED",
        "deep27_n_train_pairs": deep27_payload.get("n_train_pairs"),
        "deep27_n_test_pairs": deep27_payload.get("n_test_pairs"),
        "deep31_n_train_pairs": calibration["n_train_pairs"],
        "deep31_n_test_pairs": calibration["n_test_pairs"],
        "deep27_train_F1": deep27_payload.get("train_F1"),
        "deep27_test_F1": deep27_payload.get("test_F1"),
        "deep31_train_F1": calibration["train_F1"],
        "deep31_test_F1": calibration["test_F1"],
        "deep27_weights": deep27_payload.get("weights", {}),
        "deep31_weights": dict(calibration["weights"]),
        "weight_delta_per_layer": calibration["weight_delta_vs_deep27"],
        "interpretation": (
            "DEEP-27 калибровался только на F-Droid v2 version-парах, "
            "где code-слой не различает clone от non-clone (DEX-имена "
            "одинаковы). DEEP-31 добавил DEEP-30 inject-пары и сменил "
            "code-feature на method_ids — это даёт code-слою реальный "
            "сигнал и должно поднять его вес выше 0.05."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    return path


def copy_to_deep22_canonical(payload: dict, deep22_path: Path = DEEP22_PROD_PATH) -> Path:
    """Скопировать calibrated_weights.json в DEEP-22 canonical-путь
    (его читает m_static_views.LAYER_WEIGHTS)."""
    deep22_path.parent.mkdir(parents=True, exist_ok=True)
    deep22_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False)
                            + "\n",
                            encoding="utf-8")
    return deep22_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate LAYER_WEIGHTS on F-Droid v2 + DEEP-30 inject-пары."
    )
    parser.add_argument("--corpus_dir", default=str(DEFAULT_FDROID_V2_CORPUS_DIR))
    parser.add_argument("--inject_dir", default=str(DEFAULT_INJECT_DIR))
    parser.add_argument("--inject_report", default=str(DEFAULT_INJECT_REPORT))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--comparison_out", default=str(DEFAULT_COMPARISON_PATH))
    parser.add_argument("--cache_dir", default=str(DEFAULT_FEATURE_CACHE))
    parser.add_argument("--grid_step", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test_size", type=float, default=0.3)
    parser.add_argument("--no_propagate_to_deep22", action="store_true",
                        help="Не копировать новый calibrated_weights.json в "
                             "DEEP-22 canonical-путь.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_dir = Path(args.corpus_dir).expanduser().resolve()
    inject_dir = Path(args.inject_dir).expanduser().resolve()
    inject_report = Path(args.inject_report).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    comparison_path = Path(args.comparison_out).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print(f"[DEEP-31] F-Droid v2 corpus: {corpus_dir}", flush=True)
    print(f"[DEEP-31] DEEP-30 inject dir: {inject_dir}", flush=True)
    print(f"[DEEP-31] DEEP-30 report: {inject_report}", flush=True)
    print(f"[DEEP-31] feature cache: {cache_dir}", flush=True)

    # 1. Build F-Droid v2 pairs.
    fdroid_pairs = build_fdroid_v2_pairs(corpus_dir, cache_dir=cache_dir,
                                          seed=args.seed)
    n_fd_clone = sum(1 for _, l in fdroid_pairs if l == 1)
    n_fd_non = len(fdroid_pairs) - n_fd_clone
    print(f"[DEEP-31] F-Droid v2 pairs: {n_fd_clone} clone, {n_fd_non} non-clone",
          flush=True)

    # 2. Build DEEP-30 inject pairs (опционально, если артефакт есть).
    inject_pairs: list[tuple[dict, int]] = []
    if inject_report.exists():
        inject_pairs = build_deep30_inject_pairs(
            inject_report,
            corpus_dir=corpus_dir, inject_dir=inject_dir,
            cache_dir=cache_dir,
        )
        print(f"[DEEP-31] DEEP-30 inject-пары: {len(inject_pairs)} clone",
              flush=True)
    else:
        print(f"[DEEP-31] WARNING: inject report не найден, без DEEP-30",
              flush=True)

    # 3. Combine.
    pairs = fdroid_pairs + inject_pairs
    n_clone = sum(1 for _, l in pairs if l == 1)
    n_non = len(pairs) - n_clone
    print(f"[DEEP-31] total pairs: {n_clone} clone, {n_non} non-clone",
          flush=True)
    train_set_composition = {
        "fdroid_v2_clone": n_fd_clone,
        "fdroid_v2_non_clone": n_fd_non,
        "deep30_inject_clone": len(inject_pairs),
        "hint30_r8_clone": 0,  # см. README — mock-режим
        "total_clone": n_clone,
        "total_non_clone": n_non,
        "total_pairs": len(pairs),
    }

    # 4. Calibrate.
    print(f"[DEEP-31] grid-search step={args.grid_step} ...", flush=True)
    calibration = calibrate_layer_weights_mixed(
        pairs,
        grid_step=args.grid_step,
        seed=args.seed,
        test_size=args.test_size,
    )
    print(f"[DEEP-31] weights: {calibration['weights']}", flush=True)
    print(f"[DEEP-31] train_F1={calibration['train_F1']:.4f} "
          f"test_F1={calibration['test_F1']:.4f} "
          f"threshold={calibration['threshold']:.4f}", flush=True)
    print(f"[DEEP-31] weight_delta_vs_deep27: "
          f"{calibration['weight_delta_vs_deep27']}", flush=True)

    # 5. Build payload + write artifact.
    n_documents = sum(1 for _ in corpus_dir.rglob("*.apk"))
    payload = build_payload(calibration, n_documents=n_documents,
                              train_set_composition=train_set_composition)
    write_payload(payload, out_path)
    write_comparison_with_deep27(calibration, comparison_path)
    print(f"[DEEP-31] wrote {out_path}", flush=True)
    print(f"[DEEP-31] wrote {comparison_path}", flush=True)

    if not args.no_propagate_to_deep22:
        copy_to_deep22_canonical(payload)
        print(f"[DEEP-31] propagated to DEEP-22 canonical: {DEEP22_PROD_PATH}",
              flush=True)


if __name__ == "__main__":
    main()

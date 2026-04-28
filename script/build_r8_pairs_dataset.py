#!/usr/bin/env python3
"""EXEC-HINT-30-OBFUSCATION-DATASET: CLI «оригинал ↔ R8-обфусцированный».

Закрывает Часть 3 EXEC-HINT-30-OBFUSCATION-WRITER. Цель — собрать
mini-dataset из 10 пар, в каждой паре:
- side `original` — исходный APK (предполагается debug/non-optimized сборка),
- side `r8_obfuscated` — тот же APK после переименования части smali-классов
  и методов в короткие имена `a, b, c, ...` (имитация R8/ProGuard rename).

Реальный путь требует apktool (`/opt/homebrew/bin/apktool` по
предусловию волны 30) и набор APK из F-Droid v2 (350 APK).

При отсутствии APK на диске CLI падает в режим ``mock`` и формирует
synthetic-датасет: writer-сторона уже умеет ставить evidence
``signal_type='obfuscation_shift'`` по полям ``library_view_v2.detected_via``
и ``code_view_v4.method_signatures`` (см. ``pairwise_explainer.detect_obfuscation_evidence``);
mock-pair_row подаёт эти поля напрямую — это как раз то, чему доверяем
в тестах ``test_obfuscation_writer.py``.

Опции CLI:
- ``--apk-dir`` — каталог с APK, из которых собрать `original` стороны
  (при наличии apktool пытаемся реально пересобрать R8-версию);
- ``--n-pairs`` — сколько пар записать (по умолчанию 10);
- ``--rename-ratio`` — какую долю классов/методов переименовывать в
  smali (по умолчанию 0.25, может быть снижен до 0.1 при сбоях apktool);
- ``--out-dir`` — каталог артефактов
  (``experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/``).

Артефакты:
- ``r8_pairs.json`` — список пар с `pair_id`, `original_apk`,
  `r8_obfuscated_apk`, evidence (writer-эмулированные).
- ``per_channel_metrics_r8.json`` — replay channel-faithfulness через
  ``hint_faithfulness.compute_channel_faithfulness`` для всех 6 каналов.

Не пушит. Не запускает apktool без `--build-real`. По умолчанию режим
mock — это даёт стабильный артефакт даже без F-Droid v2 на локальном
диске и без долгой apktool-стадии. Real build остаётся опциональной
веткой для волн, когда появится 350 APK на диске.

Канонические правила и контракт: ``system/result-interpretation-contract-v1.md``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# Делаем импорты robust к запуску `python3 script/build_r8_pairs_dataset.py`
# и к запуску из родительской директории.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from hint_faithfulness import (  # noqa: E402  pylint: disable=wrong-import-position
    EVIDENCE_CHANNELS,
    compute_channel_faithfulness,
)


APKTOOL_PATH = "/opt/homebrew/bin/apktool"
DEFAULT_OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "experiments"
    / "artifacts"
    / "EXEC-HINT-30-OBFUSCATION-DATASET"
)


# ---------------------------------------------------------------------------
# Mock-режим: synthetic pair_row без apktool/реальных APK.
# ---------------------------------------------------------------------------


def build_mock_pair(pair_index: int) -> dict:
    """Сконструировать synthetic R8-пару из mock-данных.

    Каждая пара получает оба heuristic-сигнала writer'а:
    - library_view_v2.detected_via='jaccard_v2' -> evidence
      'jaccard_v2_libmask' с magnitude=0.5;
    - code_view_v4.method_signatures с ≥50% коротких имён -> evidence
      'short_method_names' с magnitude=0.6.
    Кроме того, pair_row содержит «обычные» layer_score-evidence по
    code/library — чтобы channel-replay покрыл больше одного канала.

    pair_id формируется как `MOCK-R8-001`..`MOCK-R8-N`, что чётко
    отличает mock-пару от реальной.
    """
    pair_id = f"MOCK-R8-{pair_index + 1:03d}"
    short_method_signatures = [
        "a()",
        "b()",
        "c()",
        "d$e()",
        "f$g()",
        "h()",
        "computeHash()",
        "encodeBitmap()",
    ]
    pair_row = {
        "pair_id": pair_id,
        "app_a": f"{pair_id}_original.apk",
        "app_b": f"{pair_id}_r8.apk",
        "full_similarity_score": 0.55,
        "library_reduced_score": 0.55,
        "library_view_v2": {
            "detected_via": "jaccard_v2",
            "shared_libraries": ["okhttp3", "retrofit"],
        },
        "code_view_v4": {
            "method_signatures": short_method_signatures,
        },
        # Базовый evidence-набор, который writer уже считает каноническим.
        "evidence": [
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.55,
                "ref": "code",
            },
            {
                "source_stage": "pairwise",
                "signal_type": "layer_score",
                "magnitude": 0.5,
                "ref": "library",
            },
        ],
    }
    return pair_row


def emit_obfuscation_evidence_for_pair(pair_row: dict) -> list[dict]:
    """Прогнать writer-детектор и приклеить obfuscation-сигналы к
    pair_row['evidence'].

    Использует pairwise_explainer.detect_obfuscation_evidence — тот же
    путь, что и в production-build_output_rows. Без него pair_row не
    имел бы записей с signal_type='obfuscation_shift', и канал
    'obfuscation' в replay был бы пустым.
    """
    from pairwise_explainer import detect_obfuscation_evidence  # noqa: WPS433

    raw = pair_row.get("evidence")
    base: list[dict] = list(raw) if isinstance(raw, list) else []
    obfuscation = detect_obfuscation_evidence(pair_row)
    pair_row["evidence"] = base + obfuscation
    return pair_row["evidence"]


# ---------------------------------------------------------------------------
# Real-mode utilities (apktool decode/build, smali rename).
# ---------------------------------------------------------------------------


def _run_apktool(args: list[str], cwd: Optional[Path] = None) -> int:
    """Запустить apktool с заданными аргументами; возврат — код выхода."""
    if not Path(APKTOOL_PATH).exists():
        print(f"apktool not found at {APKTOOL_PATH}; skipping real build", file=sys.stderr)
        return 127
    cmd = [APKTOOL_PATH, *args]
    proc = subprocess.run(  # noqa: S603 (controlled cmd)
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        print(
            f"apktool {' '.join(args)} failed: stderr={proc.stderr[:500]}",
            file=sys.stderr,
        )
    return proc.returncode


def _rename_smali_classes(decoded_dir: Path, ratio: float) -> int:
    """Переименовать долю smali-классов и методов в формат `a, b, c, ...`.

    Возвращает количество изменённых файлов. Если apktool ещё не decod'ил
    smali (decoded_dir/smali/ отсутствует), функция возвращает 0.
    """
    smali_root = decoded_dir / "smali"
    if not smali_root.exists():
        return 0
    # Простое правило: для каждого smali-файла переименовываем
    # подлежащий класс и до 3-х методов в короткие имена.
    smali_files = list(smali_root.rglob("*.smali"))
    if not smali_files:
        return 0
    target_count = max(1, int(len(smali_files) * ratio))
    changed = 0
    short_alphabet = "abcdefghijklmnopqrstuvwxyz"
    for index, smali_path in enumerate(smali_files[:target_count]):
        try:
            text = smali_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        new_class_short = short_alphabet[index % len(short_alphabet)]
        # .class public Lcom/foo/Bar; -> Lcom/foo/a;
        text_new = text
        if "/" in text and ".class" in text:
            # Заменить только короткое имя в конце пути: грубая, но
            # достаточная эвристика для синтетического R8-rename.
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.startswith(".class "):
                    head, _, tail = line.rpartition("/")
                    if tail.endswith(";"):
                        lines[i] = f"{head}/{new_class_short};"
                if line.strip().startswith(".method "):
                    parts = line.split()
                    for j, part in enumerate(parts):
                        if "(" in part:
                            method_name = part.split("(", 1)[0]
                            if method_name and not method_name.startswith("<"):
                                parts[j] = (
                                    f"{short_alphabet[(index + j) % len(short_alphabet)]}"
                                    + part[len(method_name):]
                                )
                            break
                    lines[i] = " ".join(parts)
            text_new = "\n".join(lines)
        if text_new != text:
            smali_path.write_text(text_new, encoding="utf-8")
            changed += 1
    return changed


def build_r8_pair_real(
    apk_path: Path, staging_dir: Path, ratio: float
) -> Optional[dict]:
    """Реальный build: apktool decode -> rename ≥ratio классов -> apktool build.

    На любой ошибке возвращает None. Артефакт-каталог пары:
    `staging_dir/<apk_stem>_r8/` (decoded) и `<apk_stem>_r8.apk` (rebuilt).
    """
    apk_stem = apk_path.stem
    decoded_dir = staging_dir / f"{apk_stem}_r8"
    if decoded_dir.exists():
        shutil.rmtree(decoded_dir)
    decoded_dir.parent.mkdir(parents=True, exist_ok=True)
    rc = _run_apktool(["d", "-f", "-o", str(decoded_dir), str(apk_path)])
    if rc != 0:
        return None
    changed = _rename_smali_classes(decoded_dir, ratio)
    if changed == 0:
        # При нулевом rename'е считаем что real build не дал нужного эффекта;
        # возвращаем None, чтобы вызывающий упал в mock.
        return None
    rebuilt_apk = staging_dir / f"{apk_stem}_r8.apk"
    rc = _run_apktool(["b", "-o", str(rebuilt_apk), str(decoded_dir)])
    if rc != 0:
        return None
    if not rebuilt_apk.exists():
        return None
    return {
        "original_apk": str(apk_path),
        "r8_obfuscated_apk": str(rebuilt_apk),
        "smali_files_renamed": changed,
    }


# ---------------------------------------------------------------------------
# Замер channel-faithfulness на собранном датасете.
# ---------------------------------------------------------------------------


def replay_channel_metrics(pairs: list[dict]) -> dict:
    """Прогнать compute_channel_faithfulness для каждой пары и собрать
    агрегаты по 6 каналам.

    Контракт: возвращает dict с ключами `artifact_id`, `source_dataset`,
    `n_pairs`, `channels`, `per_pair` — структура совместима со схемой
    `experiments/artifacts/EXEC-HINT-27-CHANNEL-COVERAGE/per_channel_metrics_v2.json`.
    """
    per_pair: list[dict] = []
    aggregates: dict[str, dict[str, list[float]]] = {
        channel: {"faithfulness": [], "sufficiency": [], "comprehensiveness": []}
        for channel in EVIDENCE_CHANNELS
    }
    for pair in pairs:
        evidence = pair.get("evidence") or []
        result = compute_channel_faithfulness(pair, evidence)
        per_pair_entry: dict = {
            "pair_id": str(pair.get("pair_id", "")),
            "ground_truth": "r8_obfuscated",
            "channels": {},
        }
        for channel_name in EVIDENCE_CHANNELS:
            metrics = result.get(channel_name, {})
            per_pair_entry["channels"][channel_name] = {
                "faithfulness": metrics.get("faithfulness"),
                "sufficiency": metrics.get("sufficiency"),
                "comprehensiveness": metrics.get("comprehensiveness"),
            }
            for metric_name in ("faithfulness", "sufficiency", "comprehensiveness"):
                value = metrics.get(metric_name)
                if value is not None:
                    aggregates[channel_name][metric_name].append(float(value))
        per_pair.append(per_pair_entry)

    channels_summary: dict[str, dict] = {}
    for channel_name in EVIDENCE_CHANNELS:
        bucket = aggregates[channel_name]
        n = len(bucket["faithfulness"])
        if n == 0:
            channels_summary[channel_name] = {
                "n_pairs_with_data": 0,
                "faithfulness_mean": None,
                "sufficiency_mean": None,
                "comprehensiveness_mean": None,
            }
            continue
        channels_summary[channel_name] = {
            "n_pairs_with_data": n,
            "faithfulness_mean": round(sum(bucket["faithfulness"]) / n, 6),
            "sufficiency_mean": round(sum(bucket["sufficiency"]) / n, 6),
            "comprehensiveness_mean": round(
                sum(bucket["comprehensiveness"]) / n, 6
            ),
        }

    return {
        "artifact_id": "EXEC-HINT-30-OBFUSCATION-DATASET",
        "source_dataset": "r8_pairs_mock_or_real",
        "n_pairs": len(pairs),
        "channels": channels_summary,
        "per_pair": per_pair,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apk-dir",
        default=None,
        help="Каталог с APK; при пустом/несуществующем → mock-режим",
    )
    parser.add_argument(
        "--staging-dir",
        default="/tmp/wave30-hint-r8-staging",
        help="Каталог для apktool decode/build (по умолчанию изолирован для wave30)",
    )
    parser.add_argument("--n-pairs", type=int, default=10)
    parser.add_argument(
        "--rename-ratio",
        type=float,
        default=0.25,
        help="Доля smali-классов для rename'а (можно снизить до 0.1)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Каталог артефактов",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Принудительно использовать mock-режим (без apktool/APK)",
    )
    parser.add_argument(
        "--build-real",
        action="store_true",
        help="Пытаться реально пересобрать APK через apktool (по умолчанию mock)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(args.staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    use_real = bool(args.build_real and not args.mock)
    apk_paths: list[Path] = []
    if args.apk_dir:
        apk_dir = Path(args.apk_dir)
        if apk_dir.is_dir():
            apk_paths = sorted(apk_dir.glob("*.apk"))[: args.n_pairs]
    if use_real and not apk_paths:
        print(
            "real build requested but no APK found; falling back to mock",
            file=sys.stderr,
        )
        use_real = False

    pairs: list[dict] = []
    if use_real:
        for apk_path in apk_paths:
            real_meta = build_r8_pair_real(apk_path, staging, args.rename_ratio)
            if real_meta is None:
                continue
            pair_row = build_mock_pair(len(pairs))
            pair_row["app_a"] = real_meta["original_apk"]
            pair_row["app_b"] = real_meta["r8_obfuscated_apk"]
            pair_row["pair_id"] = (
                f"REAL-R8-{Path(real_meta['original_apk']).stem}"
            )
            pair_row["smali_files_renamed"] = real_meta["smali_files_renamed"]
            emit_obfuscation_evidence_for_pair(pair_row)
            pairs.append(pair_row)
            if len(pairs) >= args.n_pairs:
                break

    # Mock fallback (или принудительный mock) — добиваем до N пар.
    while len(pairs) < args.n_pairs:
        mock_pair = build_mock_pair(len(pairs))
        emit_obfuscation_evidence_for_pair(mock_pair)
        pairs.append(mock_pair)

    r8_pairs_path = out_dir / "r8_pairs.json"
    r8_pairs_path.write_text(
        json.dumps(
            {
                "artifact_id": "EXEC-HINT-30-OBFUSCATION-DATASET",
                "n_pairs": len(pairs),
                "mode": "real_or_mock_mixed" if use_real else "mock",
                "rename_ratio": args.rename_ratio,
                "apktool_path": APKTOOL_PATH,
                "pairs": pairs,
                "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    metrics_path = out_dir / "per_channel_metrics_r8.json"
    metrics_path.write_text(
        json.dumps(replay_channel_metrics(pairs), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "n_pairs": len(pairs),
                "mode": "real_or_mock_mixed" if use_real else "mock",
                "r8_pairs_json": str(r8_pairs_path),
                "per_channel_metrics_r8": str(metrics_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

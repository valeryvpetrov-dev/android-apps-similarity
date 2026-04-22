"""Замер качества детекции LIBLOOM на малом размеченном подкорпусе.

EXEC-LIBLOOM-QUALITY-SMOKE (волна 13).

Задача: померить precision/recall/F1 детекции сторонних библиотек
через LIBLOOM на маленьком корпусе APK с известным составом TPL.
До волны 13 были только замеры скорости (~0.56 с на APK),
замера качества не было — это фундаментальный пробел по R-02.

CLI:
    python -m script.run_libloom_quality_smoke \\
        --apk-list apks.txt \\
        --catalog-dir /path/to/libloom_libs_profile \\
        --jar-path /path/to/LIBLOOM.jar \\
        --output report.json

Формат файла --apk-list — JSON со списком записей:
    [
      {"apk_path": "apk/simple_app/simple_app-empty.apk",
       "ground_truth": [],
       "notes": "empty SimpleApplication без dependencies"},
      ...
    ]

Поле ground_truth — список канонических имён библиотек из top-20
каталога (например, "okhttp", "retrofit", "gson"). Пустой список
допустим: тогда precision/recall для записи считаются по пересечению
с предсказаниями (пустой ground-truth даёт recall=1.0 по соглашению).

Если LIBLOOM недоступен (нет JAR, нет java, нет каталога) — скрипт
не падает: записывает status=partial и reason, возвращает код 2.

Артефакт детекции сохраняется как JSON-отчёт; простая агрегация
corpus_precision/corpus_recall/corpus_f1 считается по micro-averaging
(сумма TP/FP/FN по всему корпусу).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from script import libloom_adapter
except ImportError:
    # Разрешает запуск как `python script/run_libloom_quality_smoke.py`.
    import importlib.util
    _adapter_path = Path(__file__).resolve().parent / "libloom_adapter.py"
    _spec = importlib.util.spec_from_file_location(
        "libloom_adapter", str(_adapter_path)
    )
    libloom_adapter = importlib.util.module_from_spec(_spec)  # type: ignore[assignment]
    assert _spec.loader is not None
    _spec.loader.exec_module(libloom_adapter)  # type: ignore[union-attr]


def parse_apk_list(apk_list_path: str) -> list[dict[str, Any]]:
    """Разобрать файл со списком APK и их ground-truth.

    Формат: JSON-массив, каждый элемент — объект с полями
    apk_path (str, обязательно), ground_truth (list[str]),
    notes (str, необязательно).

    При ошибках парсинга или отсутствии ключей возбуждает ValueError
    с понятным сообщением.
    """
    p = Path(apk_list_path)
    if not p.is_file():
        raise ValueError(f"apk-list file not found: {apk_list_path}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"apk-list not a valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("apk-list must be a JSON array")
    entries: list[dict[str, Any]] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"apk-list entry #{i} is not a JSON object")
        apk_path = raw.get("apk_path")
        if not isinstance(apk_path, str) or not apk_path:
            raise ValueError(f"apk-list entry #{i} missing 'apk_path'")
        gt_raw = raw.get("ground_truth", [])
        if not isinstance(gt_raw, list):
            raise ValueError(
                f"apk-list entry #{i} 'ground_truth' must be a list"
            )
        ground_truth = [str(x).lower() for x in gt_raw]
        notes = str(raw.get("notes", ""))
        entries.append(
            {
                "apk_path": apk_path,
                "ground_truth": ground_truth,
                "notes": notes,
            }
        )
    return entries


def compute_prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Посчитать precision, recall, F1 из целочисленных счётчиков.

    По соглашению: если предсказания пусты и ground-truth пуст, то
    precision=1.0, recall=1.0, F1=1.0 (идеальное согласие на пустоте).
    Если предсказания пусты, а ground-truth не пуст — precision=0.0,
    recall=0.0. Если предсказания не пусты, а ground-truth пуст —
    precision=0.0, recall=1.0 по соглашению (ничего не должно было быть
    найдено, но был шум).
    """
    if tp + fp + fn == 0:
        return 1.0, 1.0, 1.0
    if tp + fp == 0:
        precision = 0.0
    else:
        precision = tp / (tp + fp)
    if tp + fn == 0:
        # Пустой ground-truth — recall по соглашению 1.0.
        recall = 1.0
    else:
        recall = tp / (tp + fn)
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def _canon(name: str) -> str:
    """Привести имя библиотеки к каноническому виду для сравнения.

    Нижний регистр + срезать типичные суффиксы артефактов.
    """
    s = str(name).lower().strip()
    # Срезаем типичные суффиксы платформо-зависимых артефактов.
    for suf in ("-android", "-jvm", "-runtime", "-core"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def score_entry(
    predicted: list[str],
    ground_truth: list[str],
) -> dict[str, Any]:
    """Посчитать TP/FP/FN и PRF для одного APK.

    Сравнение канонизированное: _canon(name) для обеих сторон.
    """
    gt_canon = {_canon(x) for x in ground_truth}
    pred_canon = {_canon(x) for x in predicted}
    tp_set = gt_canon & pred_canon
    fp_set = pred_canon - gt_canon
    fn_set = gt_canon - pred_canon
    tp, fp, fn = len(tp_set), len(fp_set), len(fn_set)
    precision, recall, f1 = compute_prf(tp, fp, fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_names": sorted(tp_set),
        "fp_names": sorted(fp_set),
        "fn_names": sorted(fn_set),
    }


def run_corpus(
    entries: list[dict[str, Any]],
    catalog_dir: str,
    jar_path: str,
    timeout_sec: int,
    java_heap_mb: int,
) -> dict[str, Any]:
    """Прогнать LIBLOOM по всем записям и собрать corpus-агрегаты.

    Возвращает отчёт с полями
    - status: "done" | "partial"
    - reason: str | None (причина partial)
    - per_apk: list[dict] с результатом по каждому APK
    - corpus: dict с tp/fp/fn/precision/recall/f1 по сумме
    - meta: catalog_dir, jar_path, corpus_size
    """
    # Ранняя проверка: каталог.
    catalog_path = Path(catalog_dir)
    if not catalog_path.is_dir() or not any(catalog_path.iterdir()):
        return {
            "status": "partial",
            "reason": "catalog_missing_or_empty",
            "per_apk": [],
            "corpus": {
                "tp": 0, "fp": 0, "fn": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
            },
            "meta": {
                "catalog_dir": str(catalog_dir),
                "jar_path": str(jar_path),
                "corpus_size": len(entries),
            },
        }
    # Ранняя проверка: JAR.
    if not libloom_adapter.libloom_available(jar_path):
        return {
            "status": "partial",
            "reason": "libloom_jar_or_java_missing",
            "per_apk": [],
            "corpus": {
                "tp": 0, "fp": 0, "fn": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
            },
            "meta": {
                "catalog_dir": str(catalog_dir),
                "jar_path": str(jar_path),
                "corpus_size": len(entries),
            },
        }

    per_apk: list[dict[str, Any]] = []
    total_tp = total_fp = total_fn = 0
    had_any_ok = False
    for entry in entries:
        apk_path = entry["apk_path"]
        gt = entry["ground_truth"]
        notes = entry.get("notes", "")
        detect_res = libloom_adapter.detect_libraries(
            apk_path=apk_path,
            jar_path=jar_path,
            libs_profile_dir=str(catalog_dir),
            timeout_sec=timeout_sec,
            java_heap_mb=java_heap_mb,
        )
        predicted_names = [
            lib.get("name", "")
            for lib in detect_res.get("libraries", [])
            if lib.get("name")
        ]
        scored = score_entry(predicted_names, gt)
        per_apk.append(
            {
                "apk_path": apk_path,
                "notes": notes,
                "ground_truth": gt,
                "predicted": predicted_names,
                "detect_status": detect_res.get("status"),
                "detect_error_reason": detect_res.get("error_reason"),
                "elapsed_sec": detect_res.get("elapsed_sec", 0.0),
                **scored,
            }
        )
        if detect_res.get("status") == "ok":
            had_any_ok = True
            total_tp += scored["tp"]
            total_fp += scored["fp"]
            total_fn += scored["fn"]

    corpus_p, corpus_r, corpus_f1 = compute_prf(total_tp, total_fp, total_fn)
    status = "done" if had_any_ok else "partial"
    reason = None if had_any_ok else "no_apk_scored_successfully"
    return {
        "status": status,
        "reason": reason,
        "per_apk": per_apk,
        "corpus": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": corpus_p,
            "recall": corpus_r,
            "f1": corpus_f1,
        },
        "meta": {
            "catalog_dir": str(catalog_dir),
            "jar_path": str(jar_path),
            "corpus_size": len(entries),
        },
    }


def write_report(report: dict[str, Any], output_path: str) -> None:
    """Сохранить отчёт в JSON, создавая родительскую папку при нужде."""
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with out_p.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)


def build_arg_parser() -> argparse.ArgumentParser:
    """Собрать argparse для CLI."""
    p = argparse.ArgumentParser(
        description=(
            "LIBLOOM quality smoke: precision/recall/F1 на маленьком "
            "размеченном подкорпусе APK (EXEC-LIBLOOM-QUALITY-SMOKE)."
        )
    )
    p.add_argument(
        "--apk-list",
        required=True,
        help="путь к JSON-файлу со списком APK и их ground_truth",
    )
    p.add_argument(
        "--catalog-dir",
        required=True,
        help="каталог с prebuilt LIBLOOM-профилями (libloom_libs_profile)",
    )
    p.add_argument(
        "--jar-path",
        required=True,
        help="путь к LIBLOOM.jar",
    )
    p.add_argument(
        "--output",
        default=None,
        help="путь для сохранения JSON-отчёта; если не задан — stdout",
    )
    p.add_argument(
        "--timeout-sec",
        type=int,
        default=libloom_adapter.DEFAULT_TIMEOUT_SEC,
        help="таймаут одной фазы LIBLOOM (default=%(default)s)",
    )
    p.add_argument(
        "--java-heap-mb",
        type=int,
        default=libloom_adapter.DEFAULT_JAVA_HEAP_MB,
        help="JVM heap MB (default=%(default)s)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Точка входа. Возвращает 0 (done), 2 (partial), 1 (argparse/IO error)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        entries = parse_apk_list(args.apk_list)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    report = run_corpus(
        entries=entries,
        catalog_dir=args.catalog_dir,
        jar_path=args.jar_path,
        timeout_sec=args.timeout_sec,
        java_heap_mb=args.java_heap_mb,
    )
    if args.output:
        write_report(report, args.output)
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0 if report["status"] == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())

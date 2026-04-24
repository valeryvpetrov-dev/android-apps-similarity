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
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from script import libloom_adapter
    from script import noise_profile_envelope
    from script import system_requirements
except ImportError:
    # Разрешает запуск как `python script/run_libloom_quality_smoke.py`.
    import importlib.util

    def _load_local_module(module_name: str, file_name: str) -> Any:
        module_path = Path(__file__).resolve().parent / file_name
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    libloom_adapter = _load_local_module("libloom_adapter", "libloom_adapter.py")
    noise_profile_envelope = _load_local_module(
        "noise_profile_envelope",
        "noise_profile_envelope.py",
    )
    system_requirements = _load_local_module(
        "system_requirements",
        "system_requirements.py",
    )


REAL_RUN_ID = "EXEC-LIBLOOM-QUALITY-REAL-RUN"
DEFAULT_APK_DIR = str(Path(__file__).resolve().parent.parent / "apk")
DEFAULT_REAL_OUTPUT = str(
    Path(__file__).resolve().parent.parent
    / "experiments"
    / "artifacts"
    / REAL_RUN_ID
    / "report.json"
)
INLINE_MINI_LABELS = {
    "simple_app-empty.apk": [],
    "simple_app-releaseNonOptimized.apk": [],
    "simple_app-releaseOptimized.apk": [],
    "simple_app-releaseRename.apk": [],
    "snake.apk": [],
}
INLINE_TRACKED_TPLS = (
    "okhttp3",
    "gson",
    "retrofit",
    "glide",
    "kotlinx-coroutines",
)
SOURCE_PATH = "script/run_libloom_quality_smoke.py"


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
    aliases = {
        "okhttp": "okhttp3",
        "com.squareup.okhttp3": "okhttp3",
        "retrofit2": "retrofit",
        "com.squareup.retrofit2": "retrofit",
        "com.google.gson": "gson",
        "kotlinx_coroutines": "kotlinx-coroutines",
        "kotlinx.coroutines": "kotlinx-coroutines",
    }
    if s in aliases:
        return aliases[s]
    for prefix, normalized in (
        ("com.squareup.okhttp3.", "okhttp3"),
        ("com.squareup.retrofit2.", "retrofit"),
        ("com.google.gson.", "gson"),
        ("com.bumptech.glide.", "glide"),
        ("kotlinx.coroutines.", "kotlinx-coroutines"),
    ):
        if s.startswith(prefix):
            return normalized
    # Срезаем типичные суффиксы платформо-зависимых артефактов.
    for suf in ("-android", "-jvm", "-runtime", "-core"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return aliases.get(s, s)


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


def discover_apks(apk_dir: str) -> list[str]:
    """Найти APK в каталоге рекурсивно и вернуть отсортированный список путей."""
    root = Path(apk_dir)
    if not root.is_dir():
        raise ValueError(f"apk-dir not found: {apk_dir}")
    return sorted(str(path) for path in root.rglob("*.apk") if path.is_file())


def probe_libloom_runtime() -> dict[str, Any]:
    """Проверить доступность LIBLOOM без выброса исключений."""
    warnings: list[str] = []
    libloom_home = os.environ.get(system_requirements.LIBLOOM_HOME_ENV_VAR, "").strip()
    if not libloom_home:
        return {
            "available": False,
            "jar_path": None,
            "libs_profile_dir": None,
            "warnings": ["LIBLOOM_HOME is not set"],
        }

    jar_path = Path(libloom_home) / system_requirements.LIBLOOM_JAR_NAME
    if not jar_path.is_file():
        warnings.append(f"LIBLOOM.jar not found at {jar_path}")

    libs_profile_dir = Path(libloom_home) / system_requirements.LIBLOOM_PROFILE_DIR_NAME
    if not libs_profile_dir.is_dir():
        warnings.append(f"libs_profile not found at {libs_profile_dir}")
    else:
        try:
            if not any(libs_profile_dir.iterdir()):
                warnings.append(f"libs_profile is empty at {libs_profile_dir}")
        except OSError:
            warnings.append(f"libs_profile is unreadable at {libs_profile_dir}")

    if shutil.which("java") is None:
        warnings.append("java is not available on PATH")

    return {
        "available": not warnings,
        "jar_path": str(jar_path),
        "libs_profile_dir": str(libs_profile_dir),
        "warnings": warnings,
    }


def _build_labels_lookup(
    labels_path: str | None,
) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    if labels_path is None:
        lookup = {
            apk_name: {
                "ground_truth": list(values),
                "notes": "inline-mini-labels",
            }
            for apk_name, values in INLINE_MINI_LABELS.items()
        }
        return lookup, list(INLINE_TRACKED_TPLS), "inline-mini-labels"

    raw_entries = parse_apk_list(labels_path)
    lookup: dict[str, dict[str, Any]] = {}
    labeled_tpls: set[str] = set()
    for entry in raw_entries:
        apk_key = str(Path(entry["apk_path"]).name)
        lookup[apk_key] = {
            "ground_truth": list(entry.get("ground_truth", [])),
            "notes": entry.get("notes", ""),
        }
        labeled_tpls.update(_canon(name) for name in entry.get("ground_truth", []))
    return lookup, sorted(labeled_tpls), str(labels_path)


def _build_quality_entries(
    apk_paths: list[str],
    labels_path: str | None,
) -> tuple[list[dict[str, Any]], list[str], str]:
    labels_lookup, labeled_tpls, labels_source = _build_labels_lookup(labels_path)
    entries: list[dict[str, Any]] = []
    for apk_path in apk_paths:
        label_entry = labels_lookup.get(Path(apk_path).name, {})
        entries.append(
            {
                "apk_path": apk_path,
                "ground_truth": list(label_entry.get("ground_truth", [])),
                "notes": str(label_entry.get("notes", "")),
            }
        )
    return entries, labeled_tpls, labels_source


def _empty_real_run_report(
    apk_dir: str,
    corpus_size: int,
    labeled_tpls: list[str],
    labels_source: str,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": REAL_RUN_ID,
        "status": "libloom_unavailable",
        "corpus_size": corpus_size,
        "labeled_tpls": labeled_tpls,
        "per_apk_results": [],
        "aggregate": {
            "precision": 0.0,
            "recall": 0.0,
            "coverage": 0.0,
        },
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "warnings": list(runtime.get("warnings", [])),
        "source": {
            "script": SOURCE_PATH,
            "apk_dir": apk_dir,
            "labels": labels_source,
            "jar_path": runtime.get("jar_path"),
            "libs_profile_dir": runtime.get("libs_profile_dir"),
        },
    }


def run_real_quality_smoke(
    apk_dir: str = DEFAULT_APK_DIR,
    labels_path: str | None = None,
    output_path: str | None = DEFAULT_REAL_OUTPUT,
    timeout_sec: int = libloom_adapter.DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Собрать первый real-run отчёт качества LIBLOOM по APK-подкорпусу."""
    apk_paths = discover_apks(apk_dir)
    entries, labeled_tpls, labels_source = _build_quality_entries(apk_paths, labels_path)
    runtime = probe_libloom_runtime()
    if not runtime["available"]:
        report = _empty_real_run_report(
            apk_dir=apk_dir,
            corpus_size=len(apk_paths),
            labeled_tpls=labeled_tpls,
            labels_source=labels_source,
            runtime=runtime,
        )
        if output_path:
            write_report(report, output_path)
        return report

    total_tp = 0
    total_fp = 0
    total_fn = 0
    covered_apks = 0
    per_apk_results: list[dict[str, Any]] = []
    for entry in entries:
        enriched = noise_profile_envelope.apply_libloom_detection(
            apk_path=entry["apk_path"],
            apkid_result={
                "gate_status": "clean",
                "recommended_detector": "libloom",
                "reason": "quality_real_run",
            },
            libloom_jar_path=runtime["jar_path"],
            libs_profile_dir=runtime["libs_profile_dir"],
            envelope={},
            timeout_sec=timeout_sec,
        )
        detected_tpls = [
            lib.get("name", "")
            for lib in list(enriched.get("libloom_libraries") or [])
            if isinstance(lib, dict) and lib.get("name")
        ]
        scored = score_entry(detected_tpls, entry["ground_truth"])
        total_tp += scored["tp"]
        total_fp += scored["fp"]
        total_fn += scored["fn"]
        if detected_tpls:
            covered_apks += 1
        per_apk_results.append(
            {
                "apk_path": entry["apk_path"],
                "ground_truth": list(entry["ground_truth"]),
                "detected_tpls": sorted({_canon(name) for name in detected_tpls}),
                "libloom_status": enriched.get("libloom_status"),
                "libloom_error_reason": enriched.get("libloom_error_reason"),
                "libloom_elapsed_sec": enriched.get("libloom_elapsed_sec", 0.0),
                **scored,
            }
        )

    precision, recall, _ = compute_prf(total_tp, total_fp, total_fn)
    coverage = covered_apks / len(apk_paths) if apk_paths else 0.0
    report = {
        "run_id": REAL_RUN_ID,
        "status": "ok",
        "corpus_size": len(apk_paths),
        "labeled_tpls": labeled_tpls,
        "per_apk_results": per_apk_results,
        "aggregate": {
            "precision": precision,
            "recall": recall,
            "coverage": coverage,
        },
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "warnings": list(runtime.get("warnings", [])),
        "source": {
            "script": SOURCE_PATH,
            "apk_dir": apk_dir,
            "labels": labels_source,
            "jar_path": runtime.get("jar_path"),
            "libs_profile_dir": runtime.get("libs_profile_dir"),
        },
    }
    if output_path:
        write_report(report, output_path)
    return report


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
            "LIBLOOM quality smoke / real-run: замер качества на APK-корпусе "
            "с мягкой деградацией при недоступном LIBLOOM."
        )
    )
    p.add_argument(
        "--apk-list",
        default=None,
        help="legacy: путь к JSON-файлу со списком APK и их ground_truth",
    )
    p.add_argument(
        "--apk-dir",
        default=DEFAULT_APK_DIR,
        help="каталог APK для real-run режима (default=%(default)s)",
    )
    p.add_argument(
        "--labels-path",
        default=None,
        help="JSON-разметка APK -> ground_truth; если не задано, используется inline mini-labeling",
    )
    p.add_argument(
        "--catalog-dir",
        default=None,
        help="legacy: каталог с prebuilt LIBLOOM-профилями",
    )
    p.add_argument(
        "--jar-path",
        default=None,
        help="legacy: путь к LIBLOOM.jar",
    )
    p.add_argument(
        "--output",
        default=None,
        help="путь для сохранения JSON-отчёта",
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
    """Точка входа.

    Legacy-режим (`--apk-list --catalog-dir --jar-path`) сохраняет старый
    контракт. Новый real-run режим по умолчанию возвращает 0 и пишет
    `status=libloom_unavailable`, если внешняя среда не готова.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    legacy_mode = any(
        value is not None
        for value in (args.apk_list, args.catalog_dir, args.jar_path)
    )
    if legacy_mode:
        if not all((args.apk_list, args.catalog_dir, args.jar_path)):
            print(
                "ERROR: legacy mode requires --apk-list, --catalog-dir and --jar-path",
                file=sys.stderr,
            )
            return 1
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

    try:
        report = run_real_quality_smoke(
            apk_dir=args.apk_dir,
            labels_path=args.labels_path,
            output_path=args.output or DEFAULT_REAL_OUTPUT,
            timeout_sec=args.timeout_sec,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not args.output:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

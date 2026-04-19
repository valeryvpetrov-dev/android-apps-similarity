"""EXEC-083-APKID-SMOKE: реальный прогон apkid_adapter на одном APK.

Запуск:
    python3 script/run_apkid_smoke.py <путь_к_APK>

Пишет JSON-отчёт в
    experiments/artifacts/E-EXEC-083-APKID-SMOKE/smoke-<timestamp>.json

с полями: apkid_version, apk_path, classification (полный результат
detect_classifiers), gate (полный результат decide_gate), timestamp.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from apkid_adapter import apkid_available, detect_classifiers, decide_gate

ARTIFACT_SUBDIR = "E-EXEC-083-APKID-SMOKE"


def _default_output_dir() -> Path:
    """Каталог артефактов от корня субмодуля.

    `experiments/artifacts/E-EXEC-083-APKID-SMOKE/` относительно
    директории на два уровня выше `script/` (корень субмодуля).
    """
    submodule_root = _SCRIPT_DIR.parent
    return submodule_root / "experiments" / "artifacts" / ARTIFACT_SUBDIR


def _build_report(apk_path: str, classification: dict, gate: dict) -> dict:
    """Собирает полный словарь отчёта."""
    return {
        "apkid_version": classification.get("apkid_version"),
        "apk_path": apk_path,
        "classification": classification,
        "gate": gate,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_smoke(apk_path: str, output_dir: Path | None = None) -> dict:
    """Запускает apkid_adapter и сохраняет JSON-отчёт.

    Возвращает полный словарь отчёта. Параллельно пишет его в файл
    `smoke-<timestamp>.json` внутри `output_dir` (или каталог по
    умолчанию, если не задан).

    Если `apkid_available()=False` — отчёт всё равно пишется,
    classification получит `status="not_available"`.
    """
    if output_dir is None:
        output_dir = _default_output_dir()

    if not apkid_available():
        classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": [],
            "anti_debug": [],
            "anti_vm": [],
            "apkid_version": None,
            "rules_sha256": None,
            "status": "not_available",
            "elapsed_sec": 0.0,
            "raw_stdout": None,
        }
    else:
        classification = detect_classifiers(apk_path)

    gate = decide_gate(classification)
    report = _build_report(apk_path, classification, gate)

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = output_dir / f"smoke-{stamp}.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    report["_report_path"] = str(report_path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EXEC-083-APKID-SMOKE: реальный прогон apkid_adapter на одном APK."
    )
    parser.add_argument("apk", help="Путь к APK для smoke-прогона")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Куда писать отчёт (по умолчанию experiments/artifacts/E-EXEC-083-APKID-SMOKE/)",
    )
    args = parser.parse_args()
    result = run_smoke(args.apk, Path(args.output_dir) if args.output_dir else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))

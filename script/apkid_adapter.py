"""APKiD adapter (EXEC-083-APKID-ADAPTER).

Статический классификатор упаковщиков/обфускаторов/компиляторов
на YARA-правилах. Запускается через subprocess. При обнаружении
упаковщика — жёсткая политика блокировки (статус `blocked`).

Установка APKiD в системе: pip install apkid (отдельный шаг).

Дизайн: inbox/research/apkid-gate-design-2026-04-19.md.
Политика: D-2026-04-19 (HARD).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

DEFAULT_TIMEOUT_SEC = 60

# Gate policy (HARD — D-2026-04-19):
PACK_DETECTED_POLICY = "blocked"        # жёсткая: детектор библиотек НЕ запускается
OBFUSCATOR_DETECTED_POLICY = "libloom"  # переключаемся на устойчивый детектор
CLEAN_POLICY = "prefix_catalog"         # дешёвый путь

# Gate status vocabulary
GATE_BLOCKED = "blocked"
GATE_OBFUSCATOR_DETECTED = "obfuscator_detected"
GATE_CLEAN = "clean"

# Recommended detector vocabulary
RECOMMENDED_NONE = "none"
RECOMMENDED_LIBLOOM = "libloom"
RECOMMENDED_PREFIX_CATALOG = "prefix_catalog"


def apkid_available() -> bool:
    """Checks whether apkid is importable or available via PATH."""
    if shutil.which("apkid") is not None:
        return True
    try:
        __import__("apkid")
        return True
    except ImportError:
        return False


def _empty_classification() -> dict:
    """Skeleton of classification result with empty lists and unset status."""
    return {
        "packers": [],
        "obfuscators": [],
        "compilers": [],
        "anti_debug": [],
        "anti_vm": [],
        "apkid_version": None,
        "rules_sha256": None,
        "status": "ok",
        "elapsed_sec": 0.0,
        "raw_stdout": None,
    }


def _parse_apkid_json(payload: dict) -> dict:
    """Extract categories from a parsed APKiD JSON document.

    APKiD JSON schema (v3.1.0) produces:
        {
            "apkid_version": "3.1.0",
            "rules_sha256": "<hash>",
            "files": [
                {"filename": "input.apk",
                 "matches": {"compiler": [...], "obfuscator": [...],
                             "packer": [...], "anti_debug": [...],
                             "anti_vm": [...]}},
                {"filename": "input.apk!classes.dex",
                 "matches": {"compiler": [...]}}
            ]
        }

    Categories are aggregated across all files (root + nested DEX entries)
    so a packer found only in a nested DEX still propagates to the gate.
    """
    result = _empty_classification()
    result["apkid_version"] = payload.get("apkid_version")
    result["rules_sha256"] = payload.get("rules_sha256")

    packers: list[str] = []
    obfuscators: list[str] = []
    compilers: list[str] = []
    anti_debug: list[str] = []
    anti_vm: list[str] = []

    for entry in payload.get("files", []):
        matches = entry.get("matches", {}) or {}
        for key, values in matches.items():
            if not isinstance(values, list):
                continue
            key_lower = key.lower()
            if "packer" in key_lower:
                packers.extend(str(v) for v in values)
            if "obfuscator" in key_lower:
                obfuscators.extend(str(v) for v in values)
            if "compiler" in key_lower:
                compilers.extend(str(v) for v in values)
            if "anti_debug" in key_lower or "anti-debug" in key_lower:
                anti_debug.extend(str(v) for v in values)
            if "anti_vm" in key_lower or "anti-vm" in key_lower:
                anti_vm.extend(str(v) for v in values)

    # Deduplicate while preserving order.
    result["packers"] = list(dict.fromkeys(packers))
    result["obfuscators"] = list(dict.fromkeys(obfuscators))
    result["compilers"] = list(dict.fromkeys(compilers))
    result["anti_debug"] = list(dict.fromkeys(anti_debug))
    result["anti_vm"] = list(dict.fromkeys(anti_vm))
    return result


def detect_classifiers(apk_path: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> dict:
    """Classifies an APK via APKiD.

    Returns: {
        "packers": list[str],
        "obfuscators": list[str],
        "compilers": list[str],
        "anti_debug": list[str],
        "anti_vm": list[str],
        "apkid_version": str | None,
        "rules_sha256": str | None,
        "status": "ok" | "not_available" | "timeout" | "subprocess_error",
        "elapsed_sec": float,
        "raw_stdout": str | None,
    }
    """
    if not apkid_available():
        result = _empty_classification()
        result["status"] = "not_available"
        return result

    cmd = ["apkid", "-j", "-t", str(timeout_sec), apk_path]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = _empty_classification()
        result["status"] = "timeout"
        result["elapsed_sec"] = float(timeout_sec)
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        result = _empty_classification()
        result["status"] = "subprocess_error"
        result["elapsed_sec"] = time.monotonic() - started
        result["raw_stdout"] = None
        result["raw_stderr"] = str(exc)
        return result

    elapsed = time.monotonic() - started
    stdout = proc.stdout or ""

    if proc.returncode != 0:
        result = _empty_classification()
        result["status"] = "subprocess_error"
        result["elapsed_sec"] = elapsed
        result["raw_stdout"] = stdout
        return result

    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        result = _empty_classification()
        result["status"] = "subprocess_error"
        result["elapsed_sec"] = elapsed
        result["raw_stdout"] = stdout
        return result

    parsed = _parse_apkid_json(payload)
    parsed["status"] = "ok"
    parsed["elapsed_sec"] = elapsed
    parsed["raw_stdout"] = stdout
    return parsed


def decide_gate(classification: dict) -> dict:
    """Maps APKiD classification into a gate decision.

    Policy (HARD — D-2026-04-19):
      - packer detected   -> gate_status=blocked, recommended=none
        (детектор библиотек НЕ запускается)
      - obfuscator only   -> gate_status=obfuscator_detected, recommended=libloom
      - clean (no signal) -> gate_status=clean, recommended=prefix_catalog

    Returns: {
        "gate_status": "blocked" | "obfuscator_detected" | "clean",
        "recommended_detector": "none" | "libloom" | "prefix_catalog",
        "reason": str,
        "apkid_signals": {
            "packers": [...], "obfuscators": [...], ...
        }
    }
    """
    packers = list(classification.get("packers", []) or [])
    obfuscators = list(classification.get("obfuscators", []) or [])
    compilers = list(classification.get("compilers", []) or [])
    anti_debug = list(classification.get("anti_debug", []) or [])
    anti_vm = list(classification.get("anti_vm", []) or [])

    signals = {
        "packers": packers,
        "obfuscators": obfuscators,
        "compilers": compilers,
        "anti_debug": anti_debug,
        "anti_vm": anti_vm,
    }

    # Packer — приоритетнее всего (жёсткая политика).
    if packers:
        return {
            "gate_status": GATE_BLOCKED,
            "recommended_detector": RECOMMENDED_NONE,
            "reason": "packer detected: {}".format(", ".join(packers)),
            "apkid_signals": signals,
        }

    if obfuscators:
        return {
            "gate_status": GATE_OBFUSCATOR_DETECTED,
            "recommended_detector": RECOMMENDED_LIBLOOM,
            "reason": "obfuscator detected: {}".format(", ".join(obfuscators)),
            "apkid_signals": signals,
        }

    return {
        "gate_status": GATE_CLEAN,
        "recommended_detector": RECOMMENDED_PREFIX_CATALOG,
        "reason": "no packer/obfuscator detected",
        "apkid_signals": signals,
    }

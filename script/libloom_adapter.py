"""LIBLOOM adapter skeleton (EXEC-083-SEED-B).

Минимальный скелет для будущего вызова LIBLOOM через subprocess.
Реальный jar и JDK 8 — отдельный шаг установки (см.
inbox/research/libloom-integration-plan-2026-04-19.md).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SEC = 600  # 315 сек по публикации + buffer


def libloom_available(jar_path: str) -> bool:
    """Check whether LIBLOOM jar + java are installed."""
    if not Path(jar_path).is_file():
        return False
    return shutil.which("java") is not None


def detect_libraries(
    apk_path: str,
    jar_path: str,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Invoke LIBLOOM on a single APK and return a structured result.

    Returns:
        {
            "libraries": list[dict] with {name, version?, confidence?},
            "unknown_packages": list[str],
            "status": "ok" | "timeout" | "subprocess_error" | "not_available",
            "elapsed_sec": float,
            "raw_stdout": str | None,
            "raw_stderr": str | None,
        }
    """
    if not libloom_available(jar_path):
        return {
            "libraries": [],
            "unknown_packages": [],
            "status": "not_available",
            "elapsed_sec": 0.0,
            "raw_stdout": None,
            "raw_stderr": None,
        }
    # TODO(EXEC-083-FULL): actual `java -jar jar_path detect apk_path` call
    # with timeout handling, JSON parsing, and error mapping.
    raise NotImplementedError(
        "LIBLOOM subprocess call is a follow-up (EXEC-083-FULL). "
        "Skeleton in place; JDK 8 + jar installation required first."
    )

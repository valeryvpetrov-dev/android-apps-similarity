"""LIBLOOM adapter (EXEC-083-FULL).

Запускает LIBLOOM через subprocess в двухфазном режиме (profile → detect)
для детекции сторонних библиотек в APK, устойчивой к class repackaging и
package flattening (R8 non-structure-preserving obfuscations).

LIBLOOM особенности:
- требует `config/parameters.properties` относительно CWD (не рядом с jar) —
  subprocess.run вызывается с cwd=Path(jar_path).parent.
- работает в два шага: profile (строит Bloom-фильтры) и detect (сопоставляет
  профиль APK с каталогом библиотечных профилей).
- JDK 17+ подтверждён; на 1.8.0 и выше работает корректно.
- возвращает JSON вида {"appname": str, "libraries": [{"name", "version", "similarity"}, ...]}.

Дизайн: inbox/research/libloom-integration-plan-2026-04-19.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SEC = 600  # 315 сек по публикации + buffer
DEFAULT_JAVA_HEAP_MB = 2048
LIBLOOM_HOME_ENV_VAR = "LIBLOOM_HOME"
LIBLOOM_JAR_NAME = "LIBLOOM.jar"

_STDOUT_TAIL_BYTES = 4096


def resolve_libloom_jar_path() -> str:
    """Resolve `$LIBLOOM_HOME/LIBLOOM.jar` or raise a clear RuntimeError."""
    libloom_home = os.environ.get(LIBLOOM_HOME_ENV_VAR, "").strip()
    if not libloom_home:
        raise RuntimeError(
            "LIBLOOM_HOME is not set; install LIBLOOM via "
            "`experiments/scripts/setup_libloom.sh` and export LIBLOOM_HOME"
        )

    jar_path = Path(libloom_home) / LIBLOOM_JAR_NAME
    if not jar_path.is_file():
        raise RuntimeError(
            f"LIBLOOM jar not found at {jar_path}; "
            "run `experiments/scripts/setup_libloom.sh` again"
        )
    return str(jar_path)


def libloom_available(jar_path: str | None = None) -> bool:
    """Check whether LIBLOOM jar is a file and java is on PATH.

    If `jar_path` is omitted, it is resolved strictly from `LIBLOOM_HOME`.
    Missing env var or missing jar are configuration errors, not silent fallbacks.
    """
    resolved_jar_path = resolve_libloom_jar_path() if jar_path is None else jar_path
    if not Path(resolved_jar_path).is_file():
        return False
    return shutil.which("java") is not None


def _empty_result() -> dict[str, Any]:
    """Skeleton of detect_libraries result."""
    return {
        "status": "ok",
        "libraries": [],
        "unknown_packages": [],
        "elapsed_sec": 0.0,
        "raw_stdout": None,
        "raw_stderr": None,
        "appname": None,
        "error_reason": None,
    }


def _tail_text(text: str | None) -> str | None:
    """Return the last ~4KB of a text string (or None)."""
    if text is None:
        return None
    if len(text) <= _STDOUT_TAIL_BYTES:
        return text
    return text[-_STDOUT_TAIL_BYTES:]


def _read_first_json(result_dir: Path) -> dict | None:
    """Read and parse the first `.json` file from result_dir, or None."""
    if not result_dir.is_dir():
        return None
    json_files = sorted(result_dir.glob("*.json"))
    if not json_files:
        return None
    try:
        with json_files[0].open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _run_libloom_phase(
    cmd: list[str],
    cwd: Path,
    timeout_sec: int,
) -> subprocess.CompletedProcess:
    """Run one LIBLOOM phase (profile or detect). Raises TimeoutExpired."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )


def detect_libraries(
    apk_path: str,
    jar_path: str,
    libs_profile_dir: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    java_heap_mb: int = DEFAULT_JAVA_HEAP_MB,
) -> dict[str, Any]:
    """Run LIBLOOM on a single APK against a prebuilt TPL profile catalogue.

    Args:
        apk_path: path to the APK file to analyse.
        jar_path: path to `LIBLOOM.jar`.
        libs_profile_dir: directory with prebuilt library profiles. If None
            or empty — returns status="missing_profile".
        timeout_sec: hard timeout for each subprocess phase.
        java_heap_mb: JVM max heap size (`-Xmx<N>m`).

    Returns:
        dict with keys:
            - status: "ok" | "not_available" | "timeout" | "subprocess_error"
                      | "missing_profile" | "bad_apk"
            - libraries: list of {"name": str, "version": list[str], "similarity": float}
            - unknown_packages: list[str] (empty for MVP)
            - elapsed_sec: float (wall-clock total for both phases)
            - raw_stdout: str | None (last ~4KB of last phase stdout)
            - raw_stderr: str | None
            - appname: str | None (from result JSON)
            - error_reason: short token when status != "ok"

    This function never raises — all exceptions are mapped to a status.
    """
    # 1. bad_apk — APK file missing.
    if not Path(apk_path).is_file():
        result = _empty_result()
        result["status"] = "bad_apk"
        result["error_reason"] = "apk_not_found"
        return result

    # 2. not_available — jar or java missing.
    if not libloom_available(jar_path):
        result = _empty_result()
        result["status"] = "not_available"
        result["error_reason"] = "jar_or_java_missing"
        return result

    # 3. missing_profile — libs_profile_dir None / not a dir / empty.
    if libs_profile_dir is None:
        result = _empty_result()
        result["status"] = "missing_profile"
        result["error_reason"] = "libs_profile_missing"
        return result

    libs_profile_path = Path(libs_profile_dir)
    if not libs_profile_path.is_dir() or not any(libs_profile_path.iterdir()):
        result = _empty_result()
        result["status"] = "missing_profile"
        result["error_reason"] = "libs_profile_missing"
        return result

    # 4–10. Phases + parse; everything wrapped in try/except so we never raise.
    tmp_root_path: Path | None = None
    started = time.monotonic()
    try:
        tmp_root = tempfile.mkdtemp(prefix="libloom_")
        tmp_root_path = Path(tmp_root)

        app_input_dir = tmp_root_path / "app_input"
        apps_profile_dir = tmp_root_path / "apps_profile"
        result_dir = tmp_root_path / "result"
        app_input_dir.mkdir(parents=True, exist_ok=True)
        apps_profile_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        # Copy APK into the input dir (LIBLOOM expects a directory of APKs).
        apk_src = Path(apk_path)
        apk_dst = app_input_dir / apk_src.name
        try:
            shutil.copyfile(str(apk_src), str(apk_dst))
        except OSError as exc:
            result = _empty_result()
            result["status"] = "bad_apk"
            result["error_reason"] = f"apk_copy_failed:{type(exc).__name__}"
            result["elapsed_sec"] = time.monotonic() - started
            return result

        jar_cwd = Path(jar_path).parent
        heap_flag = f"-Xmx{java_heap_mb}m"

        # Phase 1: profile APK.
        profile_cmd = [
            "java",
            heap_flag,
            "-jar",
            str(jar_path),
            "profile",
            "-d",
            str(app_input_dir),
            "-o",
            str(apps_profile_dir),
        ]
        try:
            proc1 = _run_libloom_phase(profile_cmd, jar_cwd, timeout_sec)
        except subprocess.TimeoutExpired:
            result = _empty_result()
            result["status"] = "timeout"
            result["error_reason"] = "hard_timeout"
            result["elapsed_sec"] = time.monotonic() - started
            return result

        if proc1.returncode != 0:
            result = _empty_result()
            result["status"] = "subprocess_error"
            result["error_reason"] = f"jvm_nonzero_exit:{proc1.returncode}"
            result["elapsed_sec"] = time.monotonic() - started
            result["raw_stdout"] = _tail_text(proc1.stdout)
            result["raw_stderr"] = _tail_text(proc1.stderr)
            return result

        # Phase 2: detect.
        detect_cmd = [
            "java",
            heap_flag,
            "-jar",
            str(jar_path),
            "detect",
            "-ad",
            str(apps_profile_dir),
            "-ld",
            str(libs_profile_path),
            "-o",
            str(result_dir),
        ]
        try:
            proc2 = _run_libloom_phase(detect_cmd, jar_cwd, timeout_sec)
        except subprocess.TimeoutExpired:
            result = _empty_result()
            result["status"] = "timeout"
            result["error_reason"] = "hard_timeout"
            result["elapsed_sec"] = time.monotonic() - started
            return result

        if proc2.returncode != 0:
            result = _empty_result()
            result["status"] = "subprocess_error"
            result["error_reason"] = f"jvm_nonzero_exit:{proc2.returncode}"
            result["elapsed_sec"] = time.monotonic() - started
            result["raw_stdout"] = _tail_text(proc2.stdout)
            result["raw_stderr"] = _tail_text(proc2.stderr)
            return result

        # 8. Parse result JSON.
        payload = _read_first_json(result_dir)
        if payload is None:
            result = _empty_result()
            result["status"] = "subprocess_error"
            result["error_reason"] = "empty_result"
            result["elapsed_sec"] = time.monotonic() - started
            result["raw_stdout"] = _tail_text(proc2.stdout)
            result["raw_stderr"] = _tail_text(proc2.stderr)
            return result

        libraries_raw = payload.get("libraries", []) or []
        libraries: list[dict[str, Any]] = []
        for lib in libraries_raw:
            if not isinstance(lib, dict):
                continue
            name = lib.get("name")
            version = lib.get("version", []) or []
            similarity = lib.get("similarity")
            if not isinstance(version, list):
                version = [str(version)]
            try:
                similarity_f = float(similarity) if similarity is not None else 0.0
            except (TypeError, ValueError):
                similarity_f = 0.0
            libraries.append(
                {
                    "name": str(name) if name is not None else "",
                    "version": [str(v) for v in version],
                    "similarity": similarity_f,
                }
            )

        result = _empty_result()
        result["status"] = "ok"
        result["libraries"] = libraries
        result["unknown_packages"] = []
        result["elapsed_sec"] = time.monotonic() - started
        result["raw_stdout"] = _tail_text(proc2.stdout)
        result["raw_stderr"] = _tail_text(proc2.stderr)
        result["appname"] = payload.get("appname")
        result["error_reason"] = None
        return result

    except Exception as exc:  # noqa: BLE001 — adapter never raises.
        result = _empty_result()
        result["status"] = "subprocess_error"
        result["error_reason"] = f"unexpected:{type(exc).__name__}"
        result["elapsed_sec"] = time.monotonic() - started
        return result
    finally:
        if tmp_root_path is not None:
            shutil.rmtree(str(tmp_root_path), ignore_errors=True)

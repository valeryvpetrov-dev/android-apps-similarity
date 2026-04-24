"""Tests for LIBLOOM adapter (EXEC-083-FULL).

Все unit-тесты работают без реального LIBLOOM jar — они мокают
`subprocess.run` и `shutil.which`. Реальный smoke-тест (tests 11)
skipping, если `LIBLOOM.jar` недоступен.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from script import libloom_adapter


REAL_JAR_PATH = (
    "/Users/valeryvpetrov/phd/.claude/worktrees/"
    "brave-cartwright-99d489/experiments/external/LIBLOOM/artifacts/LIBLOOM.jar"
)


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    """Build a CompletedProcess stand-in for subprocess.run mocks."""
    return subprocess.CompletedProcess(
        args=["java"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class LibloomAvailableTests(unittest.TestCase):
    """Контракт `libloom_available`."""

    def test_raises_when_libloom_home_missing_and_no_explicit_path(self) -> None:
        """Без jar_path функция требует LIBLOOM_HOME и поднимает RuntimeError."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIBLOOM_HOME", None)
            with self.assertRaises(RuntimeError) as excinfo:
                libloom_adapter.libloom_available()

        self.assertIn("LIBLOOM_HOME", str(excinfo.exception))

    def test_raises_when_libloom_home_jar_missing_and_no_explicit_path(self) -> None:
        """LIBLOOM_HOME задан, но jar отсутствует -> RuntimeError без fallback."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.dict(os.environ, {"LIBLOOM_HOME": tmp_dir}, clear=False):
                with self.assertRaises(RuntimeError) as excinfo:
                    libloom_adapter.libloom_available()

        self.assertIn("LIBLOOM.jar", str(excinfo.exception))

    def test_reads_jar_from_libloom_home_when_no_explicit_path(self) -> None:
        """Без jar_path адаптер читает `$LIBLOOM_HOME/LIBLOOM.jar`."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            jar_path = Path(tmp_dir) / "LIBLOOM.jar"
            jar_path.write_bytes(b"fake jar")

            with mock.patch.dict(os.environ, {"LIBLOOM_HOME": tmp_dir}, clear=False):
                with mock.patch.object(
                    libloom_adapter.shutil, "which", return_value="/usr/bin/java"
                ):
                    self.assertTrue(libloom_adapter.libloom_available())

    def test_returns_false_on_missing_jar(self) -> None:
        """Jar-файла нет → False (java не проверяется)."""
        self.assertFalse(
            libloom_adapter.libloom_available("/nonexistent/path/to/LIBLOOM.jar")
        )

    def test_returns_true_when_jar_and_java_present(self) -> None:
        """Jar есть и java на PATH → True."""
        with tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar:
            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ):
                self.assertTrue(libloom_adapter.libloom_available(tmp_jar.name))

    def test_returns_false_when_java_missing(self) -> None:
        """Jar есть, java нет на PATH → False."""
        with tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar:
            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value=None
            ):
                self.assertFalse(libloom_adapter.libloom_available(tmp_jar.name))


class DetectLibrariesPrecondTests(unittest.TestCase):
    """Контракт detect_libraries на ранних проверках."""

    def test_bad_apk_when_apk_missing(self) -> None:
        """APK не существует → status=bad_apk."""
        result = libloom_adapter.detect_libraries(
            apk_path="/nonexistent/app.apk",
            jar_path="/nonexistent/LIBLOOM.jar",
        )
        self.assertEqual(result["status"], "bad_apk")
        self.assertEqual(result["libraries"], [])
        self.assertEqual(result["error_reason"], "apk_not_found")

    def test_not_available_when_jar_missing(self) -> None:
        """APK есть, jar нет → status=not_available."""
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk:
            result = libloom_adapter.detect_libraries(
                apk_path=tmp_apk.name,
                jar_path="/nonexistent/LIBLOOM.jar",
            )
        self.assertEqual(result["status"], "not_available")
        self.assertEqual(result["libraries"], [])

    def test_missing_profile_when_libs_none(self) -> None:
        """libs_profile_dir=None → status=missing_profile."""
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar:
            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=None,
                )
        self.assertEqual(result["status"], "missing_profile")
        self.assertEqual(result["libraries"], [])

    def test_missing_profile_when_libs_empty(self) -> None:
        """libs_profile_dir существует но пустой → status=missing_profile."""
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as empty_libs_dir:
            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=empty_libs_dir,
                )
        self.assertEqual(result["status"], "missing_profile")


class DetectLibrariesHappyPathTests(unittest.TestCase):
    """Успешный двухфазный прогон с мокнутым subprocess."""

    def _stub_run(self, result_json: dict):
        """Build a subprocess.run stub that writes result_json on `detect` phase."""

        def _run(cmd, cwd=None, capture_output=None, text=None, timeout=None, check=None):
            # cmd = [..., "profile"|"detect", ...]
            phase = cmd[4]
            if phase == "detect":
                # -o <result_dir> is the last arg pair
                out_dir = Path(cmd[-1])
                out_dir.mkdir(parents=True, exist_ok=True)
                with (out_dir / "result.json").open("w", encoding="utf-8") as fh:
                    json.dump(result_json, fh)
            return _make_completed_process(returncode=0, stdout="ok\n", stderr="")

        return _run

    def test_happy_path_parses_libraries(self) -> None:
        """Оба phase успешны → status=ok, libraries парсятся из JSON."""
        fixture_payload = {
            "appname": "com.example.app",
            "libraries": [
                {
                    "name": "okhttp3",
                    "version": ["3.12.1"],
                    "similarity": 0.95,
                },
                {
                    "name": "retrofit2",
                    "version": ["2.9.0", "2.10.0"],
                    "similarity": 0.88,
                },
            ],
        }
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as libs_dir:
            # libs_dir должен быть non-empty.
            Path(libs_dir, "okhttp3").mkdir()

            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ), mock.patch.object(
                libloom_adapter.subprocess,
                "run",
                side_effect=self._stub_run(fixture_payload),
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=libs_dir,
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["appname"], "com.example.app")
        self.assertEqual(len(result["libraries"]), 2)
        self.assertEqual(result["libraries"][0]["name"], "okhttp3")
        self.assertEqual(result["libraries"][0]["version"], ["3.12.1"])
        self.assertAlmostEqual(result["libraries"][0]["similarity"], 0.95)
        self.assertEqual(result["libraries"][1]["name"], "retrofit2")
        self.assertEqual(result["libraries"][1]["version"], ["2.9.0", "2.10.0"])
        self.assertEqual(result["unknown_packages"], [])
        self.assertGreaterEqual(result["elapsed_sec"], 0.0)
        self.assertEqual(result["error_reason"], None)


class DetectLibrariesErrorPathTests(unittest.TestCase):
    """Ошибочные subprocess-пути с мокнутым subprocess."""

    def test_timeout_on_profile_phase(self) -> None:
        """TimeoutExpired на profile → status=timeout."""
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as libs_dir:
            Path(libs_dir, "okhttp3").mkdir()

            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ), mock.patch.object(
                libloom_adapter.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["java"], timeout=1),
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=libs_dir,
                    timeout_sec=1,
                )

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["error_reason"], "hard_timeout")
        self.assertEqual(result["libraries"], [])

    def test_nonzero_returncode_on_detect(self) -> None:
        """detect фаза возвращает returncode=3 → status=subprocess_error."""

        def _run(cmd, cwd=None, capture_output=None, text=None, timeout=None, check=None):
            phase = cmd[4]
            if phase == "profile":
                return _make_completed_process(0, "profile-ok", "")
            return _make_completed_process(3, "detect-bad", "OutOfMemoryError")

        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as libs_dir:
            Path(libs_dir, "okhttp3").mkdir()

            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ), mock.patch.object(
                libloom_adapter.subprocess, "run", side_effect=_run
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=libs_dir,
                )

        self.assertEqual(result["status"], "subprocess_error")
        self.assertTrue(
            result["error_reason"].startswith("jvm_nonzero_exit:"),
            msg=f"unexpected error_reason={result['error_reason']}",
        )
        self.assertIn("OutOfMemoryError", result["raw_stderr"] or "")

    def test_empty_result_directory(self) -> None:
        """detect завершился успешно, но result dir пуст → empty_result."""

        def _run(cmd, cwd=None, capture_output=None, text=None, timeout=None, check=None):
            return _make_completed_process(0, "", "")

        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as libs_dir:
            Path(libs_dir, "okhttp3").mkdir()

            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ), mock.patch.object(
                libloom_adapter.subprocess, "run", side_effect=_run
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=libs_dir,
                )

        self.assertEqual(result["status"], "subprocess_error")
        self.assertEqual(result["error_reason"], "empty_result")

    def test_does_not_raise_on_unexpected_exception(self) -> None:
        """Любое непредвиденное исключение — не пробрасывается наружу."""

        def _run(cmd, cwd=None, capture_output=None, text=None, timeout=None, check=None):
            raise RuntimeError("simulated")

        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk, \
                tempfile.NamedTemporaryFile(suffix=".jar") as tmp_jar, \
                tempfile.TemporaryDirectory() as libs_dir:
            Path(libs_dir, "okhttp3").mkdir()

            with mock.patch.object(
                libloom_adapter.shutil, "which", return_value="/usr/bin/java"
            ), mock.patch.object(
                libloom_adapter.subprocess, "run", side_effect=_run
            ):
                result = libloom_adapter.detect_libraries(
                    apk_path=tmp_apk.name,
                    jar_path=tmp_jar.name,
                    libs_profile_dir=libs_dir,
                )

        self.assertEqual(result["status"], "subprocess_error")
        self.assertTrue(
            result["error_reason"].startswith("unexpected:"),
            msg=f"unexpected error_reason={result['error_reason']}",
        )


class DefaultsTests(unittest.TestCase):
    """Контракт дефолтных констант."""

    def test_default_timeout_is_600(self) -> None:
        self.assertEqual(libloom_adapter.DEFAULT_TIMEOUT_SEC, 600)

    def test_default_heap_is_2048(self) -> None:
        self.assertEqual(libloom_adapter.DEFAULT_JAVA_HEAP_MB, 2048)


@unittest.skipUnless(
    os.path.isfile(REAL_JAR_PATH),
    f"real LIBLOOM.jar not available at {REAL_JAR_PATH}",
)
class RealJarSmokeTests(unittest.TestCase):
    """Smoke-тест на реальном LIBLOOM.jar (skipped без jar в окружении)."""

    def test_smoke_missing_profile_without_libs_profile_dir(self) -> None:
        """Тест быстрый: не запускает JVM, проверяет только ранние пути."""
        with tempfile.NamedTemporaryFile(suffix=".apk") as tmp_apk:
            result = libloom_adapter.detect_libraries(
                apk_path=tmp_apk.name,
                jar_path=REAL_JAR_PATH,
                libs_profile_dir=None,
            )
        self.assertEqual(result["status"], "missing_profile")


if __name__ == "__main__":
    unittest.main()

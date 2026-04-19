"""Tests for LIBLOOM adapter skeleton (EXEC-083-SEED-B).

Тесты работают без реального LIBLOOM jar: они проверяют контракт skeleton'а,
не реальный subprocess-вызов. Полная интеграция — EXEC-083-FULL.
"""
from __future__ import annotations

import unittest
from unittest import mock

from script import libloom_adapter


class LibloomAvailableTests(unittest.TestCase):
    """Контракт проверки доступности LIBLOOM."""

    def test_libloom_available_returns_false_on_missing_jar(self) -> None:
        """Если jar-файла нет — доступность False, java не важна."""
        fake_jar = "/nonexistent/path/to/LIBLOOM.jar"
        self.assertFalse(libloom_adapter.libloom_available(fake_jar))


class DetectLibrariesNotAvailableTests(unittest.TestCase):
    """Контракт detect_libraries при отсутствии jar."""

    def test_detect_libraries_returns_not_available_when_jar_missing(self) -> None:
        """Missing jar → status=not_available, libraries=[]."""
        result = libloom_adapter.detect_libraries(
            apk_path="/tmp/any.apk",
            jar_path="/nonexistent/LIBLOOM.jar",
        )
        self.assertEqual(result["status"], "not_available")
        self.assertEqual(result["libraries"], [])

    def test_detect_libraries_schema_keys(self) -> None:
        """Dict содержит все контрактные ключи даже в not_available-пути."""
        result = libloom_adapter.detect_libraries(
            apk_path="/tmp/any.apk",
            jar_path="/nonexistent/LIBLOOM.jar",
        )
        expected_keys = {
            "libraries",
            "unknown_packages",
            "status",
            "elapsed_sec",
            "raw_stdout",
            "raw_stderr",
        }
        self.assertEqual(set(result.keys()), expected_keys)


class DetectLibrariesJarPresentTests(unittest.TestCase):
    """Контракт detect_libraries когда jar доступен (skeleton raises)."""

    def test_detect_libraries_raises_not_implemented_when_jar_present(self) -> None:
        """Если jar + java доступны → skeleton падает NotImplementedError.

        Это намеренный flag: реальный subprocess-вызов ещё не реализован
        (EXEC-083-FULL). Skeleton явно сигнализирует, что предстоит работа.
        """
        with mock.patch.object(
            libloom_adapter, "libloom_available", return_value=True
        ):
            with self.assertRaises(NotImplementedError):
                libloom_adapter.detect_libraries(
                    apk_path="/tmp/any.apk",
                    jar_path="/some/jar/path.jar",
                )


class DefaultTimeoutTests(unittest.TestCase):
    """Контракт значения таймаута по умолчанию."""

    def test_timeout_default_is_600_sec(self) -> None:
        """315 сек по публикации + buffer = 600 сек (см. план EXEC-083)."""
        self.assertEqual(libloom_adapter.DEFAULT_TIMEOUT_SEC, 600)


if __name__ == "__main__":
    unittest.main()

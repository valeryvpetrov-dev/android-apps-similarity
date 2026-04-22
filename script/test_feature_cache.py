#!/usr/bin/env python3
"""Тесты модуля feature_cache (EXEC-REPR-FEATURE-CACHE).

Покрытие:

* ``sha256_of_file`` — детерминизм, зависимость от содержимого,
  отсутствующий файл;
* ``FeatureCache.put`` / ``get`` — круговой цикл, отсутствующий ключ;
* ``get_or_extract`` — первый вызов (с ``put``), повторный (без
  пересчёта через ``extract_fn``), смена ``feature_version``
  инвалидирует кэш;
* устойчивость: повреждённый JSON, недоступный ``cache_dir``;
* экономия времени: медленный ``extract_fn`` + повторный вызов ≤ 10 мс.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from feature_cache import (  # noqa: E402
    FeatureCache,
    cache_key,
    get_or_extract,
    sha256_of_file,
)


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class TestSha256OfFile(unittest.TestCase):
    """SHA-256 APK должен быть детерминированным и зависеть от содержимого."""

    def test_sha256_matches_reference_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = _write_bytes(Path(tmp) / "a.apk", b"hello-apk")
            expected = hashlib.sha256(b"hello-apk").hexdigest()
            self.assertEqual(sha256_of_file(str(apk)), expected)

    def test_sha256_is_deterministic_for_same_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk1 = _write_bytes(Path(tmp) / "a.apk", b"payload-X")
            apk2 = _write_bytes(Path(tmp) / "b.apk", b"payload-X")
            self.assertEqual(sha256_of_file(str(apk1)), sha256_of_file(str(apk2)))

    def test_sha256_differs_for_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk1 = _write_bytes(Path(tmp) / "a.apk", b"payload-A")
            apk2 = _write_bytes(Path(tmp) / "b.apk", b"payload-B")
            self.assertNotEqual(sha256_of_file(str(apk1)), sha256_of_file(str(apk2)))

    def test_sha256_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.apk"
            with self.assertRaises(FileNotFoundError):
                sha256_of_file(str(missing))


class TestCacheKey(unittest.TestCase):
    """Ключ включает feature_version, чтобы инвалидировать при смене схемы."""

    def test_cache_key_includes_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = _write_bytes(Path(tmp) / "a.apk", b"bytes")
            k1 = cache_key(str(apk), "v1")
            k2 = cache_key(str(apk), "v2")
            self.assertNotEqual(k1, k2)
            self.assertTrue(k1.endswith("__v1"))
            self.assertTrue(k2.endswith("__v2"))


class TestFeatureCachePutGet(unittest.TestCase):
    """Круговой цикл put -> get сохраняет и восстанавливает признаки."""

    def test_put_then_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = FeatureCache(tmp)
            self.assertTrue(cache.available)
            payload = {
                "code": {"a", "b"},
                "metadata": {"uses_permission:android.INTERNET"},
                "signing": {"hash": "abc", "chain": []},
                "mode": "quick",
            }
            cache.put("key-1", payload)
            restored = cache.get("key-1")
            self.assertEqual(restored, payload)
            # set восстановлен как set, а не список.
            self.assertIsInstance(restored["code"], set)

    def test_get_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = FeatureCache(tmp)
            self.assertIsNone(cache.get("absent-key"))

    def test_clear_removes_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = FeatureCache(tmp)
            cache.put("k1", {"a": 1})
            cache.put("k2", {"b": 2})
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 2)
            cache.clear()
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 0)


class TestGetOrExtract(unittest.TestCase):
    """Поведение обёртки get_or_extract по сценариям."""

    def test_first_call_invokes_extract_and_stores(self):
        with tempfile.TemporaryDirectory() as tmp_cache, tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")
            extract_fn = mock.Mock(return_value={"code": {"x"}, "mode": "quick"})
            result = get_or_extract(str(apk), extract_fn, tmp_cache, feature_version="v1")
            extract_fn.assert_called_once()
            self.assertEqual(result, {"code": {"x"}, "mode": "quick"})
            # В кэше появился файл.
            files = list(Path(tmp_cache).glob("*.json"))
            self.assertEqual(len(files), 1)

    def test_second_call_skips_extract_fn(self):
        with tempfile.TemporaryDirectory() as tmp_cache, tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")

            def _first() -> dict:
                return {"code": {"x"}, "mode": "quick"}

            get_or_extract(str(apk), _first, tmp_cache, feature_version="v1")

            extract_fn_second = mock.Mock(return_value={"should": "not-be-used"})
            result = get_or_extract(
                str(apk), extract_fn_second, tmp_cache, feature_version="v1",
            )
            extract_fn_second.assert_not_called()
            self.assertEqual(result, {"code": {"x"}, "mode": "quick"})

    def test_feature_version_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp_cache, tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")

            extract_v1 = mock.Mock(return_value={"v": 1})
            get_or_extract(str(apk), extract_v1, tmp_cache, feature_version="v1")
            extract_v1.assert_called_once()

            extract_v2 = mock.Mock(return_value={"v": 2})
            result = get_or_extract(str(apk), extract_v2, tmp_cache, feature_version="v2")
            extract_v2.assert_called_once()
            self.assertEqual(result, {"v": 2})

    def test_cache_dir_none_always_calls_extract(self):
        with tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")
            extract_fn = mock.Mock(return_value={"code": {"x"}})
            r1 = get_or_extract(str(apk), extract_fn, None)
            r2 = get_or_extract(str(apk), extract_fn, None)
            self.assertEqual(extract_fn.call_count, 2)
            self.assertEqual(r1, r2)


class TestCorruptedCache(unittest.TestCase):
    """Повреждённый JSON должен игнорироваться с пересчётом."""

    def test_corrupted_json_triggers_recompute(self):
        with tempfile.TemporaryDirectory() as tmp_cache, tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")
            key = cache_key(str(apk), "v1")
            # Кладём невалидный JSON вместо нормального файла.
            corrupt = Path(tmp_cache) / "{}.json".format(key)
            corrupt.parent.mkdir(parents=True, exist_ok=True)
            corrupt.write_text("{not valid json", encoding="utf-8")

            extract_fn = mock.Mock(return_value={"code": {"x"}})
            result = get_or_extract(str(apk), extract_fn, tmp_cache, feature_version="v1")

            extract_fn.assert_called_once()
            self.assertEqual(result, {"code": {"x"}})
            # Файл должен быть перезаписан корректным JSON.
            loaded = json.loads(corrupt.read_text(encoding="utf-8"))
            self.assertIsInstance(loaded, dict)


class TestUnavailableCacheDir(unittest.TestCase):
    """Если cache_dir недоступен — работаем без кэша с warning-логом."""

    def test_unavailable_cache_dir_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp_apk, tempfile.TemporaryDirectory() as tmp_base:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")
            # Создаём файл на месте будущей директории — mkdir упадёт.
            blocker = Path(tmp_base) / "not-a-dir"
            blocker.write_text("blocker", encoding="utf-8")

            extract_fn = mock.Mock(return_value={"code": {"x"}})
            with self.assertLogs("feature_cache", level="WARNING") as cm:
                result = get_or_extract(
                    str(apk), extract_fn, str(blocker), feature_version="v1",
                )
            self.assertEqual(result, {"code": {"x"}})
            extract_fn.assert_called_once()
            joined = "\n".join(cm.output)
            self.assertIn("недоступна", joined)


class TestWarmCachePerformance(unittest.TestCase):
    """Повторный вызов должен укладываться в ≤ 10 мс за счёт пропуска extract_fn."""

    def test_warm_cache_is_faster_than_cold(self):
        with tempfile.TemporaryDirectory() as tmp_cache, tempfile.TemporaryDirectory() as tmp_apk:
            apk = _write_bytes(Path(tmp_apk) / "a.apk", b"data")

            def slow_extract() -> dict:
                time.sleep(0.1)
                return {"code": {"x"}, "mode": "quick"}

            cold_start = time.perf_counter()
            get_or_extract(str(apk), slow_extract, tmp_cache, feature_version="v1")
            cold_elapsed = time.perf_counter() - cold_start

            warm_start = time.perf_counter()
            get_or_extract(str(apk), slow_extract, tmp_cache, feature_version="v1")
            warm_elapsed = time.perf_counter() - warm_start

            # Холодный вызов обязан быть ≥ 100 мс (sleep), тёплый — ≤ 10 мс.
            self.assertGreaterEqual(cold_elapsed, 0.09)
            self.assertLessEqual(
                warm_elapsed,
                0.010,
                "Тёплый вызов занял {:.4f} с, ожидалось ≤ 0.010 с".format(warm_elapsed),
            )


if __name__ == "__main__":
    unittest.main()

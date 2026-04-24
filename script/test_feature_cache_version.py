#!/usr/bin/env python3
"""Тесты versioning ключа SQLite feature-cache."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for candidate in (str(SCRIPT_DIR), str(PROJECT_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pairwise_runner  # noqa: E402
from feature_cache_sqlite import FeatureCacheSqlite  # noqa: E402


def _touch_apk(path: Path) -> Path:
    path.write_bytes(b"fake-apk")
    return path


class TestFeatureCacheSqliteVersioning(unittest.TestCase):
    def test_put_stores_independent_rows_per_feature_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FeatureCacheSqlite(Path(tmpdir) / "feature-cache.sqlite")

            cache.put("a" * 64, "v1", {"mode": "old"})
            cache.put("a" * 64, "v2", {"mode": "new"})

            self.assertEqual(cache.get("a" * 64, "v1"), {"mode": "old"})
            self.assertEqual(cache.get("a" * 64, "v2"), {"mode": "new"})
            cache.close()

    def test_get_returns_miss_for_same_sha_with_other_feature_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FeatureCacheSqlite(Path(tmpdir) / "feature-cache.sqlite")

            cache.put("b" * 64, "v1", {"mode": "old"})

            self.assertIsNone(cache.get("b" * 64, "v2"))
            cache.close()

    def test_legacy_schema_without_feature_version_refuses_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "feature-cache.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE feature_cache (
                        sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
                        features_json TEXT NOT NULL CHECK(length(features_json) > 0),
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO feature_cache(sha256, features_json, created_at)
                    VALUES(?, ?, 1)
                    """,
                    ("c" * 64, '{"mode":"legacy"}'),
                )
                conn.commit()

            cache = FeatureCacheSqlite(db_path)
            with self.assertRaises(ValueError):
                cache.get("c" * 64, "v1")
            cache.close()


class TestPairwiseRunnerFeatureCacheVersion(unittest.TestCase):
    def test_load_layers_passes_feature_cache_version_to_cache_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = _touch_apk(Path(tmpdir) / "sample.apk")
            feature_bundle = {
                "code": {"dex:classes.dex"},
                "metadata": set(),
                "component": {},
                "resource": {},
                "library": {},
            }
            feature_cache = mock.Mock()
            feature_cache.get.return_value = None

            with mock.patch.object(
                pairwise_runner,
                "_sha256_of_file",
                return_value="d" * 64,
            ), mock.patch.object(
                pairwise_runner,
                "extract_all_features",
                return_value=feature_bundle,
            ):
                pairwise_runner.load_layers_for_pairwise(
                    apk_path=str(apk_path),
                    decoded_dir=None,
                    selected_layers=["code"],
                    layer_cache={},
                    feature_cache=feature_cache,
                )

            self.assertEqual(pairwise_runner.FEATURE_CACHE_VERSION, "v1")
            feature_cache.get.assert_called_once_with("d" * 64, "v1")
            feature_cache.put.assert_called_once_with("d" * 64, "v1", feature_bundle)


if __name__ == "__main__":
    unittest.main(verbosity=2)

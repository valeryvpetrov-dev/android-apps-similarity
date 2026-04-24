#!/usr/bin/env python3
"""EXEC-PAIRWISE-SHARED-CACHE: SQLite-кэш feature bundle по SHA-256 APK.

Legacy DB-файлы без `feature_version` намеренно не читаются: при доступе
поднимается `ValueError` с инструкцией удалить старый cache-файл и пересобрать
его заново. Это безопаснее, чем молча читать устаревшие feature bundle.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_TYPE_MARKER = "__feature_cache_type__"
_TYPE_SET = "set"
_TYPE_TUPLE = "tuple"
_FEATURES_TABLE = "features"
_LEGACY_TABLE = "feature_cache"


def _encode_json(payload: Any) -> Any:
    if isinstance(payload, set):
        items = [_encode_json(item) for item in payload]
        items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return {_TYPE_MARKER: _TYPE_SET, "items": items}
    if isinstance(payload, tuple):
        return {
            _TYPE_MARKER: _TYPE_TUPLE,
            "items": [_encode_json(item) for item in payload],
        }
    if isinstance(payload, dict):
        return {str(key): _encode_json(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_encode_json(item) for item in payload]
    return payload


def _decode_json(payload: Any) -> Any:
    if isinstance(payload, dict):
        marker = payload.get(_TYPE_MARKER)
        if marker == _TYPE_SET:
            return {_decode_json(item) for item in payload.get("items", [])}
        if marker == _TYPE_TUPLE:
            return tuple(_decode_json(item) for item in payload.get("items", []))
        return {key: _decode_json(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_decode_json(item) for item in payload]
    return payload


class FeatureCacheSqlite:
    """Persistent feature-cache на SQLite с WAL для multi-process access."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._legacy_schema_error: str | None = None
        self._conn = sqlite3.connect(
            self._path,
            timeout=30.0,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA busy_timeout = 30000")
        for attempt in range(10):
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
                self._initialize_schema()
                self._conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))

    @staticmethod
    def _validate_sha256(apk_sha256: str) -> None:
        if len(apk_sha256) != 64:
            raise ValueError("apk_sha256 must be a 64-char hex digest")

    @staticmethod
    def _validate_feature_version(feature_version: str) -> None:
        if not feature_version or not feature_version.strip():
            raise ValueError("feature_version must be a non-empty string")

    def _initialize_schema(self) -> None:
        tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if _FEATURES_TABLE in tables:
            columns = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info({})".format(_FEATURES_TABLE)
                ).fetchall()
            ]
            if columns != ["sha256", "feature_version", "blob"]:
                self._legacy_schema_error = (
                    "Unsupported feature cache schema at {}. "
                    "Delete the cache DB and rerun to rebuild it."
                ).format(self._path)
            return
        if _LEGACY_TABLE in tables:
            self._legacy_schema_error = (
                "Legacy feature cache schema without feature_version detected at {}. "
                "Delete the cache DB and rerun to rebuild it."
            ).format(self._path)
            return
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS features (
                sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                feature_version TEXT NOT NULL CHECK(length(feature_version) > 0),
                blob BLOB NOT NULL,
                PRIMARY KEY (sha256, feature_version)
            )
            """
        )

    def _ensure_supported_schema(self) -> None:
        if self._legacy_schema_error is not None:
            raise ValueError(self._legacy_schema_error)

    def get(self, apk_sha256: str, feature_version: str) -> dict | None:
        self._validate_sha256(apk_sha256)
        self._validate_feature_version(feature_version)
        self._ensure_supported_schema()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT blob
                FROM features
                WHERE sha256 = ? AND feature_version = ?
                """,
                (apk_sha256, feature_version),
            ).fetchone()
        if row is None:
            return None
        try:
            raw_blob = row[0]
            if isinstance(raw_blob, bytes):
                payload = json.loads(raw_blob.decode("utf-8"))
            else:
                payload = json.loads(raw_blob)
        except json.JSONDecodeError:
            logger.warning(
                "FeatureCacheSqlite: corrupted JSON for sha256=%s feature_version=%s",
                apk_sha256,
                feature_version,
            )
            return None
        decoded = _decode_json(payload)
        if not isinstance(decoded, dict):
            logger.warning(
                "FeatureCacheSqlite: payload is not a dict for sha256=%s feature_version=%s",
                apk_sha256,
                feature_version,
            )
            return None
        return decoded

    def put(self, apk_sha256: str, feature_version: str, features: dict) -> None:
        self._validate_sha256(apk_sha256)
        self._validate_feature_version(feature_version)
        self._ensure_supported_schema()
        encoded = _encode_json(features)
        blob = json.dumps(encoded, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if not blob:
            raise ValueError("blob must not be empty")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO features(sha256, feature_version, blob)
                VALUES(?, ?, ?)
                ON CONFLICT(sha256, feature_version) DO UPDATE SET
                    blob = excluded.blob
                """,
                (apk_sha256, feature_version, blob),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = ["FeatureCacheSqlite"]

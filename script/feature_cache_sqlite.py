#!/usr/bin/env python3
"""EXEC-PAIRWISE-SHARED-CACHE: SQLite-кэш feature bundle по SHA-256 APK."""
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
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feature_cache (
                        sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
                        features_json TEXT NOT NULL CHECK(length(features_json) > 0),
                        created_at INTEGER NOT NULL
                    )
                    """
                )
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

    def get(self, apk_sha256: str) -> dict | None:
        self._validate_sha256(apk_sha256)
        with self._lock:
            row = self._conn.execute(
                "SELECT features_json FROM feature_cache WHERE sha256 = ?",
                (apk_sha256,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("FeatureCacheSqlite: corrupted JSON for sha256=%s", apk_sha256)
            return None
        decoded = _decode_json(payload)
        if not isinstance(decoded, dict):
            logger.warning("FeatureCacheSqlite: payload is not a dict for sha256=%s", apk_sha256)
            return None
        return decoded

    def set(self, apk_sha256: str, features: dict) -> None:
        self._validate_sha256(apk_sha256)
        encoded = _encode_json(features)
        features_json = json.dumps(encoded, ensure_ascii=False, sort_keys=True)
        if not features_json:
            raise ValueError("features_json must not be empty")
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO feature_cache(sha256, features_json, created_at)
                VALUES(?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    features_json = excluded.features_json,
                    created_at = excluded.created_at
                """,
                (apk_sha256, features_json, now),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = ["FeatureCacheSqlite"]

#!/usr/bin/env python3
"""Unified compatibility manifest for persistent cache layers."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "SYS-INT-24-CACHE-MANIFEST"
    / "manifest.json"
)

_EXPECTED_CACHES = {"noise", "feature_sqlite", "feature_json"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class CacheManifestMismatch(ValueError):
    """Raised when a cache record does not match the compatibility manifest."""


def load(manifest_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the cache compatibility manifest."""
    path = Path(manifest_path).expanduser() if manifest_path is not None else DEFAULT_MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("cache manifest must be a JSON object")

    missing = _EXPECTED_CACHES.difference(manifest)
    extra = set(manifest).difference(_EXPECTED_CACHES)
    if missing or extra:
        raise ValueError(
            "cache manifest caches mismatch: missing={} extra={}".format(
                sorted(missing),
                sorted(extra),
            )
        )

    for cache_name, spec in manifest.items():
        if not isinstance(spec, dict):
            raise ValueError("cache manifest spec must be an object: {}".format(cache_name))
        for field in ("key_schema", "version", "invalidation_rule", "path"):
            if field not in spec:
                raise ValueError("cache manifest {} missing {}".format(cache_name, field))
        if not isinstance(spec["key_schema"], list) or not spec["key_schema"]:
            raise ValueError("cache manifest {} has invalid key_schema".format(cache_name))
    return manifest


def validate_cache_record(
    cache_name: str,
    record: dict[str, Any],
    manifest_path: str | Path | None = None,
) -> bool:
    """Validate a physical or logical cache record against the manifest."""
    manifest = load(manifest_path)
    spec = _cache_spec(manifest, cache_name)
    if not isinstance(record, dict):
        raise CacheManifestMismatch("cache record must be a dict")

    missing = [field for field in spec["key_schema"] if field not in record]
    if missing:
        raise CacheManifestMismatch(
            "{} record missing key fields: {}".format(cache_name, ", ".join(missing))
        )

    sha256_value = record.get("sha256")
    if not isinstance(sha256_value, str) or not _SHA256_RE.fullmatch(sha256_value):
        raise CacheManifestMismatch("{} record has invalid sha256".format(cache_name))

    version_field = _version_field(spec)
    expected_version = spec["version"]
    actual_version = record.get(version_field)
    if actual_version != expected_version:
        raise CacheManifestMismatch(
            "{} record version mismatch for {}: expected {!r}, got {!r}".format(
                cache_name,
                version_field,
                expected_version,
                actual_version,
            )
        )
    return True


def invalidate_outdated(
    cache_name: str,
    manifest_path: str | Path | None = None,
) -> dict[str, list[str]]:
    """Delete physical cache records whose version does not match the manifest."""
    manifest = load(manifest_path)
    spec = _cache_spec(manifest, cache_name)
    cache_path = _resolve_cache_path(spec["path"])
    storage = spec.get("storage", "json_files")

    if storage == "json_files":
        deleted = _invalidate_json_files(cache_name, spec, cache_path)
    elif storage == "sqlite":
        deleted = _invalidate_sqlite(spec, cache_path)
    else:
        raise ValueError("unsupported cache storage for {}: {}".format(cache_name, storage))
    return {"deleted": deleted}


def _cache_spec(manifest: dict[str, dict[str, Any]], cache_name: str) -> dict[str, Any]:
    try:
        return manifest[cache_name]
    except KeyError as exc:
        raise ValueError("unknown cache in manifest: {}".format(cache_name)) from exc


def _version_field(spec: dict[str, Any]) -> str:
    configured = spec.get("version_field")
    if isinstance(configured, str) and configured:
        return configured
    candidates = [field for field in spec["key_schema"] if field.endswith("_version")]
    if len(candidates) != 1:
        raise ValueError("cannot infer version field from key_schema: {}".format(spec["key_schema"]))
    return candidates[0]


def _resolve_cache_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _invalidate_json_files(
    cache_name: str,
    spec: dict[str, Any],
    cache_path: Path,
) -> list[str]:
    if cache_path.is_file():
        files = [cache_path]
    elif cache_path.is_dir():
        files = sorted(cache_path.glob("*.json"))
    else:
        return []

    deleted: list[str] = []
    version_field = _version_field(spec)
    for file_path in files:
        record = _record_from_json_filename(file_path, version_field)
        if record is None:
            continue
        try:
            validate_cache_record_from_spec(cache_name, spec, record)
        except CacheManifestMismatch:
            file_path.unlink()
            deleted.append(str(file_path))
    return deleted


def _record_from_json_filename(file_path: Path, version_field: str) -> dict[str, str] | None:
    stem = file_path.stem
    if "__" in stem:
        digest, version = stem.split("__", 1)
    else:
        digest, version = stem, ""
    if not _SHA256_RE.fullmatch(digest):
        return None
    return {"sha256": digest.lower(), version_field: version}


def validate_cache_record_from_spec(
    cache_name: str,
    spec: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    missing = [field for field in spec["key_schema"] if field not in record]
    if missing:
        raise CacheManifestMismatch(
            "{} record missing key fields: {}".format(cache_name, ", ".join(missing))
        )
    sha256_value = record.get("sha256")
    if not isinstance(sha256_value, str) or not _SHA256_RE.fullmatch(sha256_value):
        raise CacheManifestMismatch("{} record has invalid sha256".format(cache_name))
    version_field = _version_field(spec)
    if record.get(version_field) != spec["version"]:
        raise CacheManifestMismatch("{} record version mismatch".format(cache_name))
    return True


def _invalidate_sqlite(spec: dict[str, Any], cache_path: Path) -> list[str]:
    if not cache_path.exists():
        return []

    deleted: list[str] = []
    version_field = _version_field(spec)
    if version_field != "feature_version":
        raise ValueError("sqlite invalidation supports feature_version only")

    with sqlite3.connect(cache_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "features" in tables:
            rows = conn.execute(
                "SELECT sha256, feature_version FROM features WHERE feature_version != ?",
                (spec["version"],),
            ).fetchall()
            conn.execute(
                "DELETE FROM features WHERE feature_version != ?",
                (spec["version"],),
            )
            deleted.extend(
                "{}:features:{}:{}".format(cache_path, sha256_value, feature_version)
                for sha256_value, feature_version in rows
            )
        elif "feature_cache" in tables:
            rows = conn.execute("SELECT sha256 FROM feature_cache").fetchall()
            conn.execute("DELETE FROM feature_cache")
            deleted.extend(
                "{}:feature_cache:{}:<legacy-no-version>".format(cache_path, row[0])
                for row in rows
            )
        conn.commit()
    return deleted


__all__ = [
    "CacheManifestMismatch",
    "DEFAULT_MANIFEST_PATH",
    "invalidate_outdated",
    "load",
    "validate_cache_record",
]

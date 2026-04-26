#!/usr/bin/env python3
"""Tests for the unified cache compatibility manifest."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _cache_manifest():
    try:
        return importlib.import_module("script.cache_manifest")
    except ModuleNotFoundError as exc:
        pytest.fail("script.cache_manifest must exist: {}".format(exc))


def _sha256() -> str:
    return "a" * 64


def test_load_returns_manifest_for_all_three_caches() -> None:
    cache_manifest = _cache_manifest()

    manifest = cache_manifest.load()

    assert set(manifest) == {"noise", "feature_sqlite", "feature_json"}
    assert manifest["noise"]["key_schema"] == ["sha256", "profile_version"]
    assert manifest["feature_sqlite"]["key_schema"] == ["sha256", "feature_version"]
    assert manifest["feature_json"]["key_schema"] == ["sha256", "feature_version"]


def test_every_cache_declares_version_invalidation_rule_and_path() -> None:
    cache_manifest = _cache_manifest()

    manifest = cache_manifest.load()

    for cache_name, spec in manifest.items():
        assert spec["key_schema"], cache_name
        assert isinstance(spec["version"], str) and spec["version"], cache_name
        assert isinstance(spec["invalidation_rule"], str) and spec["invalidation_rule"], cache_name
        assert isinstance(spec["path"], str) and spec["path"], cache_name


def test_validate_cache_record_accepts_manifest_match_and_rejects_mismatch() -> None:
    cache_manifest = _cache_manifest()
    manifest = cache_manifest.load()

    assert cache_manifest.validate_cache_record(
        "noise",
        {"sha256": _sha256(), "profile_version": manifest["noise"]["version"]},
    ) is True
    assert cache_manifest.validate_cache_record(
        "feature_sqlite",
        {"sha256": _sha256(), "feature_version": manifest["feature_sqlite"]["version"]},
    ) is True
    assert cache_manifest.validate_cache_record(
        "feature_json",
        {"sha256": _sha256(), "feature_version": manifest["feature_json"]["version"]},
    ) is True

    with pytest.raises(cache_manifest.CacheManifestMismatch):
        cache_manifest.validate_cache_record(
            "noise",
            {"sha256": _sha256(), "profile_version": "old-profile"},
        )

    with pytest.raises(cache_manifest.CacheManifestMismatch):
        cache_manifest.validate_cache_record(
            "feature_sqlite",
            {"sha256": _sha256()},
        )


def test_invalidate_outdated_removes_json_files_with_old_versions(tmp_path: Path) -> None:
    cache_manifest = _cache_manifest()
    manifest = cache_manifest.load()
    cache_dir = tmp_path / "feature-json-cache"
    cache_dir.mkdir()

    test_manifest = dict(manifest)
    test_manifest["feature_json"] = dict(manifest["feature_json"])
    test_manifest["feature_json"]["path"] = str(cache_dir)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(test_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    current_version = test_manifest["feature_json"]["version"]
    current = cache_dir / "{}__{}.json".format(_sha256(), current_version)
    outdated = cache_dir / "{}__old-feature.json".format(_sha256())
    current.write_text("{}", encoding="utf-8")
    outdated.write_text("{}", encoding="utf-8")

    result = cache_manifest.invalidate_outdated(
        "feature_json",
        manifest_path=manifest_path,
    )

    assert result["deleted"] == [str(outdated)]
    assert current.exists()
    assert not outdated.exists()

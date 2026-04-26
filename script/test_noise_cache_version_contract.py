#!/usr/bin/env python3
"""Version compatibility contract for NoiseCache records."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from unittest import mock

from script import cache_manifest
from script import libloom_adapter
from script import noise_profile_envelope
from script.noise_cache import NoiseCache


def _sha256(data: bytes = b"fake-apk") -> str:
    return hashlib.sha256(data).hexdigest()


def _envelope(status: str = "success") -> dict:
    return {
        "schema_version": "nc-v1",
        "status": status,
        "libloom_status": "ok",
        "libloom_libraries": [{"name": "com.squareup.okhttp3"}],
    }


def _write_manifest(tmp_path: Path, cache_dir: Path, version: str) -> Path:
    manifest = copy.deepcopy(cache_manifest.load())
    manifest["noise"]["path"] = str(cache_dir)
    manifest["noise"]["version"] = version
    manifest["noise"]["invalidation_rule"] = (
        "A record is stale when profile_version differs from {}.".format(version)
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def test_noise_cache_put_uses_current_profile_version_from_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache_dir = tmp_path / "noise-cache"
    manifest_path = _write_manifest(tmp_path, cache_dir, version="v-current-noise")
    monkeypatch.setattr(cache_manifest, "DEFAULT_MANIFEST_PATH", manifest_path)
    apk_sha256 = _sha256()

    cache = NoiseCache(cache_dir)
    cache.put(apk_sha256, _envelope())

    assert (cache_dir / "{}__v-current-noise.json".format(apk_sha256)).exists()
    assert not (cache_dir / "{}__v1.json".format(apk_sha256)).exists()


def test_noise_cache_get_rejects_deletes_and_logs_outdated_profile_version(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    cache_dir = tmp_path / "noise-cache"
    manifest_path = _write_manifest(tmp_path, cache_dir, version="v-current-noise")
    monkeypatch.setattr(cache_manifest, "DEFAULT_MANIFEST_PATH", manifest_path)
    apk_sha256 = _sha256()
    cache = NoiseCache(cache_dir)
    outdated = cache_dir / "{}__v-old-noise.json".format(apk_sha256)
    outdated.write_text(json.dumps(_envelope(status="stale")), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert cache.get(apk_sha256) is None

    assert not outdated.exists()
    assert "noise_cache_outdated: sha256={}".format(apk_sha256) in caplog.text


def test_cache_manifest_invalidate_outdated_noise_deletes_all_old_versions(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "noise-cache"
    cache_dir.mkdir()
    manifest_path = _write_manifest(tmp_path, cache_dir, version="v-current-noise")
    current = cache_dir / "{}__v-current-noise.json".format(_sha256(b"current"))
    outdated_a = cache_dir / "{}__v-old-a.json".format(_sha256(b"old-a"))
    outdated_b = cache_dir / "{}__v-old-b.json".format(_sha256(b"old-b"))
    for path in (current, outdated_a, outdated_b):
        path.write_text("{}", encoding="utf-8")

    result = cache_manifest.invalidate_outdated("noise", manifest_path=manifest_path)

    assert result["deleted"] == sorted([str(outdated_a), str(outdated_b)])
    assert current.exists()
    assert not outdated_a.exists()
    assert not outdated_b.exists()


def test_apply_libloom_detection_recomputes_outdated_cache_hit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache_dir = tmp_path / "noise-cache"
    manifest_path = _write_manifest(tmp_path, cache_dir, version="v-current-noise")
    monkeypatch.setattr(cache_manifest, "DEFAULT_MANIFEST_PATH", manifest_path)
    apk_bytes = b"same-apk-body"
    apk_path = tmp_path / "app.apk"
    apk_path.write_bytes(apk_bytes)
    apk_sha256 = _sha256(apk_bytes)
    outdated = cache_dir / "{}__v-old-noise.json".format(apk_sha256)
    cache_dir.mkdir()
    outdated.write_text(json.dumps(_envelope(status="stale")), encoding="utf-8")
    cache = NoiseCache(cache_dir)

    with mock.patch.object(
        libloom_adapter,
        "verify_libloom_setup",
        return_value={
            "status": "available",
            "jar_path": "/opt/LIBLOOM.jar",
            "libs_profile_dir": "/opt/libs_profile",
            "reason": "",
        },
    ), mock.patch.object(
        libloom_adapter,
        "detect_libraries",
        return_value={
            "status": "ok",
            "libraries": [{"name": "new-lib"}],
            "elapsed_sec": 0.25,
            "error_reason": None,
        },
    ) as detect:
        merged = noise_profile_envelope.apply_libloom_detection(
            apk_path=str(apk_path),
            apkid_result={"gate_status": "clean"},
            libloom_jar_path="/opt/LIBLOOM.jar",
            libs_profile_dir="/opt/libs_profile",
            envelope={"schema_version": "nc-v1", "status": "success"},
            cache=cache,
        )

    assert detect.call_count == 1
    assert merged["libloom_libraries"] == [{"name": "new-lib"}]
    assert not outdated.exists()
    assert (cache_dir / "{}__v-current-noise.json".format(apk_sha256)).exists()

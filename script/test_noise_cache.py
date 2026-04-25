#!/usr/bin/env python3
"""Tests for persistent NoiseProfileEnvelope cache."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest import mock

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


def test_noise_cache_put_then_get_returns_identical_envelope(tmp_path: Path) -> None:
    cache = NoiseCache(tmp_path, profile_version="v1")
    apk_sha256 = _sha256()
    envelope = _envelope()

    assert cache.get(apk_sha256) is None

    cache.put(apk_sha256, envelope)

    assert cache.get(apk_sha256) == envelope


def test_noise_cache_separates_entries_by_profile_version(tmp_path: Path) -> None:
    apk_sha256 = _sha256()
    cache_v1 = NoiseCache(tmp_path, profile_version="v1")
    cache_v2 = NoiseCache(tmp_path, profile_version="v2")

    cache_v1.put(apk_sha256, _envelope(status="success"))
    cache_v2.put(apk_sha256, _envelope(status="partial"))

    assert cache_v1.get(apk_sha256) == _envelope(status="success")
    assert cache_v2.get(apk_sha256) == _envelope(status="partial")


def test_apply_libloom_detection_cache_hit_skips_detect_libraries(tmp_path: Path) -> None:
    apk_bytes = b"same-apk-body"
    apk_path = tmp_path / "app.apk"
    apk_path.write_bytes(apk_bytes)
    apk_sha256 = _sha256(apk_bytes)
    cached = _envelope(status="success")
    cache = NoiseCache(tmp_path / "cache", profile_version="v1")
    cache.put(apk_sha256, cached)

    with mock.patch.object(libloom_adapter, "detect_libraries") as detect:
        merged = noise_profile_envelope.apply_libloom_detection(
            apk_path=str(apk_path),
            apkid_result={"gate_status": "clean"},
            libloom_jar_path="/opt/LIBLOOM.jar",
            libs_profile_dir="/opt/libs_profile",
            envelope={"schema_version": "nc-v1", "status": "success"},
            cache=cache,
        )

    assert merged == cached
    assert detect.call_count == 0


def test_noise_cache_corrupted_json_returns_none_and_logs_warning(
    tmp_path: Path,
    caplog,
) -> None:
    apk_sha256 = _sha256()
    cache = NoiseCache(tmp_path, profile_version="v1")
    (tmp_path / "{}__v1.json".format(apk_sha256)).write_text("{broken", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert cache.get(apk_sha256) is None

    assert "corrupted JSON" in caplog.text

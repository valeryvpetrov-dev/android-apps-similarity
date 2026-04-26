#!/usr/bin/env python3
"""Persistent JSON cache for NoiseProfileEnvelope payloads."""
from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


class NoiseCache:
    """Disk-backed cache keyed by (APK SHA-256, profile_version)."""

    def __init__(self, cache_dir: Path, profile_version: str = "v1") -> None:
        self.cache_dir = Path(cache_dir).expanduser()
        self.profile_version = self._validate_profile_version(profile_version)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_sha256(apk_sha256: str) -> str:
        if len(apk_sha256) != 64:
            raise ValueError("apk_sha256 must be a 64-char hex digest")
        int(apk_sha256, 16)
        return apk_sha256.lower()

    @staticmethod
    def _validate_profile_version(profile_version: str) -> str:
        if not profile_version or not profile_version.strip():
            raise ValueError("profile_version must be a non-empty string")
        if "/" in profile_version or "\\" in profile_version:
            raise ValueError("profile_version must not contain path separators")
        return profile_version

    def _path(self, apk_sha256: str, profile_version: str | None = None) -> Path:
        digest = self._validate_sha256(apk_sha256)
        version = self._validate_profile_version(profile_version or self.profile_version)
        return self.cache_dir / "{}__{}.json".format(digest, version)

    def get(self, apk_sha256: str, profile_version: str | None = None) -> dict | None:
        path = self._path(apk_sha256, profile_version)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(
                "NoiseCache: corrupted JSON for sha256=%s profile_version=%s",
                apk_sha256,
                profile_version or self.profile_version,
            )
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "NoiseCache: payload is not a dict for sha256=%s profile_version=%s",
                apk_sha256,
                profile_version or self.profile_version,
            )
            return None
        return payload

    def put(
        self,
        apk_sha256: str,
        envelope: dict | str,
        profile_version: str | dict | None = None,
    ) -> None:
        if isinstance(envelope, str):
            if not isinstance(profile_version, dict):
                raise TypeError("NoiseCache.put expects envelope as dict")
            envelope, profile_version = profile_version, envelope
        if not isinstance(envelope, dict):
            raise TypeError("NoiseCache.put expects envelope as dict")
        if profile_version is not None and not isinstance(profile_version, str):
            raise TypeError("profile_version must be a string")

        path = self._path(apk_sha256, profile_version)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(envelope, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)


__all__ = ["NoiseCache"]

#!/usr/bin/env python3
"""Persistent JSON cache for NoiseProfileEnvelope payloads."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from script import cache_manifest


logger = logging.getLogger(__name__)


class NoiseCache:
    """Disk-backed cache keyed by (APK SHA-256, profile_version)."""

    def __init__(self, cache_dir: Path, profile_version: str | None = None) -> None:
        self.cache_dir = Path(cache_dir).expanduser()
        self._uses_manifest_profile_version = profile_version is None
        if self._uses_manifest_profile_version:
            self.profile_version = self._current_profile_version()
        else:
            self.profile_version = self._validate_profile_version(profile_version)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self._uses_manifest_profile_version:
            self.invalidate_all_outdated()

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

    @classmethod
    def _current_profile_version(cls) -> str:
        manifest = cache_manifest.load()
        return cls._validate_profile_version(manifest["noise"]["version"])

    def _active_profile_version(self, profile_version: str | None = None) -> str:
        if profile_version is not None:
            return self._validate_profile_version(profile_version)
        if self._uses_manifest_profile_version:
            self.profile_version = self._current_profile_version()
        return self.profile_version

    def _path(self, apk_sha256: str, profile_version: str | None = None) -> Path:
        digest = self._validate_sha256(apk_sha256)
        version = self._active_profile_version(profile_version)
        return self.cache_dir / "{}__{}.json".format(digest, version)

    def get(self, apk_sha256: str, profile_version: str | None = None) -> dict | None:
        digest = self._validate_sha256(apk_sha256)
        if self._uses_manifest_profile_version and profile_version is None:
            self._delete_outdated_for_sha256(digest)
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

    def invalidate_all_outdated(self) -> list[str]:
        result = cache_manifest.invalidate_outdated("noise")
        return list(result["deleted"])

    def _delete_outdated_for_sha256(self, apk_sha256: str) -> list[Path]:
        deleted: list[Path] = []
        for path in sorted(self.cache_dir.glob("{}__*.json".format(apk_sha256))):
            profile_version = self._profile_version_from_path(path)
            if profile_version is None:
                continue
            record = {"sha256": apk_sha256, "profile_version": profile_version}
            try:
                cache_manifest.validate_cache_record("noise", record)
            except cache_manifest.CacheManifestMismatch:
                path.unlink()
                deleted.append(path)
                logger.warning(
                    "noise_cache_outdated: sha256=%s profile_version=%s path=%s",
                    apk_sha256,
                    profile_version,
                    path,
                )
        return deleted

    @staticmethod
    def _profile_version_from_path(path: Path) -> str | None:
        stem = path.stem
        if "__" not in stem:
            return None
        _, profile_version = stem.split("__", 1)
        return profile_version

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

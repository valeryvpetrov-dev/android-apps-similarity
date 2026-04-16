#!/usr/bin/env python3
"""Helpers for machine-local shared datasets and caches.

The shared store is intentionally outside git worktrees so multiple agents and
branches on the same machine can reuse large APK corpora, decoded dirs and
feature caches.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


SHARED_DATA_ROOT_ENV = "PHD_SHARED_DATA_ROOT"
DEFAULT_SHARED_DATA_ROOT = Path.home() / "Library" / "Caches" / "phd-shared"
SHARED_REF_PREFIX = "shared://"


def get_shared_data_root() -> Path:
    raw = os.environ.get(SHARED_DATA_ROOT_ENV)
    base = Path(raw).expanduser() if raw else DEFAULT_SHARED_DATA_ROOT
    return base.resolve()


def shared_path(*parts: str) -> Path:
    return get_shared_data_root().joinpath(*parts)


def shared_dataset_dir(dataset_name: str) -> Path:
    return shared_path("datasets", dataset_name)


def shared_artifact_dir(*parts: str) -> Path:
    return shared_path("artifacts", *parts)


def shared_feature_cache_dir(experiment_id: str) -> Path:
    return shared_artifact_dir(experiment_id, "feature_cache")


def shared_apktool_cache_root(namespace: str = "default") -> Path:
    return shared_path("decoded-cache", sanitize_token(namespace))


def fdroid_v2_apks_dir() -> Path:
    return shared_dataset_dir("fdroid-corpus-v2-apks")


def fdroid_v2_decoded_dir() -> Path:
    return shared_dataset_dir("fdroid-corpus-v2-decoded")


def sanitize_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    token = token.strip("-._")
    return token or "default"


def resolve_path_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith(SHARED_REF_PREFIX):
        relative = value[len(SHARED_REF_PREFIX):].lstrip("/")
        return str((get_shared_data_root() / relative).resolve())
    return value


def build_shared_ref(path: Path) -> str:
    root = get_shared_data_root()
    relative = path.resolve().relative_to(root)
    return "{}{}".format(SHARED_REF_PREFIX, relative.as_posix())


def discover_apk_by_stem(stem: str, apk_dir: Path | None = None) -> str | None:
    base = apk_dir or fdroid_v2_apks_dir()
    if not base.is_dir():
        return None
    for apk_path in sorted(base.glob("*.apk")):
        if apk_path.stem == stem:
            return str(apk_path.resolve())
    return None


def discover_decoded_dir_by_stem(stem: str, decoded_root: Path | None = None) -> str | None:
    base = decoded_root or fdroid_v2_decoded_dir()
    candidate = base / stem
    if candidate.is_dir():
        return str(candidate.resolve())
    return None

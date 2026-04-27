"""Install a LIBLOOM runtime layout and verify it.

NOISE-28-LIBLOOM-INSTALL-AUTO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

try:
    from script import libloom_adapter
except ImportError:
    import libloom_adapter  # type: ignore[no-redef]


DEFAULT_TARGET_DIR = "~/.cache/phd-similarity/libloom"
DEFAULT_PROFILE_VERSION = "v1"
LIBLOOM_UPSTREAM_COMMIT = "unknown-noise-28-placeholder"
LIBLOOM_UPSTREAM_URL = (
    "https://example.invalid/libloom/"
    f"{LIBLOOM_UPSTREAM_COMMIT}/LIBLOOM-runtime.zip"
)
LIBLOOM_UPSTREAM_SHA256: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copytree_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _find_first_file(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_file():
            return path
    return None


def _find_profile_dir(root: Path) -> Path | None:
    candidates = []
    for path in root.rglob("*"):
        if path.is_dir() and path.name in {"libs_profile", "libloom_libs_profile"}:
            candidates.append(path)
    for candidate in sorted(candidates):
        try:
            if any(candidate.iterdir()):
                return candidate
        except OSError:
            continue
    return None


def _materialize_runtime(download_path: Path, install_home: Path) -> None:
    staging_root = install_home.parent / f".{install_home.name}.staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)

    try:
        unpacked_root = staging_root / "unpacked"
        unpacked_root.mkdir()
        if zipfile.is_zipfile(download_path):
            with zipfile.ZipFile(download_path) as archive:
                archive.extractall(unpacked_root)
        else:
            shutil.copy2(
                download_path,
                unpacked_root / libloom_adapter.LIBLOOM_JAR_NAME,
            )

        jar_path = _find_first_file(unpacked_root, libloom_adapter.LIBLOOM_JAR_NAME)
        profiles_dir = _find_profile_dir(unpacked_root)
        if jar_path is None:
            raise ValueError("downloaded LIBLOOM archive does not contain LIBLOOM.jar")
        if profiles_dir is None:
            raise ValueError(
                "downloaded LIBLOOM archive does not contain a non-empty libs_profile"
            )

        runtime_root = staging_root / "runtime"
        runtime_root.mkdir()
        shutil.copy2(jar_path, runtime_root / libloom_adapter.LIBLOOM_JAR_NAME)
        _copytree_contents(
            profiles_dir,
            runtime_root / libloom_adapter.LIBLOOM_PROFILE_DIR_NAME,
        )

        if install_home.exists():
            shutil.rmtree(install_home)
        install_home.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(runtime_root), str(install_home))
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def install_libloom(
    *,
    target_dir: str | os.PathLike[str] = DEFAULT_TARGET_DIR,
    profile_version: str = DEFAULT_PROFILE_VERSION,
    source_url: str = LIBLOOM_UPSTREAM_URL,
    expected_sha256: str | None = LIBLOOM_UPSTREAM_SHA256,
) -> dict[str, Any]:
    """Download, normalize, and smoke-test a LIBLOOM runtime directory."""
    target_root = Path(target_dir).expanduser()
    install_home = target_root / profile_version
    target_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="libloom-download-",
        dir=str(target_root),
    ) as tmp:
        download_path = Path(tmp) / "libloom-download"
        try:
            urllib.request.urlretrieve(source_url, str(download_path))
            if expected_sha256 is not None:
                actual_sha256 = _sha256(download_path)
                if actual_sha256.lower() != expected_sha256.lower():
                    raise ValueError(
                        "LIBLOOM download sha256 mismatch: "
                        f"expected {expected_sha256}, got {actual_sha256}"
                    )
            _materialize_runtime(download_path, install_home)
        except (
            OSError,
            urllib.error.URLError,
            ValueError,
            zipfile.BadZipFile,
            shutil.Error,
        ) as exc:
            return {
                "status": "failed",
                "libloom_home": None,
                "export": None,
                "source_url": source_url,
                "profile_version": profile_version,
                "error": f"failed to install LIBLOOM from {source_url}: {exc}",
                "verification": None,
            }

    verification = libloom_adapter.verify_libloom_setup(libloom_home=str(install_home))
    status = "installed" if verification.get("available") else "failed"
    return {
        "status": status,
        "libloom_home": str(install_home),
        "export": f"export LIBLOOM_HOME={install_home}",
        "source_url": source_url,
        "profile_version": profile_version,
        "error": None if status == "installed" else verification.get("reason"),
        "verification": verification,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download LIBLOOM, set up LIBLOOM_HOME layout, and run smoke-test."
    )
    parser.add_argument(
        "--target_dir",
        default=DEFAULT_TARGET_DIR,
        help=f"install root, default: {DEFAULT_TARGET_DIR}",
    )
    parser.add_argument(
        "--profile_version",
        default=DEFAULT_PROFILE_VERSION,
        help=f"profile subdirectory, default: {DEFAULT_PROFILE_VERSION}",
    )
    parser.add_argument(
        "--source-url",
        default=LIBLOOM_UPSTREAM_URL,
        help="override LIBLOOM runtime archive/JAR URL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = install_libloom(
        target_dir=args.target_dir,
        profile_version=args.profile_version,
        source_url=args.source_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["libloom_home"]:
        print(result["export"], file=sys.stderr)
    return 0 if result["status"] == "installed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

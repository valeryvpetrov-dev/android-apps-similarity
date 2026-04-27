"""TDD for NOISE-28-LIBLOOM-INSTALL-AUTO."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from script import install_libloom
from script import libloom_adapter


def _make_libloom_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("LIBLOOM/artifacts/LIBLOOM.jar", b"fake jar")
        archive.writestr("LIBLOOM/libs_profile/okhttp3/profile.json", "{}")


def test_install_libloom_downloads_archive_and_creates_runtime_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_zip = tmp_path / "source.zip"
    _make_libloom_zip(source_zip)
    target_dir = tmp_path / "libloom"

    def fake_urlretrieve(url: str, filename: str):
        assert url == "https://example.test/libloom.zip"
        shutil.copyfile(source_zip, filename)
        return filename, None

    monkeypatch.setattr(install_libloom.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(libloom_adapter.shutil, "which", lambda _: "/usr/bin/java")

    result = install_libloom.install_libloom(
        target_dir=target_dir,
        profile_version="v1",
        source_url="https://example.test/libloom.zip",
    )

    assert result["status"] == "installed"
    assert result["libloom_home"] == str(target_dir / "v1")
    assert (target_dir / "v1" / "LIBLOOM.jar").is_file()
    assert (target_dir / "v1" / "libs_profile" / "okhttp3" / "profile.json").is_file()
    assert result["export"] == f"export LIBLOOM_HOME={target_dir / 'v1'}"


def test_install_libloom_returns_explicit_failure_when_download_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_urlretrieve(url: str, filename: str):
        raise OSError("network unreachable")

    monkeypatch.setattr(install_libloom.urllib.request, "urlretrieve", fail_urlretrieve)

    result = install_libloom.install_libloom(
        target_dir=tmp_path / "libloom",
        profile_version="v1",
        source_url="https://example.test/missing.zip",
    )

    assert result["status"] == "failed"
    assert result["libloom_home"] is None
    assert "https://example.test/missing.zip" in result["error"]
    assert "network unreachable" in result["error"]


def test_installed_layout_passes_verify_libloom_setup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_zip = tmp_path / "source.zip"
    _make_libloom_zip(source_zip)

    monkeypatch.setattr(
        install_libloom.urllib.request,
        "urlretrieve",
        lambda url, filename: (shutil.copyfile(source_zip, filename), None),
    )
    monkeypatch.setattr(libloom_adapter.shutil, "which", lambda _: "/usr/bin/java")

    result = install_libloom.install_libloom(
        target_dir=tmp_path / "libloom",
        profile_version="v1",
        source_url="https://example.test/libloom.zip",
    )

    verification = libloom_adapter.verify_libloom_setup(
        libloom_home=result["libloom_home"]
    )
    assert verification["available"] is True
    assert verification["status"] == "available"

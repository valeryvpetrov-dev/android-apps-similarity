"""TDD for NOISE-21-DEPENDENCY-POLICY."""
from __future__ import annotations

from pathlib import Path

from script import libloom_adapter
from script import noise_profile_envelope
from script import run_libloom_quality_smoke as rq


def _create_apk(apk_dir: Path, name: str) -> Path:
    apk_path = apk_dir / name
    apk_path.write_bytes(b"fake apk")
    return apk_path


def test_verify_libloom_setup_returns_unavailable_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("LIBLOOM_HOME", raising=False)

    verification = libloom_adapter.verify_libloom_setup()

    assert verification["status"] == "unavailable"
    assert verification["available"] is False
    assert verification["version"] is None
    assert "LIBLOOM_HOME is not set" in verification["reason"]


def test_verify_libloom_setup_returns_available_with_explicit_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    libloom_home = tmp_path / "libloom-home"
    libs_profile = libloom_home / "libs_profile"
    libs_profile.mkdir(parents=True)
    (libs_profile / "okhttp3").mkdir()
    (libloom_home / "LIBLOOM.jar").write_bytes(b"fake jar")
    monkeypatch.setenv("LIBLOOM_HOME", str(libloom_home))
    monkeypatch.setattr(libloom_adapter.shutil, "which", lambda _: "/usr/bin/java")

    verification = libloom_adapter.verify_libloom_setup()

    assert verification["status"] == "available"
    assert verification["available"] is True
    assert verification["reason"] == "available"
    assert verification["version"] is None or isinstance(verification["version"], str)


def test_verify_libloom_setup_returns_misconfigured_when_jar_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    libloom_home = tmp_path / "libloom-home"
    libs_profile = libloom_home / "libs_profile"
    libs_profile.mkdir(parents=True)
    (libs_profile / "okhttp3").mkdir()
    monkeypatch.setenv("LIBLOOM_HOME", str(libloom_home))

    verification = libloom_adapter.verify_libloom_setup()

    assert verification["status"] == "misconfigured"
    assert verification["available"] is False
    assert verification["version"] is None
    assert "LIBLOOM.jar not found" in verification["reason"]


def test_apply_libloom_detection_marks_unavailable_instead_of_not_configured(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LIBLOOM_HOME", raising=False)

    merged = noise_profile_envelope.apply_libloom_detection(
        apk_path="/tmp/app.apk",
        apkid_result={"gate_status": "clean", "recommended_detector": "libloom"},
        libloom_jar_path=None,
        libs_profile_dir=None,
        envelope={"schema_version": "nc-v1"},
    )

    assert merged["libloom_status"] == "libloom_unavailable"
    assert "LIBLOOM_HOME is not set" in merged["libloom_error_reason"]


def test_run_real_quality_smoke_writes_unavailable_status_into_all_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    first_apk = _create_apk(apk_dir, "first.apk")
    second_apk = _create_apk(apk_dir, "second.apk")
    monkeypatch.delenv("LIBLOOM_HOME", raising=False)

    report = rq.run_real_quality_smoke(
        apk_dir=str(apk_dir),
        labels_path=None,
        output_path=None,
    )

    assert report["status"] == "libloom_unavailable"
    assert [entry["apk_path"] for entry in report["per_apk_results"]] == [
        str(first_apk),
        str(second_apk),
    ]
    assert {entry["libloom_status"] for entry in report["per_apk_results"]} == {
        "libloom_unavailable"
    }


def test_run_real_quality_smoke_writes_misconfigured_status_when_home_is_broken(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    sample_apk = _create_apk(apk_dir, "sample.apk")
    libloom_home = tmp_path / "libloom-home"
    libs_profile = libloom_home / "libs_profile"
    libs_profile.mkdir(parents=True)
    (libs_profile / "okhttp3").mkdir()
    monkeypatch.setenv("LIBLOOM_HOME", str(libloom_home))

    report = rq.run_real_quality_smoke(
        apk_dir=str(apk_dir),
        labels_path=None,
        output_path=None,
    )

    assert report["status"] == "libloom_misconfigured"
    assert report["per_apk_results"] == [
        {
            "apk_path": str(sample_apk),
            "ground_truth": [],
            "detected_tpls": [],
            "libloom_status": "libloom_misconfigured",
            "libloom_error_reason": report["reason"],
            "libloom_elapsed_sec": 0.0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "tp_names": [],
            "fp_names": [],
            "fn_names": [],
        }
    ]


def test_readme_and_todo_reference_same_canonical_policy_document() -> None:
    readme_path = Path("README.md")
    adapter_text = Path("script/libloom_adapter.py").read_text(encoding="utf-8")

    assert readme_path.is_file()
    readme_text = readme_path.read_text(encoding="utf-8")
    assert "## LIBLOOM dependency policy" in readme_text
    assert "TODO(NOISE-21-DEPENDENCY-POLICY): see README.md#libloom-dependency-policy" in adapter_text

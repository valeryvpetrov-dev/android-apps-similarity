#!/usr/bin/env python3
"""TDD tests for SYS-31-MULTIDEX-V4-REGRESSION."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for candidate in (SCRIPT_DIR, PROJECT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from run_multidex_restructure_bench import (  # noqa: E402
    ARTIFACT_ID,
    DEFAULT_CLAIM_THRESHOLD,
    build_multidex_restructured_apk,
    build_report,
    compare_apk_pair,
    write_report,
)
from script import code_view_v4  # noqa: E402


def _apktool_or_skip() -> Path:
    apktool = shutil.which("apktool") or "/opt/homebrew/bin/apktool"
    path = Path(apktool)
    if not path.exists():
        pytest.skip("apktool is required for synthetic APK rebuild test")
    return path


def _write_synthetic_decoded_project(decoded_dir: Path, *, class_count: int) -> None:
    decoded_dir.mkdir(parents=True, exist_ok=True)
    (decoded_dir / "AndroidManifest.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.sys31">
    <application android:theme="@style/AppTheme" />
</manifest>
""",
        encoding="utf-8",
    )
    values_dir = decoded_dir / "res" / "values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "styles.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme" parent="@android:style/Theme.Material.Light.NoActionBar" />
</resources>
""",
        encoding="utf-8",
    )
    smali_dir = decoded_dir / "smali" / "com" / "example" / "sys31"
    smali_dir.mkdir(parents=True, exist_ok=True)
    for index in range(class_count):
        (smali_dir / "C{:02d}.smali".format(index)).write_text(
            """.class public final Lcom/example/sys31/C{index:02d};
.super Ljava/lang/Object;

.method public static value()I
    .locals 1
    const/16 v0, 0x{value:x}
    return v0
.end method
""".format(index=index, value=index % 16),
            encoding="utf-8",
        )
    (decoded_dir / "apktool.yml").write_text(
        """!!brut.androlib.meta.MetaInfo
apkFileName: sys31.apk
compressionType: false
doNotCompress: []
isFrameworkApk: false
packageInfo:
  forcedPackageId: 127
sdkInfo:
  minSdkVersion: '23'
  targetSdkVersion: '35'
sharedLibrary: false
unknownFiles: {}
usesFramework:
  ids:
  - 1
  tag: null
version: 2.9.3
versionInfo: {}
""",
        encoding="utf-8",
    )


def test_synthetic_single_dex_restructure_keeps_code_view_v4_high(tmp_path: Path) -> None:
    apktool = _apktool_or_skip()
    source_decoded = tmp_path / "source_decoded"
    _write_synthetic_decoded_project(source_decoded, class_count=10)
    unsigned_apk = tmp_path / "single.apk"
    signed_apk = tmp_path / "single-signed.apk"
    work_dir = tmp_path / "work"

    from run_multidex_restructure_bench import run_command, sign_apk

    run_command(
        [str(apktool), "build", str(source_decoded), "--output", str(unsigned_apk)],
        stage="apktool_build",
    )
    sign_apk(unsigned_apk, signed_apk, tmp_path / "sys31.keystore.jks")

    result = build_multidex_restructured_apk(
        signed_apk,
        output_dir=tmp_path / "out",
        work_dir=work_dir,
        apktool_path=apktool,
        keystore_path=tmp_path / "sys31-restructured.keystore.jks",
    )

    with zipfile.ZipFile(result["restructured_apk"]) as archive:
        assert "classes.dex" in archive.namelist()
        assert "classes2.dex" in archive.namelist()

    original_features = code_view_v4.extract_code_view_v4(signed_apk)
    restructured_features = code_view_v4.extract_code_view_v4(
        Path(result["restructured_apk"])
    )
    assert original_features is not None
    assert restructured_features is not None
    assert original_features["total_methods"] == 10
    assert restructured_features["total_methods"] == 10
    assert result["primary_smali_files"] == 5
    assert result["secondary_smali_files"] == 5

    comparison = code_view_v4.compare_code_v4(
        original_features,
        restructured_features,
    )
    assert comparison["score"] >= DEFAULT_CLAIM_THRESHOLD


def test_fully_different_dex_features_score_below_threshold() -> None:
    left = {
        "method_fingerprints": {
            "Lcom/example/a/C{:02d};->value()I".format(i): "S:{:016x}".format(i)
            for i in range(10)
        },
        "total_methods": 10,
        "mode": code_view_v4.MODE,
    }
    right = {
        "method_fingerprints": {
            "Lcom/example/b/D{:02d};->value()I".format(i): "S:{:016x}".format(i + 100)
            for i in range(10)
        },
        "total_methods": 10,
        "mode": code_view_v4.MODE,
    }

    comparison = code_view_v4.compare_code_v4(left, right)

    assert comparison["score"] < 0.3
    assert comparison["matched_methods"] == 0


def test_report_artifact_structure_matches_expected_schema(tmp_path: Path) -> None:
    pair_rows = [
        compare_apk_pair(
            pair_id="synthetic-high",
            original_apk=Path("/tmp/original.apk"),
            restructured_apk=Path("/tmp/restructured.apk"),
            features_a={
                "method_fingerprints": {"Lx/A;->a()V": "S:1111111111111111"},
                "total_methods": 1,
                "mode": code_view_v4.MODE,
            },
            features_b={
                "method_fingerprints": {"Lx/A;->a()V": "S:1111111111111111"},
                "total_methods": 1,
                "mode": code_view_v4.MODE,
            },
        )
    ]
    failed = [{"apk_path": "/tmp/bad.apk", "stage": "apktool_decode", "error": "boom"}]

    report = build_report(
        pair_rows,
        failed_apks=failed,
        selected_apks=["/tmp/original.apk", "/tmp/bad.apk"],
        n_requested_pairs=2,
    )
    out_path = write_report(tmp_path / "report.json", report)
    loaded = json.loads(out_path.read_text(encoding="utf-8"))

    assert {
        "schema_version",
        "artifact_id",
        "generated_at_utc",
        "config",
        "selected_apks",
        "n_pairs_total",
        "n_successful",
        "n_failed",
        "failed_apks",
        "per_pair_score",
        "mean_score",
        "min_score",
        "max_score",
        "claim_supported",
    }.issubset(loaded.keys())
    assert loaded["artifact_id"] == ARTIFACT_ID
    assert loaded["n_pairs_total"] == 2
    assert loaded["n_successful"] == 1
    assert loaded["n_failed"] == 1
    assert loaded["failed_apks"] == failed
    assert loaded["per_pair_score"][0]["score"] == 1.0
    assert loaded["mean_score"] == 1.0
    assert loaded["min_score"] == 1.0
    assert loaded["max_score"] == 1.0
    assert loaded["claim_supported"] is True

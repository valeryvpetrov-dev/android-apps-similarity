#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
APK_ROOT = PROJECT_ROOT / "apk"
CALIBRATE_SCRIPT = PROJECT_ROOT / "script" / "calibrate_tlsh_roc.py"

EXPECTED_TLSH_GRID = [100, 150, 200, 250, 300]
EXPECTED_SHINGLE_GRID = [3, 4, 5, 6]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("SIMILARITY_SKIP_REQ_CHECK", "1")
    return subprocess.run(
        [sys.executable, str(CALIBRATE_SCRIPT), *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_report_shape(report: dict) -> None:
    assert report["tlsh_diff_max_grid"] == EXPECTED_TLSH_GRID
    assert report["shingle_size_grid"] == EXPECTED_SHINGLE_GRID
    assert isinstance(report["per_param_metrics"], list)
    assert isinstance(report["optimal"], dict)
    assert isinstance(report["corpus_size"], int)
    assert isinstance(report["pairs_clone"], int)
    assert isinstance(report["pairs_non_clone"], int)

    required_metric_keys = {
        "tlsh_diff_max",
        "shingle_size",
        "precision",
        "recall",
        "f1",
        "fpr",
        "tpr",
    }
    assert len(report["per_param_metrics"]) == (
        len(EXPECTED_TLSH_GRID) * len(EXPECTED_SHINGLE_GRID)
    )
    for metric in report["per_param_metrics"]:
        assert required_metric_keys.issubset(metric)

    required_optimal_keys = {
        "tlsh_diff_max",
        "shingle_size",
        "by_f1",
        "by_youden_j",
    }
    assert required_optimal_keys.issubset(report["optimal"])


def test_calibrate_cli_creates_report_on_mini_corpus(tmp_path: Path) -> None:
    out_path = tmp_path / "report.json"

    completed = _run_cli(
        "--corpus_dir",
        str(APK_ROOT),
        "--out",
        str(out_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert out_path.exists(), completed.stderr
    report = _load_json(out_path)
    _assert_report_shape(report)
    assert report["corpus_size"] >= 4
    assert report["pairs_clone"] >= 1
    assert report["pairs_non_clone"] >= 1


def test_calibrate_cli_gracefully_degrades_on_insufficient_corpus(
    tmp_path: Path,
) -> None:
    corpus_dir = tmp_path / "tiny_corpus"
    corpus_dir.mkdir()
    sample_apks = sorted(APK_ROOT.rglob("*.apk"))[:3]
    assert len(sample_apks) == 3
    for apk_path in sample_apks:
        shutil.copy2(apk_path, corpus_dir / apk_path.name)

    out_path = tmp_path / "insufficient-report.json"
    completed = _run_cli(
        "--corpus_dir",
        str(corpus_dir),
        "--out",
        str(out_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert out_path.exists(), completed.stderr
    report = _load_json(out_path)
    assert report["status"] == "insufficient_corpus"
    assert "warning" in report


def test_report_json_matches_expected_schema(tmp_path: Path) -> None:
    out_path = tmp_path / "schema-report.json"

    completed = _run_cli(
        "--corpus_dir",
        str(APK_ROOT),
        "--out",
        str(out_path),
    )

    assert completed.returncode == 0, completed.stderr
    report = _load_json(out_path)
    _assert_report_shape(report)

#!/usr/bin/env python3
"""TDD tests for SCREENING-20-LSH-DIAGNOSTIC."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_screening_diagnostic as diagnostic


def _make_app(app_id: str, code_features: set[str]) -> dict:
    return {
        "app_id": app_id,
        "layers": {
            "code": set(code_features),
            "component": set(),
            "resource": set(),
            "metadata": set(),
            "library": set(),
        },
    }


def _write_config(
    tmp_path: Path,
    *,
    threshold: float = 0.28,
    num_perm: int = 16,
    bands: int = 8,
    seed: int = 1,
) -> Path:
    config_path = tmp_path / "cascade.yaml"
    config_path.write_text(
        "\n".join(
            [
                "cascade_config_version: v1",
                "stages:",
                "  screening:",
                "    features: [code]",
                "    metric: jaccard",
                "    threshold: {}".format(threshold),
                "    candidate_index:",
                "      type: minhash_lsh",
                "      num_perm: {}".format(num_perm),
                "      bands: {}".format(bands),
                "      seed: {}".format(seed),
                "      features: [code]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_smoke_cli_writes_report_json_with_required_fields(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, threshold=0.0, num_perm=128, bands=32, seed=42)
    output_dir = tmp_path / "artifact"

    env = dict(os.environ)
    env["SIMILARITY_SKIP_REQ_CHECK"] = "1"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "run_screening_diagnostic.py"),
            "--apk-root",
            str(REPO_ROOT / "apk"),
            "--cascade-config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        check=True,
        cwd=str(REPO_ROOT),
        env=env,
    )

    report_path = output_dir / "report.json"
    assert report_path.exists(), "CLI must create report.json"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "screening-diagnostic-v1"
    assert "summary" in report
    assert "pairs" in report

    for field in (
        "total_pairs",
        "shortlist_size",
        "candidate_list_size",
        "recall_at_shortlist",
        "false_negative_rate",
        "avg_per_view_score_in_candidates",
    ):
        assert field in report["summary"], field

    first_pair = report["pairs"][0]
    for field in (
        "query_app_id",
        "candidate_app_id",
        "in_shortlist",
        "passed_thresh",
        "full_score",
        "selected_similarity_score",
        "per_view_scores",
    ):
        assert field in first_pair, field


def test_false_negative_pair_above_thresh_is_visible_in_recall_metrics(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, threshold=0.28, num_perm=16, bands=8, seed=1)
    app_records = [
        _make_app("APP-A", {"f0", "f1", "f2", "f3"}),
        _make_app("APP-B", {"f0", "f3", "f4", "f5"}),
    ]

    report = diagnostic.build_diagnostic_report(
        app_records=app_records,
        cascade_config_path=config_path,
    )

    assert report["summary"]["positive_pairs_above_threshold"] == 1
    assert report["summary"]["recall_at_shortlist"] < 1.0
    assert report["summary"]["false_negative_rate"] > 0.0

    pair = report["pairs"][0]
    assert pair["passed_thresh"] is True
    assert pair["in_shortlist"] is False
    assert pair["full_score"] > 0.28
    assert pair["selected_similarity_score"] == pytest.approx(0.0)


def test_below_threshold_pair_is_not_counted_as_shortlist_false_positive(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, threshold=0.28, num_perm=16, bands=8, seed=1)
    app_records = [
        _make_app("APP-NEG-A", {"f0", "f1", "f2", "f3"}),
        _make_app("APP-NEG-B", {"g0", "g1", "g2", "g3"}),
    ]

    report = diagnostic.build_diagnostic_report(
        app_records=app_records,
        cascade_config_path=config_path,
    )

    assert report["summary"]["negative_pairs_below_threshold"] == 1
    assert report["summary"]["shortlist_false_positive_count"] == 0
    assert report["summary"]["false_positive_rate"] == pytest.approx(0.0)

    pair = report["pairs"][0]
    assert pair["passed_thresh"] is False
    assert pair["in_shortlist"] is False
    assert pair["full_score"] < 0.28

#!/usr/bin/env python3
"""TDD tests for SCREENING-30-PACKAGE-RENAME-SYNTH-BENCH."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_package_rename_bench import (  # noqa: E402
    DEFAULT_LSH_PARAMS,
    build_recall_report_from_pairs,
    write_report,
)


def _record(app_id: str, signature: set[str]) -> dict:
    return {
        "app_id": app_id,
        "apk_path": "/tmp/{}.apk".format(app_id),
        "screening_signature": sorted(signature),
        "layers": {
            "code": set(),
            "component": set(),
            "resource": set(),
            "metadata": set(),
            "library": set(),
        },
    }


def _pair(pair_id: str, original_sig: set[str], shifted_sig: set[str]) -> dict:
    return {
        "pair_id": pair_id,
        "original_apk": "/tmp/{}-original.apk".format(pair_id),
        "shifted_apk": "/tmp/{}-shifted.apk".format(pair_id),
        "original_record": _record("{}-original".format(pair_id), original_sig),
        "shifted_record": _record("{}-shifted".format(pair_id), shifted_sig),
        "original_package": "com.original.{}".format(pair_id.replace("-", "")),
        "shifted_package": "com.fake.{}".format(pair_id.replace("-", "")),
    }


def test_identical_screening_signature_pair_is_in_lsh_shortlist() -> None:
    signature = {"code:classes.dex", "metadata:dex_count_bin:1", "resource:res_ext:xml"}

    report = build_recall_report_from_pairs(
        [_pair("same", signature, signature)],
        candidate_index_params=DEFAULT_LSH_PARAMS,
        failed_apks=[],
    )

    assert report["n_pairs"] == 1
    assert report["n_in_shortlist"] == 1
    assert report["recall"] == 1.0
    assert report["jaccard_per_pair"][0]["jaccard"] == 1.0
    assert report["top_3_lost_pairs"] == []


def test_disjoint_screening_signature_pair_is_not_in_lsh_shortlist() -> None:
    original_sig = {"left:{}".format(index) for index in range(32)}
    shifted_sig = {"right:{}".format(index) for index in range(32)}

    report = build_recall_report_from_pairs(
        [_pair("different", original_sig, shifted_sig)],
        candidate_index_params=DEFAULT_LSH_PARAMS,
        failed_apks=[],
    )

    assert report["n_pairs"] == 1
    assert report["n_in_shortlist"] == 0
    assert report["recall"] == 0.0
    assert report["jaccard_per_pair"][0]["jaccard"] == 0.0
    assert report["top_3_lost_pairs"][0]["pair_id"] == "different"


def test_report_artifact_structure_matches_expected_schema(tmp_path: Path) -> None:
    signature = {"code:classes.dex", "metadata:manifest_present:1"}
    failed = [{"apk_path": "/tmp/bad.apk", "stage": "apktool_decode", "error": "boom"}]

    report = build_recall_report_from_pairs(
        [_pair("schema", signature, signature)],
        candidate_index_params=DEFAULT_LSH_PARAMS,
        failed_apks=failed,
    )
    out_path = write_report(tmp_path / "report.json", report)
    loaded = json.loads(out_path.read_text(encoding="utf-8"))

    assert {
        "schema_version",
        "artifact_id",
        "generated_at_utc",
        "config",
        "n_pairs",
        "n_in_shortlist",
        "recall",
        "jaccard_per_pair",
        "screening_signature_diff_per_pair",
        "top_3_lost_pairs",
        "failed_apks",
    }.issubset(loaded.keys())
    assert loaded["artifact_id"] == "SCREENING-30-PACKAGE-RENAME"
    assert loaded["failed_apks"] == failed
    assert loaded["config"]["seed"] == 42
    assert loaded["screening_signature_diff_per_pair"][0]["original_only_count"] == 0
    assert loaded["screening_signature_diff_per_pair"][0]["shifted_only_count"] == 0

#!/usr/bin/env python3
"""SYS-INT-27-TIMEOUT-INCIDENT-V2: extended timeout incident schema."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import timeout_incident_registry as reg


def _base_pair_row() -> dict:
    return {
        "pair_id": "PAIR-V2-001",
        "app_a": "AppA",
        "app_b": "AppB",
        "duration_ms": 1234,
        "views_used": ["component", "resource"],
        "timeout_info": {
            "pair_timeout_sec": 7,
            "stage": "pairwise",
        },
    }


def test_record_timeout_incident_v2_writes_all_new_fields_with_explicit_types(
    tmp_path: Path,
) -> None:
    record = reg.record_timeout_incident_v2(
        _base_pair_row(),
        log_path=tmp_path / "timeout-incidents.jsonl",
    )

    assert record["schema_version"] == "timeout-incident-v2"
    assert isinstance(record["feature_cache_hit"], bool)
    assert isinstance(record["decoded_dirs_present"], bool)
    assert isinstance(record["worker_started"], bool)
    assert isinstance(record["queued_timeout"], bool)
    assert isinstance(record["tmp_cleanup_status"], str)
    assert record["external_tool"] is None or isinstance(record["external_tool"], str)


def test_read_timeout_incidents_migrates_v1_records_to_v2_defaults(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "timeout-incidents.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "schema_version": "timeout-incident-v1",
                "status": "timeout",
                "pair_id": "PAIR-V1-001",
                "app_a": "LegacyA",
                "app_b": "LegacyB",
                "duration_ms": 1000,
                "pair_timeout_sec": 5,
                "stage": "pairwise",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = reg.read_timeout_incidents(log_path=log_path)

    assert records == [
        {
            "schema_version": "timeout-incident-v2",
            "status": "timeout",
            "pair_id": "PAIR-V1-001",
            "app_a": "LegacyA",
            "app_b": "LegacyB",
            "duration_ms": 1000,
            "pair_timeout_sec": 5,
            "stage": "pairwise",
            "feature_cache_hit": False,
            "decoded_dirs_present": False,
            "worker_started": False,
            "queued_timeout": False,
            "tmp_cleanup_status": "unknown",
            "external_tool": None,
        }
    ]


def test_record_timeout_incident_v2_collects_context_from_pair_row(
    tmp_path: Path,
) -> None:
    pair_row = _base_pair_row()
    pair_row.update(
        {
            "feature_cache_hit": True,
            "decoded_dirs_present": True,
            "worker_started": True,
            "queued_timeout": True,
            "tmp_cleanup_status": "cancelled",
            "external_tool": "ProcessPoolExecutor",
        }
    )

    record = reg.record_timeout_incident_v2(
        pair_row,
        log_path=tmp_path / "timeout-incidents.jsonl",
    )

    assert record["feature_cache_hit"] is True
    assert record["decoded_dirs_present"] is True
    assert record["worker_started"] is True
    assert record["queued_timeout"] is True
    assert record["tmp_cleanup_status"] == "cancelled"
    assert record["external_tool"] == "ProcessPoolExecutor"


def test_validate_timeout_incident_record_checks_v2_field_types() -> None:
    record = {
        "schema_version": "timeout-incident-v2",
        "recorded_at": "2026-04-27T00:00:00+00:00",
        "status": "timeout",
        "pair_id": "PAIR-V2-001",
        "app_a": "AppA",
        "app_b": "AppB",
        "duration_ms": 1234,
        "pair_timeout_sec": 7,
        "stage": "pairwise",
        "views_used": [],
        "feature_cache_hit": False,
        "decoded_dirs_present": False,
        "worker_started": True,
        "queued_timeout": False,
        "tmp_cleanup_status": "unknown",
        "external_tool": None,
    }

    assert reg.validate_timeout_incident_record(record) is True

    invalid = dict(record)
    invalid["worker_started"] = "yes"
    with pytest.raises(reg.TimeoutIncidentSchemaError):
        reg.validate_timeout_incident_record(invalid)

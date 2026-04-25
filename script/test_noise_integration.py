#!/usr/bin/env python3
"""Tests for runtime truth of noise gate behaviour."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

os.environ.setdefault("SIMILARITY_SKIP_REQ_CHECK", "1")

from noise_integration import collect_noise_gate_triggers, should_reject_by_noise_gate
from screening_runner import run_screening


def _full_layers(code: set[str]) -> dict[str, set[str]]:
    return {
        "code": set(code),
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": set(),
    }


def _make_app(
    app_id: str,
    code: set[str],
    envelope: dict | None = None,
) -> dict:
    record: dict = {
        "app_id": app_id,
        "layers": _full_layers(code),
        "apk_path": "/abs/path/{}.apk".format(app_id.lower()),
    }
    if envelope is not None:
        record["noise_profile_envelope"] = dict(envelope)
    return record


def _write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "code_layer:",
                "  mode: v2_tlsh",
                "  window: 5",
                "  norm_divisor: 300",
                "noise_gate:",
                "  enabled: true",
                "  reject_triggers: [adware, fake]",
                "stages:",
                "  screening:",
                "    features: [code]",
                "    metric: jaccard",
                "    threshold: 0.0",
                "  pairwise:",
                "    features: [code]",
                "    metric: jaccard",
                "    threshold: 0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_noise_gate_rejects_fake_apk_from_apkid_runtime_signal() -> None:
    record = _make_app(
        "APP-FAKE",
        {"f1"},
        envelope={
            "schema_version": "nc-v1",
            "apkid_gate_status": "blocked",
            "detector_blocked": True,
            "detector_block_reason": "packer_detected",
            "apkid_signals": {"packers": ["Bangcle"]},
        },
    )

    is_reject, reason = should_reject_by_noise_gate(record, ["adware", "fake"])

    assert is_reject is True
    assert reason == "noise_gate:fake"


def test_noise_gate_rejects_adware_marked_apk_before_screening() -> None:
    adware_envelope = {
        "schema_version": "nc-v1",
        "apkid_gate_status": "clean",
        "libloom_status": "ok",
        "libloom_libraries": [
            {
                "name": "com.google.android.gms.ads",
                "version": ["24.0.0"],
                "similarity": 0.99,
            }
        ],
    }
    clean_envelope = {
        "schema_version": "nc-v1",
        "apkid_gate_status": "clean",
        "libloom_status": "ok",
        "libloom_libraries": [
            {
                "name": "com.squareup.okhttp3",
                "version": ["4.9.0"],
                "similarity": 0.97,
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "cascade.yaml"
        artifact_dir = tmp_path / "artifacts"
        _write_config(config_path)

        app_records = [
            _make_app("APP-ADWARE", {"f1", "f2"}, envelope=adware_envelope),
            _make_app("APP-CLEAN", {"f2", "f3"}, envelope=clean_envelope),
            _make_app("APP-PEER", {"f3", "f4"}, envelope=clean_envelope),
        ]

        candidate_list = run_screening(
            cascade_config_path=config_path,
            app_records=app_records,
            artifact_dir=artifact_dir,
        )

        app_ids_seen = set()
        for row in candidate_list:
            app_ids_seen.add(row.get("query_app_id"))
            app_ids_seen.add(row.get("candidate_app_id"))

        assert "APP-ADWARE" not in app_ids_seen
        assert "APP-CLEAN" in app_ids_seen
        assert "APP-PEER" in app_ids_seen

        rejected_payload = json.loads(
            (artifact_dir / "noise_rejected.json").read_text(encoding="utf-8")
        )
        assert rejected_payload["total_rejected"] == 1
        assert rejected_payload["rejected"][0]["app_id"] == "APP-ADWARE"
        assert rejected_payload["rejected"][0]["reason"] == "noise_gate:adware"


def test_noise_gate_passes_known_clean_apk() -> None:
    clean_envelope = {
        "schema_version": "nc-v1",
        "apkid_gate_status": "clean",
        "libloom_status": "ok",
        "libloom_libraries": [
            {
                "name": "com.squareup.okhttp3",
                "version": ["4.9.0"],
                "similarity": 0.97,
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "cascade.yaml"
        _write_config(config_path)

        app_records = [
            _make_app("APP-CLEAN", {"f1", "f2"}, envelope=clean_envelope),
            _make_app("APP-PEER", {"f2", "f3"}, envelope=clean_envelope),
        ]

        candidate_list = run_screening(
            cascade_config_path=config_path,
            app_records=app_records,
        )

        app_ids_seen = set()
        for row in candidate_list:
            app_ids_seen.add(row.get("query_app_id"))
            app_ids_seen.add(row.get("candidate_app_id"))

        assert "APP-CLEAN" in app_ids_seen
        assert "APP-PEER" in app_ids_seen


def test_collect_noise_gate_triggers_surfaces_libloom_unavailable_status() -> None:
    record = _make_app(
        "APP-LIBLOOM-OFF",
        {"f1"},
        envelope={
            "schema_version": "nc-v1",
            "apkid_gate_status": "clean",
            "libloom_status": "libloom_unavailable",
            "libloom_error_reason": "LIBLOOM_HOME is not set",
        },
    )

    triggers = collect_noise_gate_triggers(record)

    assert "libloom_unavailable" in triggers

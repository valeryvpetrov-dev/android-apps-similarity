"""TDD-контракт для EXEC-LIBLOOM-QUALITY-REAL-RUN."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from script import run_libloom_quality_smoke as rq


def _create_apk(apk_dir: Path, name: str) -> Path:
    apk_path = apk_dir / name
    apk_path.write_bytes(b"fake apk")
    return apk_path


def _write_labels(labels_path: Path, entries: list[dict[str, Any]]) -> None:
    labels_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_real_run_smoke_writes_report_with_required_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    first_apk = _create_apk(apk_dir, "first.apk")
    _create_apk(apk_dir, "second.apk")
    output_path = tmp_path / "report.json"

    monkeypatch.setattr(
        rq,
        "probe_libloom_runtime",
        lambda: {
            "available": True,
            "jar_path": "/opt/libloom/LIBLOOM.jar",
            "libs_profile_dir": "/opt/libloom/libs_profile",
            "warnings": [],
        },
    )

    def _fake_apply(
        apk_path: str,
        apkid_result: dict[str, Any],
        libloom_jar_path: str | None,
        libs_profile_dir: str | None,
        envelope: dict[str, Any],
        timeout_sec: int = 600,
    ) -> dict[str, Any]:
        assert apkid_result["gate_status"] == "clean"
        assert libloom_jar_path == "/opt/libloom/LIBLOOM.jar"
        assert libs_profile_dir == "/opt/libloom/libs_profile"
        merged = dict(envelope)
        merged["libloom_status"] = "ok"
        merged["libloom_elapsed_sec"] = 0.1
        merged["libloom_error_reason"] = None
        merged["libloom_libraries"] = (
            [{"name": "okhttp3", "version": ["4.12.0"], "similarity": 0.99}]
            if Path(apk_path) == first_apk
            else []
        )
        return merged

    monkeypatch.setattr(
        rq.noise_profile_envelope,
        "apply_libloom_detection",
        _fake_apply,
    )

    report = rq.run_real_quality_smoke(
        apk_dir=str(apk_dir),
        labels_path=None,
        output_path=str(output_path),
    )

    assert output_path.is_file()
    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded == report
    assert loaded["run_id"] == "EXEC-LIBLOOM-QUALITY-REAL-RUN"
    assert loaded["corpus_size"] == 2
    assert set(loaded["labeled_tpls"]) >= {
        "okhttp3",
        "gson",
        "retrofit",
        "glide",
        "kotlinx-coroutines",
    }
    assert isinstance(loaded["per_apk_results"], list)
    assert set(loaded["aggregate"]) == {"precision", "recall", "coverage"}
    assert loaded["generated_at"]
    assert loaded["source"]["labels"] == "inline-mini-labels"


def test_real_run_happy_path_counts_okhttp3_as_true_positive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    apk_path = _create_apk(apk_dir, "okhttp-app.apk")
    labels_path = tmp_path / "labels.json"
    _write_labels(
        labels_path,
        [{"apk_path": str(apk_path), "ground_truth": ["okhttp3"]}],
    )

    monkeypatch.setattr(
        rq,
        "probe_libloom_runtime",
        lambda: {
            "available": True,
            "jar_path": "/opt/libloom/LIBLOOM.jar",
            "libs_profile_dir": "/opt/libloom/libs_profile",
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        rq.noise_profile_envelope,
        "apply_libloom_detection",
        lambda **_: {
            "libloom_status": "ok",
            "libloom_elapsed_sec": 0.2,
            "libloom_error_reason": None,
            "libloom_libraries": [
                {
                    "name": "com.squareup.okhttp3",
                    "version": ["4.12.0"],
                    "similarity": 0.98,
                }
            ],
        },
    )

    report = rq.run_real_quality_smoke(
        apk_dir=str(apk_dir),
        labels_path=str(labels_path),
        output_path=None,
    )

    apk_result = report["per_apk_results"][0]
    assert apk_result["tp"] == 1
    assert apk_result["fp"] == 0
    assert apk_result["fn"] == 0
    assert apk_result["precision"] == 1.0
    assert apk_result["recall"] == 1.0
    assert report["aggregate"]["precision"] == 1.0
    assert report["aggregate"]["recall"] == 1.0
    assert report["aggregate"]["coverage"] == 1.0


def test_real_run_counts_false_positive_when_tpl_is_not_labeled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    apk_path = _create_apk(apk_dir, "unknown.apk")
    labels_path = tmp_path / "labels.json"
    _write_labels(
        labels_path,
        [{"apk_path": str(apk_path), "ground_truth": []}],
    )

    monkeypatch.setattr(
        rq,
        "probe_libloom_runtime",
        lambda: {
            "available": True,
            "jar_path": "/opt/libloom/LIBLOOM.jar",
            "libs_profile_dir": "/opt/libloom/libs_profile",
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        rq.noise_profile_envelope,
        "apply_libloom_detection",
        lambda **_: {
            "libloom_status": "ok",
            "libloom_elapsed_sec": 0.3,
            "libloom_error_reason": None,
            "libloom_libraries": [
                {"name": "retrofit2", "version": ["2.11.0"], "similarity": 0.91}
            ],
        },
    )

    report = rq.run_real_quality_smoke(
        apk_dir=str(apk_dir),
        labels_path=str(labels_path),
        output_path=None,
    )

    apk_result = report["per_apk_results"][0]
    assert apk_result["tp"] == 0
    assert apk_result["fp"] == 1
    assert apk_result["fn"] == 0
    assert apk_result["precision"] == 0.0
    assert report["aggregate"]["coverage"] == 1.0


def test_main_writes_libloom_unavailable_report_and_returns_zero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    _create_apk(apk_dir, "sample.apk")
    output_path = tmp_path / "report.json"

    monkeypatch.setattr(
        rq,
        "probe_libloom_runtime",
        lambda: {
            "available": False,
            "jar_path": None,
            "libs_profile_dir": None,
            "warnings": [
                "LIBLOOM_HOME is not set",
                "java is not available on PATH",
            ],
        },
    )

    rc = rq.main(
        [
            "--apk-dir",
            str(apk_dir),
            "--output",
            str(output_path),
        ]
    )

    assert rc == 0
    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded["status"] == "libloom_unavailable"
    assert loaded["warnings"] == [
        "LIBLOOM_HOME is not set",
        "java is not available on PATH",
    ]
    assert loaded["corpus_size"] == 1

#!/usr/bin/env python3
"""Contract tests for the rejected R_api layer quarantine."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from script import m_static_views, pairwise_runner, runtime_profile_contract


SCRIPT_DIR = Path(__file__).resolve().parent


def _empty_features() -> dict[str, object]:
    return {
        "mode": "quick",
        "code": set(),
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": set(),
        "code_v4": None,
        "code_v4_shingled": None,
        "resource_v2": None,
    }


def _write_minimal_apk(path: Path, package_name: str) -> None:
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="{}"><application /></manifest>'
    ).format(package_name)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", manifest)
        archive.writestr(
            "classes.dex",
            b"dex\n035\x00" + package_name.encode("ascii"),
        )


def test_runtime_registry_separates_available_and_default_layers() -> None:
    assert "api" in m_static_views.AVAILABLE_LAYERS
    assert "api" not in m_static_views.DEFAULT_LAYERS
    assert m_static_views.ALL_LAYERS == m_static_views.AVAILABLE_LAYERS

    manifest = runtime_profile_contract.build_runtime_profile_manifest()
    assert manifest["default_layers"] == list(m_static_views.DEFAULT_LAYERS)
    assert manifest["available_layers"] == list(m_static_views.AVAILABLE_LAYERS)


def test_compare_all_default_does_not_publish_api_layer() -> None:
    result = m_static_views.compare_all(_empty_features(), _empty_features())

    assert "api" not in result["layers_used"]
    assert "api" not in result["per_layer"]


def test_layer_weight_fallbacks_do_not_restore_api(tmp_path: Path) -> None:
    missing_weights = m_static_views._load_layer_weights(
        tmp_path / "missing-weights.json"
    )
    assert "api" not in missing_weights

    broken_path = tmp_path / "broken-weights.json"
    broken_path.write_text("{not-json", encoding="utf-8")
    broken_weights = m_static_views._load_layer_weights(broken_path)
    assert "api" not in broken_weights
    assert sum(missing_weights.values()) == pytest.approx(1.0)
    assert sum(broken_weights.values()) == pytest.approx(1.0)


def test_ablation_requires_explicit_api_opt_in() -> None:
    default_results = m_static_views.run_ablation(
        _empty_features(),
        _empty_features(),
    )
    assert default_results
    assert all(
        "api" not in result["layers_used"]
        for result in default_results.values()
    )

    experimental_results = m_static_views.run_ablation(
        _empty_features(),
        _empty_features(),
        include_experimental_api=True,
    )
    assert {
        name
        for name, result in experimental_results.items()
        if "api" in result["layers_used"]
    } == {"all_6_layers", "code_api"}


def test_ablation_cli_forwards_separate_experimental_api_flag(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "m_static_views.py",
            "ablation",
            "--a-apk",
            "a.apk",
            "--b-apk",
            "b.apk",
            "--include-experimental-api",
        ],
    )
    monkeypatch.setattr(
        m_static_views,
        "_resolve_features",
        lambda _args, _side: _empty_features(),
    )

    def fake_run_ablation(**kwargs):
        observed.update(kwargs)
        return {}

    monkeypatch.setattr(m_static_views, "run_ablation", fake_run_ablation)
    monkeypatch.setattr(
        m_static_views,
        "_write_output",
        lambda _payload, _output: None,
    )

    m_static_views.main()

    assert observed["include_experimental_api"] is True


def test_detailed_views_publish_api_only_when_explicitly_selected() -> None:
    default_views = pairwise_runner.build_detailed_views(
        selected_layers=["code", "component", "resource", "library"],
        views_used=["code"],
        analysis_status="success",
        failure_reason=None,
    )
    assert "api" not in default_views

    explicit_views = pairwise_runner.build_detailed_views(
        selected_layers=["code", "api"],
        views_used=["code", "api"],
        analysis_status="success",
        failure_reason=None,
    )
    assert explicit_views["api"]["view_status"] == "success"


def test_api_view_cli_remains_available_for_explicit_experiments(
    tmp_path: Path,
) -> None:
    apk_a = tmp_path / "a.apk"
    apk_b = tmp_path / "b.apk"
    _write_minimal_apk(apk_a, "org.example.a")
    _write_minimal_apk(apk_b, "org.example.b")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "api_view.py"), str(apk_a), str(apk_b)],
        cwd=SCRIPT_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert set(("score", "status", "transitions_a", "transitions_b")) <= set(
        payload
    )

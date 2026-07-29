#!/usr/bin/env python3
"""Contract tests for the rejected R_api layer quarantine."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from script import m_static_views, pairwise_runner, runtime_profile_contract


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SYNTHETIC_API_DEX_BASE64 = (
    "ZGV4CjAzNQBceT7HJUnwSorZU6dDRaNFavYKqrkp649AAwAAcAAAAHhWNBIAAAAA"
    "AAAAAKwCAAAOAAAAcAAAAAUAAACoAAAABAAAALwAAAAAAAAAAAAAAAcAAADsAAAA"
    "AQAAACQBAAD8AQAARAEAAKIBAACqAQAAuwEAAL4BAADMAQAA0AEAAOQBAAD4AQAA"
    "EwIAABYCAAAeAgAAJAIAAC4CAAA0AgAAAwAAAAUAAAAGAAAABwAAAAgAAAACAAAA"
    "AgAAAAAAAAAEAAAAAgAAAJwBAAAEAAAAAwAAAJwBAAAIAAAABAAAAAAAAAAAAAMA"
    "AAAAAAAAAQAKAAAAAQADAAAAAAACAAAADAAAAAMAAwAAAAAAAwACAAkAAAADAAAA"
    "CwAAAAAAAAARAAAAAQAAAAAAAAABAAAAAAAAAJwCAAAAAAAAAgABAAIAAACQAQAA"
    "EQAAAG4QAwABAAwBIgADAHAQBAAAAG4gBQAQAG4QBgAAAAwBEQEAAAEAAQABAAAA"
    "mAEAAAQAAABwEAIAAAAOAAUBAA5LWjwAAgAOAAEAAAACAAY8aW5pdD4AD0FwaUZp"
    "eHR1cmUuamF2YQABTAAMTEFwaUZpeHR1cmU7AAJMTAASTGphdmEvbGFuZy9PYmpl"
    "Y3Q7ABJMamF2YS9sYW5nL1N0cmluZzsAGUxqYXZhL2xhbmcvU3RyaW5nQnVpbGRl"
    "cjsAAVYABmFwcGVuZAAEZmxvdwAIdG9TdHJpbmcABHRyaW0AZn5+RDh7ImJhY2tl"
    "bmQiOiJkZXgiLCJjb21waWxhdGlvbi1tb2RlIjoiZGVidWciLCJoYXMtY2hlY2tz"
    "dW1zIjpmYWxzZSwibWluLWFwaSI6MjEsInZlcnNpb24iOiI5LjAuMzIifQAAAAIA"
    "AIKABPgCAQnEAgAADAAAAAAAAAABAAAAAAAAAAEAAAAOAAAAcAAAAAIAAAAFAAAA"
    "qAAAAAMAAAAEAAAAvAAAAAUAAAAHAAAA7AAAAAYAAAABAAAAJAEAAAEgAAACAAAA"
    "RAEAAAMgAAACAAAAkAEAAAEQAAABAAAAnAEAAAIgAAAOAAAAogEAAAAgAAABAAAA"
    "nAIAAAAQAAABAAAArAIAAA=="
)
SYNTHETIC_ANDROID_MANIFEST_BASE64 = (
    "AwAIABQDAAABABwA8AEAAAsAAAAAAAAAAAAAAEgAAAAAAAAAAAAAACYAAABcAAAA"
    "ZAAAAHYAAACQAAAA6AAAAPwAAAAsAQAAPgEAAHIBAAARAGMAbwBtAHAAaQBsAGUA"
    "UwBkAGsAVgBlAHIAcwBpAG8AbgAAABkAYwBvAG0AcABpAGwAZQBTAGQAawBWAGUA"
    "cgBzAGkAbwBuAEMAbwBkAGUAbgBhAG0AZQAAAAIAMQA1AAAABwBhAG4AZAByAG8A"
    "aQBkAAAACwBhAHAAcABsAGkAYwBhAHQAaQBvAG4AAAAqAGgAdAB0AHAAOgAvAC8A"
    "cwBjAGgAZQBtAGEAcwAuAGEAbgBkAHIAbwBpAGQALgBjAG8AbQAvAGEAcABrAC8A"
    "cgBlAHMALwBhAG4AZAByAG8AaQBkAAAACABtAGEAbgBpAGYAZQBzAHQAAAAWAG8A"
    "cgBnAC4AZQB4AGEAbQBwAGwAZQAuAGEAcABpAGYAaQB4AHQAdQByAGUAAAAHAHAA"
    "YQBjAGsAYQBnAGUAAAAYAHAAbABhAHQAZgBvAHIAbQBCAHUAaQBsAGQAVgBlAHIA"
    "cwBpAG8AbgBDAG8AZABlAAAAGABwAGwAYQB0AGYAbwByAG0AQgB1AGkAbABkAFYA"
    "ZQByAHMAaQBvAG4ATgBhAG0AZQAAAAAAgAEIABAAAAByBQEBcwUBAQABEAAYAAAA"
    "AgAAAP////8DAAAABQAAAAIBEACIAAAAAgAAAP//////////BgAAABQAFAAFAAAA"
    "AAAAAAUAAAAAAAAA/////wgAABAjAAAABQAAAAEAAAACAAAACAAAAwIAAAD/////"
    "CAAAAAcAAAAIAAADBwAAAP////8JAAAA/////wgAABAjAAAA/////woAAAD/////"
    "CAAAEA8AAAACARAAJAAAAAQAAAD//////////wQAAAAUABQAAAAAAAAAAAADARAA"
    "GAAAAAQAAAD//////////wQAAAADARAAGAAAAAIAAAD//////////wYAAAABARAA"
    "GAAAAAIAAAD/////AwAAAAUAAAA="
)


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


def _write_synthetic_api_apk(path: Path) -> None:
    """Write a D8-built DEX whose flow() makes sequential java.lang calls."""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "AndroidManifest.xml",
            base64.b64decode(SYNTHETIC_ANDROID_MANIFEST_BASE64),
        )
        archive.writestr(
            "classes.dex",
            base64.b64decode(SYNTHETIC_API_DEX_BASE64),
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


def test_ablation_cli_extracts_non_empty_api_chains(
    monkeypatch,
    tmp_path: Path,
) -> None:
    apk_path = tmp_path / "api-fixture.apk"
    _write_synthetic_api_apk(apk_path)
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "m_static_views.py",
            "ablation",
            "--a-apk",
            str(apk_path),
            "--b-apk",
            str(apk_path),
            "--include-experimental-api",
        ],
    )
    monkeypatch.setattr(
        m_static_views,
        "_resolve_features",
        lambda _args, _side: _empty_features(),
    )
    monkeypatch.setattr(
        m_static_views,
        "_write_output",
        lambda payload, _output: observed.update(payload=payload),
    )

    m_static_views.main()

    result = observed["payload"]
    api_result = result["code_api"]["per_layer"]["api"]
    assert api_result["status"] == "markov_cosine"
    assert api_result["score"] > 0.0


@pytest.mark.parametrize(
    "missing_side",
    ["a", "b"],
)
def test_ablation_cli_requires_both_apks_for_experimental_api(
    monkeypatch,
    tmp_path: Path,
    missing_side: str,
) -> None:
    apk_path = tmp_path / "api-fixture.apk"
    _write_synthetic_api_apk(apk_path)
    supplied_side = "b" if missing_side == "a" else "a"
    argv = [
        "m_static_views.py",
        "ablation",
        "--{}-apk".format(supplied_side),
        str(apk_path),
        "--include-experimental-api",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="requires both --a-apk and --b-apk"):
        m_static_views.main()


def test_ablation_cli_fails_when_api_extractor_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    apk_path = tmp_path / "api-fixture.apk"
    _write_synthetic_api_apk(apk_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "m_static_views.py",
            "ablation",
            "--a-apk",
            str(apk_path),
            "--b-apk",
            str(apk_path),
            "--include-experimental-api",
        ],
    )
    monkeypatch.setattr(m_static_views, "build_markov_chain", None)

    with pytest.raises(SystemExit, match="build_markov_chain is unavailable"):
        m_static_views.main()


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
    apk_path = tmp_path / "api-fixture.apk"
    _write_synthetic_api_apk(apk_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "api_view.py"),
            str(apk_path),
            str(apk_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert set(("score", "status", "transitions_a", "transitions_b")) <= set(
        payload
    )
    assert payload["transitions_a"] > 0
    assert payload["transitions_b"] > 0
    assert payload["status"] == "markov_cosine"

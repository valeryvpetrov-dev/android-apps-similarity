#!/usr/bin/env python3
"""REPR-22-IDF-COMPONENT-RESOURCE: IDF channels for component/resource views."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import library_view_v2 as library_view_v2  # noqa: E402
from component_view import compare_components  # noqa: E402
from resource_view_v2 import compare_resource_view_v2  # noqa: E402


def _reset_idf_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    for attr in (
        "_IDF_SNAPSHOT_CACHE",
        "_IDF_SNAPSHOT_PATH_CACHE",
        "_IDF_WEIGHTS_CACHE",
    ):
        monkeypatch.setattr(library_view_v2, attr, None, raising=False)


def _write_snapshot(
    tmp_path: Path,
    *,
    n_documents: int = 10,
    library_df: dict[str, int] | None = None,
    component_df: dict[str, int] | None = None,
    resource_df: dict[str, int] | None = None,
) -> Path:
    payload = {
        "snapshot_version": "v1",
        "n_documents": n_documents,
        "library": {
            "document_frequency": library_df or {},
        },
        "component": {
            "document_frequency": component_df or {},
        },
        "resource": {
            "document_frequency": resource_df or {},
        },
    }
    snapshot_path = tmp_path / "idf-snapshot-v1.json"
    snapshot_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return snapshot_path


def _component_features(activity_names: set[str]) -> dict:
    return {
        "package": "com.example.synthetic",
        "activities": [{"name": name} for name in sorted(activity_names)],
        "services": [],
        "receivers": [],
        "providers": [],
        "permissions": set(),
        "features": set(),
        "icc_raw": [],
    }


def _resource_features(strings: set[str]) -> dict:
    return {
        "res_strings": set(strings),
        "res_drawables": set(),
        "res_layouts": set(),
        "assets_bin": set(),
        "icon_phash": None,
        "mode": "v2",
    }


def test_component_idf_jaccard_downweights_corpus_wide_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        component_df={
            "activity:MainActivity": 10,
            "activity:MyAppCustomActivity": 1,
        },
    )
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(snapshot_path))
    _reset_idf_cache(monkeypatch)

    feat_a = _component_features({"MainActivity", "MyAppCustomActivity"})
    feat_b = _component_features({"MainActivity"})

    result = compare_components(feat_a, feat_b)

    assert result["jaccard"] == pytest.approx(0.5)
    assert result["jaccard_idf"] == pytest.approx(0.0, abs=1e-9)
    assert result["tversky_a_idf"] == pytest.approx(0.0, abs=1e-9)
    assert result["tversky_b_idf"] == pytest.approx(0.0, abs=1e-9)
    assert math.log(10.0 / 1.0) == pytest.approx(2.30258509299)
    assert abs(result["jaccard"] - result["jaccard_idf"]) > 0.45


def test_resource_idf_jaccard_downweights_corpus_wide_app_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        resource_df={
            "string:app_name": 10,
            "string:app_specific_token": 1,
        },
    )
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(snapshot_path))
    _reset_idf_cache(monkeypatch)

    feat_a = _resource_features({"string:app_name", "string:app_specific_token"})
    feat_b = _resource_features({"string:app_name"})

    result = compare_resource_view_v2(feat_a, feat_b)

    assert result["jaccard"] == pytest.approx(0.5)
    assert result["jaccard_idf"] == pytest.approx(0.0, abs=1e-9)
    assert result["tversky_a_idf"] == pytest.approx(0.0, abs=1e-9)
    assert result["tversky_b_idf"] == pytest.approx(0.0, abs=1e-9)
    assert abs(result["jaccard"] - result["jaccard_idf"]) > 0.45


def test_component_and_resource_skip_idf_channels_when_layer_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = tmp_path / "idf-snapshot-v1.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "snapshot_version": "v1",
                "n_documents": 10,
                "library": {
                    "document_frequency": {
                        "common-lib": 10,
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(snapshot_path))
    _reset_idf_cache(monkeypatch)

    component_result = compare_components(
        _component_features({"MainActivity", "MyAppCustomActivity"}),
        _component_features({"MainActivity"}),
    )
    resource_result = compare_resource_view_v2(
        _resource_features({"string:app_name", "string:app_specific_token"}),
        _resource_features({"string:app_name"}),
    )

    for result in (component_result, resource_result):
        assert "jaccard" in result
        assert "jaccard_idf" not in result
        assert "tversky_a_idf" not in result
        assert "tversky_b_idf" not in result


def test_default_idf_snapshot_has_top_level_library_component_resource_layers() -> None:
    snapshot_path = (
        Path(__file__).resolve().parent.parent
        / "experiments"
        / "datasets"
        / "idf-snapshot-v1.json"
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert payload["snapshot_version"] == "v1"
    assert isinstance(payload["n_documents"], int)
    assert payload["n_documents"] > 0
    for layer_name in ("library", "component", "resource"):
        assert layer_name in payload
        assert "document_frequency" in payload[layer_name]
        assert isinstance(payload[layer_name]["document_frequency"], dict)
        assert payload[layer_name]["n_tokens"] == len(
            payload[layer_name]["document_frequency"]
        )

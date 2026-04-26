#!/usr/bin/env python3
"""REPR-25: screening per-view scores expose multi-channel evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import library_view_v2 as library_view_v2  # noqa: E402
from screening_runner import build_candidate_list, compute_per_view_scores  # noqa: E402


PERVIEW_LAYERS = ("code", "component", "library", "resource")
BASE_CHANNELS = ("jaccard", "tversky_a", "tversky_b", "overlap_min")


def _reset_idf_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    for attr in (
        "_IDF_SNAPSHOT_CACHE",
        "_IDF_SNAPSHOT_PATH_CACHE",
        "_IDF_WEIGHTS_CACHE",
    ):
        monkeypatch.setattr(library_view_v2, attr, None, raising=False)


def _disable_idf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(tmp_path / "missing-idf.json"))
    _reset_idf_cache(monkeypatch)


def _write_idf_snapshot(tmp_path: Path) -> Path:
    snapshot_path = tmp_path / "idf-snapshot.json"
    payload = {
        "snapshot_version": "v1",
        "n_documents": 10,
        "code": {
            "document_frequency": {
                "shared": 5,
                "code_a": 1,
                "code_b": 1,
            },
        },
        "component": {
            "document_frequency": {
                "activity:Main": 5,
                "activity:PrivateA": 1,
                "activity:PrivateB": 1,
            },
        },
        "library": {
            "document_frequency": {
                "okhttp": 5,
                "rare-a": 1,
                "rare-b": 1,
            },
        },
        "resource": {
            "document_frequency": {
                "string:app_name": 5,
                "string:private_a": 1,
                "string:private_b": 1,
            },
        },
    }
    snapshot_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return snapshot_path


def _make_app(app_id: str, layers: dict[str, set[str]]) -> dict:
    return {
        "app_id": app_id,
        "layers": {
            "code": set(layers.get("code", set())),
            "component": set(layers.get("component", set())),
            "library": set(layers.get("library", set())),
            "resource": set(layers.get("resource", set())),
            "metadata": set(layers.get("metadata", set())),
        },
    }


def test_compute_per_view_scores_returns_base_channels_for_screening_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_idf(tmp_path, monkeypatch)
    app_a = _make_app(
        "APP-A",
        {
            "code": {"shared", "code_a"},
            "component": {"activity:Main", "activity:PrivateA"},
            "library": {"okhttp", "rare-a"},
            "resource": {"string:app_name", "string:private_a"},
        },
    )
    app_b = _make_app(
        "APP-B",
        {
            "code": {"shared", "code_b"},
            "component": {"activity:Main", "activity:PrivateB"},
            "library": {"okhttp", "rare-b"},
            "resource": {"string:app_name", "string:private_b"},
        },
    )

    scores = compute_per_view_scores(
        app_a=app_a,
        app_b=app_b,
        layers=list(PERVIEW_LAYERS),
        metric="jaccard",
    )

    assert set(scores) == set(PERVIEW_LAYERS)
    for layer in PERVIEW_LAYERS:
        assert isinstance(scores[layer], dict)
        for channel in BASE_CHANNELS:
            assert channel in scores[layer]
            assert isinstance(scores[layer][channel], float)
        assert "jaccard_idf" not in scores[layer]


def test_subset_pair_preserves_asymmetric_inclusion_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_idf(tmp_path, monkeypatch)
    app_a = _make_app(
        "APP-A",
        {
            "code": {"shared"},
            "component": {"activity:Main"},
            "library": {"okhttp"},
            "resource": {"string:app_name"},
        },
    )
    app_b = _make_app(
        "APP-B",
        {
            "code": {"shared", "code_b"},
            "component": {"activity:Main", "activity:PrivateB"},
            "library": {"okhttp", "rare-b"},
            "resource": {"string:app_name", "string:private_b"},
        },
    )

    scores = compute_per_view_scores(
        app_a=app_a,
        app_b=app_b,
        layers=list(PERVIEW_LAYERS),
        metric="jaccard",
    )

    for layer in PERVIEW_LAYERS:
        assert scores[layer]["jaccard"] == pytest.approx(0.5)
        assert scores[layer]["tversky_a"] > scores[layer]["tversky_b"]
        assert scores[layer]["tversky_a"] == pytest.approx(1.0)
        assert scores[layer]["overlap_min"] == pytest.approx(1.0)


def test_jaccard_idf_is_absent_without_idf_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_idf(tmp_path, monkeypatch)
    app_a = _make_app("APP-A", {"library": {"okhttp", "rare-a"}})
    app_b = _make_app("APP-B", {"library": {"okhttp", "rare-b"}})

    scores = compute_per_view_scores(
        app_a=app_a,
        app_b=app_b,
        layers=["library"],
        metric="jaccard",
    )

    assert "jaccard" in scores["library"]
    assert "jaccard_idf" not in scores["library"]


def test_jaccard_idf_is_added_for_layers_present_in_idf_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = _write_idf_snapshot(tmp_path)
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(snapshot_path))
    _reset_idf_cache(monkeypatch)
    app_a = _make_app(
        "APP-A",
        {
            "code": {"shared", "code_a"},
            "component": {"activity:Main", "activity:PrivateA"},
            "library": {"okhttp", "rare-a"},
            "resource": {"string:app_name", "string:private_a"},
        },
    )
    app_b = _make_app(
        "APP-B",
        {
            "code": {"shared", "code_b"},
            "component": {"activity:Main", "activity:PrivateB"},
            "library": {"okhttp", "rare-b"},
            "resource": {"string:app_name", "string:private_b"},
        },
    )

    scores = compute_per_view_scores(
        app_a=app_a,
        app_b=app_b,
        layers=list(PERVIEW_LAYERS),
        metric="jaccard",
    )

    for layer in PERVIEW_LAYERS:
        assert "jaccard_idf" in scores[layer]
        assert isinstance(scores[layer]["jaccard_idf"], float)


def test_lsh_screening_row_carries_per_view_channels_downstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_idf(tmp_path, monkeypatch)
    app_a = _make_app(
        "APP-A",
        {
            "code": {"shared"},
            "component": {"activity:Main"},
            "library": {"okhttp"},
            "resource": {"string:app_name"},
        },
    )
    app_b = _make_app(
        "APP-B",
        {
            "code": {"shared", "code_b"},
            "component": {"activity:Main", "activity:PrivateB"},
            "library": {"okhttp", "rare-b"},
            "resource": {"string:app_name", "string:private_b"},
        },
    )
    app_a["screening_signature"] = ["same-bucket-token"]
    app_b["screening_signature"] = ["same-bucket-token"]

    rows = build_candidate_list(
        app_records=[app_a, app_b],
        selected_layers=list(PERVIEW_LAYERS),
        metric="jaccard",
        threshold=0.0,
        ins_block_sim_threshold=0.80,
        ged_timeout_sec=30,
        processes_count=1,
        threads_count=2,
        candidate_index_params={
            "type": "minhash_lsh",
            "num_perm": 64,
            "bands": 16,
            "seed": 42,
            "features": list(PERVIEW_LAYERS),
        },
    )

    assert len(rows) == 1
    per_view = rows[0]["per_view_scores"]
    assert set(per_view) == set(PERVIEW_LAYERS)
    for layer in PERVIEW_LAYERS:
        for channel in BASE_CHANNELS:
            assert channel in per_view[layer]
    assert per_view["code"]["tversky_a"] > per_view["code"]["tversky_b"]

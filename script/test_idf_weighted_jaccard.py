#!/usr/bin/env python3
"""REPR-20-IDF-WEIGHTED-JACCARD: тесты для IDF-взвешенного Jaccard/Tversky."""

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
from m_static_views import compare_m_static_layer  # noqa: E402


def _reset_idf_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбрасывает lazy cache snapshot-а между тестами."""
    for attr in (
        "_IDF_SNAPSHOT_CACHE",
        "_IDF_SNAPSHOT_PATH_CACHE",
        "_IDF_WEIGHTS_CACHE",
    ):
        monkeypatch.setattr(library_view_v2, attr, None, raising=False)


def _write_snapshot(
    tmp_path: Path,
    *,
    n_documents: int,
    library_df: dict[str, int],
) -> Path:
    snapshot_path = tmp_path / "idf-snapshot-v1.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "snapshot_version": "v1",
                "n_documents": n_documents,
                "layer": {
                    "library": {
                        "document_frequency": library_df,
                    },
                    "component": {
                        "document_frequency": {},
                    },
                    "resource": {
                        "document_frequency": {},
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return snapshot_path


def test_weighted_jaccard_downweights_corpus_wide_common_tokens() -> None:
    """`common` с IDF=0 не должен искусственно поднимать similarity."""
    compute = getattr(library_view_v2, "compute_idf_weighted_jaccard", None)
    assert callable(compute), "ожидается функция compute_idf_weighted_jaccard()"

    feat_a = {"libraries": {"common": {}, "rare": {}}}
    feat_b = {"libraries": {"common": {}}}
    flat = library_view_v2.compare_libraries_v2(feat_a, feat_b)["jaccard"]

    weighted = compute(
        {"common", "rare"},
        {"common"},
        {"common": 0.0, "rare": math.log(10.0 / 1.0)},
    )

    assert flat == pytest.approx(0.5)
    assert weighted == pytest.approx(0.0, abs=1e-9)
    assert abs(flat - weighted) > 0.45


def test_weighted_jaccard_equals_plain_when_all_idf_weights_are_one() -> None:
    compute = getattr(library_view_v2, "compute_idf_weighted_jaccard", None)
    assert callable(compute), "ожидается функция compute_idf_weighted_jaccard()"

    left = {"lib_a", "lib_b"}
    right = {"lib_b", "lib_c"}
    flat = 1.0 / 3.0

    weighted = compute(
        left,
        right,
        {"lib_a": 1.0, "lib_b": 1.0, "lib_c": 1.0},
    )

    assert weighted == pytest.approx(flat)


def test_default_idf_snapshot_path_points_to_v2_artifact() -> None:
    assert library_view_v2.DEFAULT_IDF_SNAPSHOT_PATH.name == "idf-snapshot-v2.json"


def test_library_main_flow_schema_adds_idf_channels_when_snapshot_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = _write_snapshot(
        tmp_path,
        n_documents=10,
        library_df={
            "common": 10,
            "rare": 1,
            "extra": 1,
        },
    )
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(snapshot_path))
    _reset_idf_cache(monkeypatch)

    feat_a = {"libraries": {"common": {}, "rare": {}}}
    feat_b = {"libraries": {"common": {}, "rare": {}, "extra": {}}}

    result = compare_m_static_layer("library", feat_a, feat_b)

    for key in (
        "jaccard",
        "jaccard_idf",
        "tversky_a",
        "tversky_b",
        "tversky_a_idf",
        "tversky_b_idf",
        "overlap_min",
    ):
        assert key in result, "schema: {!r} отсутствует в результате".format(key)
        assert isinstance(result[key], float), "{!r} должен быть float".format(key)

    assert result["score"] == pytest.approx(result["jaccard"])
    assert result["jaccard"] == pytest.approx(2.0 / 3.0)
    assert result["jaccard_idf"] == pytest.approx(0.5)
    assert result["tversky_a"] == pytest.approx(1.0)
    assert result["tversky_a_idf"] == pytest.approx(1.0)
    assert result["tversky_b"] == pytest.approx(2.0 / 3.0)
    assert result["tversky_b_idf"] == pytest.approx(0.5)


def test_library_main_flow_falls_back_to_plain_jaccard_without_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_snapshot = tmp_path / "missing-idf-snapshot-v1.json"
    monkeypatch.setenv("SIMILARITY_IDF_SNAPSHOT_PATH", str(missing_snapshot))
    _reset_idf_cache(monkeypatch)

    feat_a = {"libraries": {"lib_a": {}, "lib_b": {}}}
    feat_b = {"libraries": {"lib_a": {}, "lib_b": {}, "lib_c": {}}}

    result = compare_m_static_layer("library", feat_a, feat_b)

    assert "jaccard" in result
    assert result["jaccard"] == pytest.approx(2.0 / 3.0)
    assert result["score"] == pytest.approx(result["jaccard"])
    assert "jaccard_idf" not in result
    assert "tversky_a_idf" not in result
    assert "tversky_b_idf" not in result

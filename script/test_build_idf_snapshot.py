#!/usr/bin/env python3
"""REPR-24-IDF-FDROID-V2: tests for IDF snapshot v2 builder."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import build_idf_snapshot_v2 as idf_builder  # noqa: E402


def _touch_apk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"placeholder apk")
    return path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_idf_snapshot_scans_apks_and_counts_document_frequency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "corpus"
    _touch_apk(corpus_dir / "beta.apk")
    _touch_apk(corpus_dir / "nested" / "alpha.apk")
    _touch_apk(corpus_dir / "nested" / "gamma.apk")
    calls: list[str] = []

    layers_by_name = {
        "alpha.apk": {
            "library": {"lib:rare", "lib:common"},
            "component": {"activity:Main"},
            "resource": {"string:app_name", "drawable:icon"},
        },
        "beta.apk": {
            "library": {"lib:common"},
            "component": {"activity:Main", "service:Sync"},
            "resource": {"string:app_name"},
        },
        "gamma.apk": {
            "library": {"lib:other"},
            "component": {"service:Sync"},
            "resource": {"layout:settings"},
        },
    }

    def fake_extract(apk_path: Path) -> dict[str, set[str]]:
        calls.append(apk_path.name)
        return layers_by_name[apk_path.name]

    monkeypatch.setattr(idf_builder, "extract_layers_from_apk", fake_extract)

    out_path = tmp_path / "idf.json"
    payload = idf_builder.build_idf_snapshot(
        corpus_dir,
        ["library", "component", "resource"],
        out_path,
    )

    assert calls == ["beta.apk", "alpha.apk", "gamma.apk"]
    assert payload == _load(out_path)
    assert payload["n_documents"] == 3
    assert payload["library"]["document_frequency"] == {
        "lib:common": 2,
        "lib:other": 1,
        "lib:rare": 1,
    }
    assert payload["component"]["document_frequency"] == {
        "activity:Main": 2,
        "service:Sync": 2,
    }
    assert payload["resource"]["document_frequency"] == {
        "drawable:icon": 1,
        "layout:settings": 1,
        "string:app_name": 2,
    }


def test_snapshot_v2_schema_has_n_tokens_document_frequency_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "fdroid-v2-mini"
    _touch_apk(corpus_dir / "one.apk")

    monkeypatch.setattr(
        idf_builder,
        "extract_layers_from_apk",
        lambda _apk: {
            "library": {"lib:okhttp"},
            "component": {"activity:Main"},
            "resource": {"string:app_name"},
        },
    )

    payload = idf_builder.build_idf_snapshot(
        corpus_dir,
        ["library", "component", "resource"],
        tmp_path / "snapshot.json",
    )

    assert payload["snapshot_version"] == "v2"
    assert payload["n_documents"] == 1
    assert payload["source"] == "fdroid-v2-mini"
    assert payload["built_at"].endswith("Z")
    for layer_name in ("library", "component", "resource"):
        assert payload[layer_name]["n_tokens"] > 0
        assert payload[layer_name]["n_tokens"] == len(
            payload[layer_name]["document_frequency"]
        )


def test_build_idf_snapshot_is_deterministic_for_same_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "corpus"
    _touch_apk(corpus_dir / "a.apk")
    _touch_apk(corpus_dir / "b.apk")

    def fake_extract(apk_path: Path) -> dict[str, set[str]]:
        return {
            "library": {"lib:common", f"lib:{apk_path.stem}"},
            "component": {"activity:Main"},
            "resource": {"string:app_name"},
        }

    monkeypatch.setattr(idf_builder, "extract_layers_from_apk", fake_extract)

    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    idf_builder.build_idf_snapshot(corpus_dir, ["library", "component", "resource"], out_a)
    idf_builder.build_idf_snapshot(corpus_dir, ["library", "component", "resource"], out_b)

    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")


def test_empty_library_layer_writes_warning_and_omits_library_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "corpus"
    _touch_apk(corpus_dir / "a.apk")
    _touch_apk(corpus_dir / "b.apk")

    monkeypatch.setattr(
        idf_builder,
        "extract_layers_from_apk",
        lambda _apk: {
            "library": set(),
            "component": {"activity:Main"},
            "resource": {"string:app_name"},
        },
    )

    payload = idf_builder.build_idf_snapshot(
        corpus_dir,
        ["library", "component", "resource"],
        tmp_path / "snapshot.json",
    )

    assert "library" not in payload
    assert payload["component"]["n_tokens"] == 1
    assert payload["resource"]["n_tokens"] == 1
    assert any(
        "library" in warning and "n_tokens=0" in warning
        for warning in payload["warnings"]
    )


def test_library_layer_prefers_detected_tpl_ids_from_decoded_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "fdroid-corpus-v2-apks"
    _touch_apk(corpus_dir / "sample.apk")
    _touch_apk(corpus_dir / "missing_decoded.apk")
    decoded_root = tmp_path / "fdroid-corpus-v2-decoded"
    for rel_path in (
        "smali/okhttp3/OkHttpClient.smali",
        "smali/okhttp3/internal/Internal.smali",
        "smali/okhttp3/logging/HttpLoggingInterceptor.smali",
        "smali/okio/Buffer.smali",
    ):
        smali_path = decoded_root / "sample" / rel_path
        smali_path.parent.mkdir(parents=True, exist_ok=True)
        smali_path.write_text(".class public Lplaceholder;\n", encoding="utf-8")

    monkeypatch.setattr(
        idf_builder,
        "extract_layers_from_apk",
        lambda _apk: {
            "library": {"meta_inf_ext:MF"},
            "component": set(),
            "resource": set(),
        },
    )

    payload = idf_builder.build_idf_snapshot(
        corpus_dir,
        ["library"],
        tmp_path / "snapshot.json",
    )

    assert payload["library"]["document_frequency"] == {
        "okhttp3": 1,
        "okio": 1,
    }


def test_cli_accepts_layers_and_writes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "corpus"
    _touch_apk(corpus_dir / "a.apk")
    out_path = tmp_path / "cli-snapshot.json"

    monkeypatch.setattr(
        idf_builder,
        "extract_layers_from_apk",
        lambda _apk: {
            "library": {"lib:ignored"},
            "component": {"activity:Main"},
            "resource": {"string:app_name"},
        },
    )

    rc = idf_builder.main(
        [
            "--corpus_dir",
            str(corpus_dir),
            "--out",
            str(out_path),
            "--layers",
            "component,resource",
        ]
    )

    payload = _load(out_path)
    assert rc == 0
    assert "library" not in payload
    assert set(payload).issuperset({"component", "resource"})

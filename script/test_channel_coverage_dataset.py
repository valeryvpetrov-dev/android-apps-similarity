from __future__ import annotations

import json
from pathlib import Path

import pytest


def _load_module():
    try:
        import build_channel_coverage_dataset as module
    except ModuleNotFoundError:
        pytest.fail("script/build_channel_coverage_dataset.py must provide the channel coverage dataset CLI")
    return module


def _evidence(signal_type: str, ref: str, magnitude: float = 1.0) -> dict[str, object]:
    return {
        "source_stage": "pairwise",
        "signal_type": signal_type,
        "ref": ref,
        "magnitude": magnitude,
    }


def test_synthetic_mix_with_all_evidence_channels_has_full_coverage():
    module = _load_module()
    evidence = [
        _evidence("layer_score", "code", 0.9),
        _evidence("layer_score", "component", 0.8),
        _evidence("library_match", "okhttp3", 0.7),
        _evidence("resource_overlap", "drawable/icon", 0.6),
        _evidence("signature_match", "apk_signature", 1.0),
    ]
    pairs = [
        {
            "pair_id": f"synthetic-{idx}",
            "ground_truth": "clone",
            "evidence_channels": list(module.EVIDENCE_CHANNELS),
            "full_metadata": {"evidence": list(evidence)},
        }
        for idx in range(3)
    ]

    coverage = module.channel_coverage_summary(pairs)

    assert coverage["pairs_with_all_channels"] == 3
    assert coverage["all_channels_ratio"] == 1.0
    for channel in module.EVIDENCE_CHANNELS:
        assert coverage["per_channel"][channel]["pairs_with_data"] == 3
        assert coverage["per_channel"][channel]["ratio"] == 1.0


def test_ground_truth_pair_pools_keep_clone_and_different_separate(tmp_path: Path):
    module = _load_module()
    records = [
        module.ApkRecord(
            path=tmp_path / "a.apk",
            app_id="pkg.alpha",
            sha256="same-sha",
            package_name="pkg.alpha",
            signature_hash="same-signature",
            library_set=frozenset({"okhttp3", "retrofit2"}),
            category="tools",
        ),
        module.ApkRecord(
            path=tmp_path / "a-copy.apk",
            app_id="pkg.alpha",
            sha256="same-sha",
            package_name="pkg.alpha",
            signature_hash="same-signature",
            library_set=frozenset({"okhttp3", "retrofit2"}),
            category="tools",
        ),
        module.ApkRecord(
            path=tmp_path / "a-repack.apk",
            app_id="pkg.alpha",
            sha256="different-sha",
            package_name="pkg.alpha",
            signature_hash="other-signature",
            library_set=frozenset({"okhttp3", "retrofit2"}),
            category="tools",
        ),
        module.ApkRecord(
            path=tmp_path / "b.apk",
            app_id="pkg.beta",
            sha256="beta-sha",
            package_name="pkg.beta",
            signature_hash="beta-signature",
            library_set=frozenset({"material", "appcompat"}),
            category="games",
        ),
    ]

    pools = module.build_pair_pools(records)

    assert ("pkg.alpha", "pkg.alpha") in {
        (left.app_id, right.app_id) for left, right in pools["clone"]
    }
    assert all(
        {left.path.name, right.path.name} != {"a.apk", "a-copy.apk"}
        for left, right in pools["different"]
    )
    assert pools["different"], "different pool should contain disjoint cross-package pairs"


def test_cli_falls_back_to_synthetic_dataset_when_corpus_is_missing(tmp_path: Path, capsys):
    module = _load_module()
    out_path = tmp_path / "channel_dataset.json"

    exit_code = module.main(
        [
            "--corpus_dir",
            str(tmp_path / "missing-corpus"),
            "--out",
            str(out_path),
            "--n_pairs",
            "4",
            "--mix",
            "clone:1,repackage:1,similar:1,different:1",
            "--seed",
            "42",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "fallback" in captured.err.lower()
    assert payload["source"] == "synthetic_fallback"
    assert payload["n_pairs"] == 4
    assert payload["channel_coverage"]["all_channels_ratio"] == 1.0


def test_quick_compare_pair_keeps_dex_code_channel(tmp_path: Path):
    module = _load_module()
    layers = {
        "code": {"dex:classes.dex"},
        "component": {"manifest_component:MainActivity"},
        "resource": {"res_type:layout"},
        "metadata": {"manifest_present:1"},
        "library": {"meta_inf_ext:RSA"},
    }
    left = module.ApkRecord(
        path=tmp_path / "left.apk",
        app_id="left",
        sha256="left-sha",
        package_name="pkg.left",
        signature_hash="same-signature",
        library_set=frozenset({"meta_inf_ext:RSA"}),
        category="pkg",
        features=module._quick_features_from_layers(layers, "same-signature"),
    )
    right = module.ApkRecord(
        path=tmp_path / "right.apk",
        app_id="right",
        sha256="right-sha",
        package_name="pkg.right",
        signature_hash="same-signature",
        library_set=frozenset({"meta_inf_ext:RSA"}),
        category="pkg",
        features=module._quick_features_from_layers(layers, "same-signature"),
    )

    row = module._compare_pair(left, right, "similar")

    assert "code" in row["evidence_channels"]


def test_near_duplicate_same_package_pair_enters_clone_pool(tmp_path: Path):
    module = _load_module()
    layers = {
        "code": {"dex:classes.dex"},
        "component": {"manifest_component:MainActivity"},
        "resource": {"res_type:layout", "res_ext:xml"},
        "metadata": {"manifest_present:1"},
        "library": {"meta_inf_ext:RSA"},
    }
    records = [
        module.ApkRecord(
            path=tmp_path / "pkg.alpha_1.apk",
            app_id="pkg.alpha_1",
            sha256="sha-one",
            package_name="pkg.alpha",
            signature_hash="signature-one",
            library_set=frozenset({"meta_inf_ext:RSA"}),
            category="pkg",
            features=module._quick_features_from_layers(layers, "signature-one"),
        ),
        module.ApkRecord(
            path=tmp_path / "pkg.alpha_2.apk",
            app_id="pkg.alpha_2",
            sha256="sha-two",
            package_name="pkg.alpha",
            signature_hash="signature-two",
            library_set=frozenset({"meta_inf_ext:RSA"}),
            category="pkg",
            features=module._quick_features_from_layers(layers, "signature-two"),
        ),
    ]

    pools = module.build_pair_pools(records)

    assert pools["clone"]

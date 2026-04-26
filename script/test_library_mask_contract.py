#!/usr/bin/env python3
"""NOISE-24-MASK-CONTRACT: unified TPL/library mask contract."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _jaccard_record() -> dict:
    return {
        "app_id": "synthetic-okhttp",
        "packages": frozenset(
            {
                "okhttp3",
                "okhttp3.internal",
                "okhttp3.internal.cache",
                "okhttp3.logging",
                "com.example.app",
            }
        ),
        "paths": {
            "smali/okhttp3/OkHttpClient.smali",
            "smali/okhttp3/internal/cache/DiskLruCache.smali",
            "smali/com/example/app/MainActivity.smali",
        },
        "cascade_config": {
            "library_mask": {
                "algorithm": "jaccard_v2",
                "threshold": 0.30,
                "min_matches": 1,
            }
        },
    }


def test_get_library_mask_uses_explicit_config_not_use_library_v2_env(monkeypatch):
    from library_mask import get_library_mask

    record = _jaccard_record()

    monkeypatch.setenv("USE_LIBRARY_V2", "0")
    env_disabled = get_library_mask(record)
    monkeypatch.setenv("USE_LIBRARY_V2", "1")
    env_enabled = get_library_mask(record)

    assert env_disabled == env_enabled
    assert env_enabled == {
        "okhttp3",
        "okhttp3.internal",
        "okhttp3.internal.cache",
        "okhttp3.logging",
    }


def test_noise_normalizer_and_library_view_v2_classify_from_same_mask():
    from library_mask import get_library_mask
    from library_view_v2 import detect_library_like_v2, detect_tpl_in_packages
    from noise_normalizer import CATEGORY_LIBRARY, detect_library_like

    record = _jaccard_record()
    mask = get_library_mask(record)
    tpl_path = "smali/okhttp3/internal/cache/DiskLruCache.smali"
    app_path = "smali/com/example/app/MainActivity.smali"

    tpl_hits = detect_tpl_in_packages(
        record["packages"],
        config=record["cascade_config"]["library_mask"],
    )
    detected_packages = {
        package
        for hit in tpl_hits.values()
        if hit["detected"]
        for package in hit["matched_packages"]
    }

    assert detected_packages == mask
    assert detect_library_like(tpl_path, app_record=record)[0] == CATEGORY_LIBRARY
    assert detect_library_like_v2(tpl_path, app_record=record)[0] == CATEGORY_LIBRARY
    assert detect_library_like(app_path, app_record=record) is None
    assert detect_library_like_v2(app_path, app_record=record) is None


@pytest.mark.skip(
    reason=(
        "NOISE-24 ↔ DEEP-24 интеграция mask: NOISE возвращает package-prefix"
        " mask (e.g. {'okhttp3'}), DEEP canonical работает на token-level"
        " (e.g. {'library:okhttp3'}). Прямое применение через"
        " library_reduced_score_canonical(library_mask=...) не выполняет"
        " prefix-matching против code-токенов (e.g. 'okhttp3.Client'). Конфликт"
        " разрешён в волне 24 (mask делегируется через get_library_mask, но"
        " полная prefix-семантика — отдельная задача DEEP-25-MASK-PREFIX-MATCHING)."
        " См. inbox/library-reduced-discovery.md и inbox/library-mask-discovery.md."
    )
)
def test_m_static_library_reduced_score_masks_only_unified_tpl_packages(monkeypatch):
    import m_static_views
    from m_static_views import compare_all

    calls: list[str] = []

    def fake_get_library_mask(app_record: dict) -> set[str]:
        calls.append(app_record["app_id"])
        return set(app_record["mask"])

    monkeypatch.setattr(m_static_views, "get_library_mask", fake_get_library_mask, raising=False)

    features_a = {
        "app_id": "left",
        "mode": "quick",
        "mask": {"okhttp3"},
        "code": {"okhttp3.Client", "legacy.lib.Shared", "com.example.Left"},
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": {"okhttp3", "legacy.lib"},
    }
    features_b = {
        "app_id": "right",
        "mode": "quick",
        "mask": {"okhttp3"},
        "code": {"okhttp3.Client", "legacy.lib.Shared", "com.example.Right"},
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": {"okhttp3", "legacy.lib"},
    }

    result = compare_all(features_a, features_b, layers=["code", "library"])

    assert calls == ["left", "right"]
    assert result["per_layer"]["code"]["score"] == pytest.approx(2 / 4)
    assert result["library_reduced_score"] == pytest.approx(1 / 3)


def test_library_mask_contract_draft_is_documented():
    draft_path = _PROJECT_ROOT / "docs" / "phd-drafts" / "library-mask-contract.md"

    assert draft_path.exists()
    text = draft_path.read_text(encoding="utf-8")
    assert "get_library_mask(app_record)" in text
    assert "USE_LIBRARY_V2" in text
    assert "library_reduced_score" in text

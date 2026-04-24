#!/usr/bin/env python3
"""
Unified M_static comparison: combines all 5 views (code, component, resource, metadata, library).

Supports two modes:
1. Quick mode (APK ZIP only) — uses existing string-set extraction from screening_runner
2. Enhanced mode (unpacked APK dirs) — uses new view modules for resource, component, library

Enhanced mode produces richer similarity scores with per-layer explanations.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.screening_runner import extract_layers_from_apk
    from script.screening_runner import jaccard_similarity
except Exception:
    from screening_runner import extract_layers_from_apk
    from screening_runner import jaccard_similarity

try:
    from script.resource_view import compare_resources
    from script.resource_view import extract_resource_features
    from script.resource_view import resource_explanation_hints
except Exception:
    try:
        from resource_view import compare_resources
        from resource_view import extract_resource_features
        from resource_view import resource_explanation_hints
    except Exception:
        compare_resources = None
        extract_resource_features = None
        resource_explanation_hints = None

try:
    from script.component_view import compare_components
    from script.component_view import component_explanation_hints
    from script.component_view import extract_component_features
except Exception:
    try:
        from component_view import compare_components
        from component_view import component_explanation_hints
        from component_view import extract_component_features
    except Exception:
        compare_components = None
        component_explanation_hints = None
        extract_component_features = None

try:
    from script.library_view_v2 import (
        extract_library_features_v2 as extract_library_features,
        compare_libraries_v2 as compare_libraries,
        library_explanation_hints_v2 as library_explanation_hints,
    )
    from script.library_view import extract_library_features as _extract_library_features_v1
    _LIBRARY_V2 = True
except Exception:
    try:
        from library_view_v2 import (
            extract_library_features_v2 as extract_library_features,
            compare_libraries_v2 as compare_libraries,
            library_explanation_hints_v2 as library_explanation_hints,
        )
        from library_view import extract_library_features as _extract_library_features_v1
        _LIBRARY_V2 = True
    except Exception:
        try:
            from script.library_view import compare_libraries
            from script.library_view import extract_library_features
            from script.library_view import library_explanation_hints
        except Exception:
            try:
                from library_view import compare_libraries
                from library_view import extract_library_features
                from library_view import library_explanation_hints
            except Exception:
                compare_libraries = None
                extract_library_features = None
                library_explanation_hints = None
        _extract_library_features_v1 = extract_library_features
        _LIBRARY_V2 = False

try:
    from script.api_view import compare_api
    from script.api_view import build_markov_chain
except Exception:
    try:
        from api_view import compare_api
        from api_view import build_markov_chain
    except Exception:
        compare_api = None
        build_markov_chain = None

try:
    from script.signing_view import extract_apk_signature_hash
except Exception:
    try:
        from signing_view import extract_apk_signature_hash
    except Exception:
        extract_apk_signature_hash = None

try:
    from script.signing_view import extract_signing_chain
except Exception:
    try:
        from signing_view import extract_signing_chain
    except Exception:
        extract_signing_chain = None

try:
    from script.code_view_v4 import extract_code_view_v4, compare_code_v4
except Exception:
    try:
        from code_view_v4 import extract_code_view_v4, compare_code_v4
    except Exception:
        extract_code_view_v4 = None
        compare_code_v4 = None

try:
    from script.code_view_v4_shingled import compare_code_v4_shingled
except Exception:
    try:
        from code_view_v4_shingled import compare_code_v4_shingled
    except Exception:
        compare_code_v4_shingled = None

try:
    from script.resource_view_v2 import (
        extract_resource_view_v2,
        compare_resource_view_v2,
        ICON_HASH_METHOD as _RESOURCE_V2_ICON_HASH_METHOD,
    )
except Exception:
    try:
        from resource_view_v2 import (
            extract_resource_view_v2,
            compare_resource_view_v2,
            ICON_HASH_METHOD as _RESOURCE_V2_ICON_HASH_METHOD,
        )
    except Exception:
        extract_resource_view_v2 = None
        compare_resource_view_v2 = None
        # Без resource_view_v2 метод хеша неизвестен; используем нейтральный
        # маркер, чтобы кэш всё равно инвалидировался при смене
        # ICON_HASH_METHOD после перезапуска процесса.
        _RESOURCE_V2_ICON_HASH_METHOD = "na"

try:
    from script.feature_cache import get_or_extract as _feature_cache_get_or_extract
except Exception:
    try:
        from feature_cache import get_or_extract as _feature_cache_get_or_extract
    except Exception:
        _feature_cache_get_or_extract = None


ALL_LAYERS = (
    "code",
    "component",
    "resource",
    "metadata",
    "library",
    "api",
    "code_v4",
    "code_v4_shingled",
    "resource_v2",
)

# Weights from cascade-config-schema-v1.
# metadata is used as tiebreaker, not included in weighted score.
# api layer weight is additive; existing weights renormalized when api is included.
# code_v4 / code_v4_shingled are registered with weight 0.0 — layers are plumbed
# through aggregation but are not part of the default weighted score until
# calibrated by EXEC-086. Existing weights are intentionally unchanged.
#
# DEEP-19-LAYER-WEIGHTS-CALIBRATE: активные веса калиброваны до суммы
# ``1.0``. Ранее использовались интуитивные значения
# ``{code: 0.45, component: 0.25, resource: 0.20, library: 0.10, api: 0.15}``
# с суммой ``1.15``, что нарушало инвариант «веса распределения
# единичны» (критик волны 18,
# ``inbox/critics/deep-verification-2026-04-24.md`` раздел 1).
#
# Метод калибровки — нормировка ``new_w = old_w / sum(old_w)``:
# относительные пропорции сохранены (code/library = 4.5,
# component/api = 5/3), поэтому агрегированный score
# ``full_similarity_score`` и ``library_reduced_score`` численно
# совпадают со значениями до калибровки (формула в ``compare_all``
# делит на ``weight_total``, см. строки ~870–900 ниже). Калибровка —
# инвариантное исправление словаря, а не изменение метрики.
#
# Обучение весов по корпусу (EXEC-086, логистическая регрессия / learn-
# to-rank по per-view scores) остаётся в бэклоге: задача заблокирована
# EXEC-080 (train/test split с AndroZoo key).
LAYER_WEIGHTS = {
    "code": 0.45 / 1.15,
    "component": 0.25 / 1.15,
    "resource": 0.20 / 1.15,
    "library": 0.10 / 1.15,
    "api": 0.15 / 1.15,
    "code_v4": 0.0,
    "code_v4_shingled": 0.0,
    # resource_v2: подключено, не активировано до калибровки EXEC-086.
    "resource_v2": 0.0,
}

# Predefined ablation configurations.
ABLATION_CONFIGS = {
    "code_only": ["code"],
    "code_metadata": ["code", "metadata"],
    "all_5_layers": ["code", "component", "resource", "metadata", "library"],
    "all_6_layers": ["code", "component", "resource", "metadata", "library", "api"],
    "code_resource": ["code", "resource"],
    "code_component": ["code", "component"],
    "code_library": ["code", "library"],
    "code_api": ["code", "api"],
    "resource_component_library": ["resource", "component", "library"],
    "code_only_v4": ["code_v4"],
    "code_only_v4_shingled": ["code_v4_shingled"],
    "all_code_variants": ["code", "code_v4", "code_v4_shingled"],
    "resource_only_v2": ["resource_v2"],
}


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_all_features(
    apk_path: str | None = None,
    unpacked_dir: str | None = None,
    cache_dir: str | None = None,
    feature_version: str = "v1",
) -> dict:
    """Extract features from an APK in quick or enhanced mode.

    Parameters
    ----------
    apk_path:
        Path to an APK ZIP file.  Used for quick mode (string-set layers
        via screening_runner).
    unpacked_dir:
        Path to an apktool-decoded directory.  Enables enhanced mode with
        richer per-layer features from resource_view, component_view,
        library_view.
    cache_dir:
        Опциональная директория для устойчивого кэша признаков по
        SHA-256 APK (EXEC-REPR-FEATURE-CACHE). По умолчанию ``None`` —
        кэш выключен, поведение не меняется. Кэш включается только
        когда есть ``apk_path`` (иначе нет стабильного файла-ключа).
    feature_version:
        Версия схемы признаков для инвалидации кэша при смене формата.

    Returns
    -------
    dict with keys: code, component, resource, metadata, library, signing,
    code_v4, resource_v2, mode.
    `signing` is a dict with key `hash` holding the SHA-256 of the APK
    signature certificate, or None if apk_path was not provided or the
    APK has no recognisable signature.
    `code_v4` is a dict with keys `method_fingerprints`, `total_methods`,
    `mode` — method-level fuzzy fingerprint of opcode sequences via
    code_view_v4. Empty stub (`mode="v4_unavailable"`) when apk_path is
    absent or code_view_v4 dependency is unavailable.
    `resource_v2` is a dict with keys `res_strings`, `res_drawables`,
    `res_layouts`, `assets_bin`, `icon_phash`, `mode` — sub-categorised
    resource signal via resource_view_v2. Empty stub
    (`mode="v2_unavailable"`) when unpacked_dir is absent or the
    resource_view_v2 dependency is unavailable (EXEC-R_resource_v2-INTEGRATION).

    Layers ``code_v4`` and ``code_v4_shingled`` are now first-class
    entries in :data:`ALL_LAYERS` and can be selected via
    ``selected_layers`` when calling ``compare_all`` / ``run_ablation``.
    Per-layer comparison is dispatched through
    :func:`compare_m_static_layer`, which delegates ``code_v4`` to
    :func:`compare_code_v4` and ``code_v4_shingled`` to
    :func:`compare_code_v4_shingled`. Their default weight in
    :data:`LAYER_WEIGHTS` is ``0.0`` until calibration (EXEC-086), so
    they are plumbed through aggregation without affecting the default
    weighted score.

    Layer ``resource_v2`` is also registered in :data:`ALL_LAYERS` with
    default weight ``0.0`` in :data:`LAYER_WEIGHTS` — signal «подключено,
    не активировано до калибровки EXEC-086». Per-layer comparison
    delegates to :func:`compare_resource_view_v2`.
    """
    def _do_extract() -> dict:
        if unpacked_dir is not None:
            return _extract_enhanced(unpacked_dir, apk_path)
        if apk_path is not None:
            return _extract_quick(apk_path)
        raise ValueError("Either apk_path or unpacked_dir must be provided.")

    # Кэш включается только когда есть APK (устойчивый ключ по SHA-256
    # файла) и подключён модуль feature_cache. В остальных случаях —
    # backward-compat путь без кэша.
    if (
        cache_dir is not None
        and apk_path is not None
        and _feature_cache_get_or_extract is not None
    ):
        # REPR-16-WHASH-HAAR: метод перцептивного хеша иконки входит в
        # ключ кэша, чтобы смена ANDROID_SIM_IMAGE_HASH_METHOD
        # автоматически инвалидировала старые записи со смешанными
        # методами (см. resource_view_v2 для деталей).
        effective_version = "{}__ihash-{}".format(
            feature_version, _RESOURCE_V2_ICON_HASH_METHOD,
        )
        return _feature_cache_get_or_extract(
            apk_path=apk_path,
            extract_fn=_do_extract,
            cache_dir=cache_dir,
            feature_version=effective_version,
        )
    return _do_extract()


# ---------------------------------------------------------------------------
# EXEC-R_metadata_v2-CREATOR: creator-centric Жаккар разрешений
# ---------------------------------------------------------------------------

# Минимальный расширяемый словарь «префикс разрешения -> creator-group».
# Порядок ключей важен: более длинные/специфичные префиксы должны идти
# раньше более коротких, чтобы победил самый точный матч. Проверка ведётся
# по .startswith на имени разрешения (без префикса "uses_permission:").
# Неизвестные разрешения попадают в группу "third_party".
PERMISSION_CREATOR_GROUPS: tuple[tuple[str, str], ...] = (
    ("com.google.", "google"),
    ("com.android.vending.", "google"),
    ("com.facebook.", "facebook"),
    ("android.permission.", "android"),
    ("android.", "android"),
)


def _creator_group_for_permission(permission_name: str) -> str:
    """Сопоставить имя разрешения с creator-группой.

    Parameters
    ----------
    permission_name:
        Полное имя разрешения без префикса ``uses_permission:``. Например
        ``android.permission.INTERNET`` или
        ``com.google.android.c2dm.permission.RECEIVE``.

    Returns
    -------
    str
        Имя creator-группы. По умолчанию ``"third_party"``, если имя
        не совпало ни с одним префиксом из :data:`PERMISSION_CREATOR_GROUPS`.
    """
    for prefix, group in PERMISSION_CREATOR_GROUPS:
        if permission_name.startswith(prefix):
            return group
    return "third_party"


def _enrich_metadata_with_perm_groups(metadata: set[str]) -> set[str]:
    """Добавить токены ``perm_group:<creator>:<permission>`` рядом с ``uses_permission:*``.

    Работает по идемпотентной схеме: старые токены ``uses_permission:*`` остаются
    на месте, новые ``perm_group:*`` добавляются параллельно. Это даёт два
    варианта Жаккара — по плоским разрешениям и по creator-группам —
    для сравнения на экспериментах (follow-up из research/R-07).
    """
    if not metadata:
        return metadata
    new_tokens: set[str] = set()
    for token in metadata:
        if not token.startswith("uses_permission:"):
            continue
        permission_name = token[len("uses_permission:"):]
        if not permission_name:
            continue
        group = _creator_group_for_permission(permission_name)
        new_tokens.add("perm_group:{}:{}".format(group, permission_name))
    if new_tokens:
        metadata = set(metadata)
        metadata.update(new_tokens)
    return metadata


def _extract_signing(apk_path: str | None) -> dict:
    """Return signing signal bundle for the given APK, or a null stub.

    EXEC-R_metadata_v2-CREATOR: словарь также содержит ключ ``chain`` —
    список ``dict{issuer, subject, sha256}`` для каждого сертификата в
    цепочке подписи. Это обогащённые метаданные для объяснителей и
    веб-сервиса; в расчёт Жаккара напрямую не попадают.
    """
    if apk_path is None or extract_apk_signature_hash is None:
        return {"hash": None, "chain": []}
    bundle: dict[str, Any] = {"hash": None, "chain": []}
    try:
        bundle["hash"] = extract_apk_signature_hash(Path(apk_path))
    except Exception:
        bundle["hash"] = None
    if extract_signing_chain is not None:
        try:
            bundle["chain"] = extract_signing_chain(Path(apk_path))
        except Exception:
            bundle["chain"] = []
    return bundle


def _extract_code_v4(apk_path: str | None) -> dict:
    """Return code_view_v4 bundle or a null stub.

    EXEC-082a-INTEGRATION: method-level fuzzy fingerprint signal is
    available only when apk_path is provided and code_view_v4
    dependency is importable. Otherwise an empty stub is returned to
    keep the feature bundle contract stable.
    """
    if apk_path is None or extract_code_view_v4 is None:
        return {"method_fingerprints": {}, "total_methods": 0, "mode": "v4_unavailable"}
    try:
        return extract_code_view_v4(apk_path)
    except Exception:
        return {"method_fingerprints": {}, "total_methods": 0, "mode": "v4_unavailable"}


def _extract_resource_v2(unpacked_dir: str | None) -> dict:
    """Return resource_view_v2 bundle or a null stub.

    EXEC-R_resource_v2-INTEGRATION: sub-categorised resource signal is
    available only when unpacked_dir is provided and resource_view_v2
    dependency is importable. Otherwise an empty stub is returned to
    keep the feature bundle contract stable.
    """
    if unpacked_dir is None or extract_resource_view_v2 is None:
        return {
            "res_strings": set(),
            "res_drawables": set(),
            "res_layouts": set(),
            "assets_bin": set(),
            "icon_phash": None,
            "mode": "v2_unavailable",
        }
    try:
        return extract_resource_view_v2(unpacked_dir)
    except Exception:
        return {
            "res_strings": set(),
            "res_drawables": set(),
            "res_layouts": set(),
            "assets_bin": set(),
            "icon_phash": None,
            "mode": "v2_unavailable",
        }


def _extract_quick(apk_path: str) -> dict:
    """Quick extraction: string-set layers from APK ZIP."""
    resolved = Path(apk_path).expanduser().resolve()
    layers = extract_layers_from_apk(resolved)
    metadata = _enrich_metadata_with_perm_groups(layers.get("metadata", set()))
    return {
        "code": layers.get("code", set()),
        "component": layers.get("component", set()),
        "resource": layers.get("resource", set()),
        "metadata": metadata,
        "library": layers.get("library", set()),
        "signing": _extract_signing(str(resolved)),
        "code_v4": _extract_code_v4(str(resolved)),
        # resource_v2 requires unpacked_dir — null stub in quick mode.
        "resource_v2": _extract_resource_v2(None),
        "mode": "quick",
    }


def _extract_enhanced(unpacked_dir: str, apk_path: str | None) -> dict:
    """Enhanced extraction: per-view modules + optional quick fallback."""
    features: dict[str, Any] = {"mode": "enhanced"}

    # Code layer: keep quick-mode string set if APK ZIP is available.
    if apk_path is not None:
        resolved = Path(apk_path).expanduser().resolve()
        quick_layers = extract_layers_from_apk(resolved)
        features["code"] = quick_layers.get("code", set())
        features["metadata"] = _enrich_metadata_with_perm_groups(
            quick_layers.get("metadata", set())
        )
    else:
        features["code"] = set()
        features["metadata"] = set()

    # Resource view
    if extract_resource_features is not None:
        try:
            features["resource"] = extract_resource_features(unpacked_dir)
        except Exception:
            features["resource"] = set()
    else:
        features["resource"] = set()

    # Component view
    if extract_component_features is not None:
        try:
            features["component"] = extract_component_features(unpacked_dir)
        except Exception:
            features["component"] = set()
    else:
        features["component"] = set()

    # Library view
    if _LIBRARY_V2 and apk_path is not None and extract_library_features is not None:
        # v2: works from APK file directly
        try:
            features["library"] = extract_library_features(apk_path)
        except Exception:
            features["library"] = set()
    elif not _LIBRARY_V2 and extract_library_features is not None:
        # v1: works from unpacked smali dir
        try:
            features["library"] = extract_library_features(unpacked_dir)
        except Exception:
            features["library"] = set()
    elif _LIBRARY_V2 and apk_path is None and _extract_library_features_v1 is not None:
        # v2 active but no apk_path: fall back to v1 for enhanced mode
        try:
            features["library"] = _extract_library_features_v1(unpacked_dir)
        except Exception:
            features["library"] = set()
    else:
        features["library"] = set()

    # Signing signal: only available from the APK ZIP itself.
    features["signing"] = _extract_signing(apk_path)

    # code_view_v4: method-level fuzzy fingerprint; only from APK ZIP.
    features["code_v4"] = _extract_code_v4(apk_path)

    # resource_view_v2: sub-categorised resource signal from unpacked dir.
    features["resource_v2"] = _extract_resource_v2(unpacked_dir)

    return features


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def _jaccard_on_sets(set_a: set, set_b: set) -> float:
    """Jaccard similarity on two plain string sets."""
    return float(jaccard_similarity(set_a, set_b))


def _compare_layer_quick(layer: str, feat_a: Any, feat_b: Any) -> dict:
    """Fallback comparison: Jaccard on string sets."""
    left = feat_a if isinstance(feat_a, set) else set()
    right = feat_b if isinstance(feat_b, set) else set()
    return {"score": _jaccard_on_sets(left, right), "status": "quick"}


def _compare_code(
    feat_a: Any,
    feat_b: Any,
    code_ged_score: float | None,
    code_v2_hash_a: str | None = None,
    code_v2_hash_b: str | None = None,
    code_v3_set_a: Any | None = None,
    code_v3_set_b: Any | None = None,
) -> dict:
    """Compare code layer.

    Priority: GED > v3 method-opcode Jaccard > v2 TLSH > v1 Jaccard on DEX names.
    """
    if code_ged_score is not None:
        return {"score": float(code_ged_score), "status": "ged"}
    if code_v3_set_a is not None or code_v3_set_b is not None:
        try:
            from code_view_v3 import compare_code_v3
        except ImportError:
            try:
                from script.code_view_v3 import compare_code_v3
            except ImportError:
                compare_code_v3 = None
        if compare_code_v3 is not None:
            return compare_code_v3(code_v3_set_a, code_v3_set_b)
    if code_v2_hash_a is not None or code_v2_hash_b is not None:
        try:
            from code_view_v2 import compare_code_v2
        except ImportError:
            try:
                from script.code_view_v2 import compare_code_v2
            except ImportError:
                compare_code_v2 = None
        if compare_code_v2 is not None:
            return compare_code_v2(code_v2_hash_a, code_v2_hash_b)
    left = feat_a if isinstance(feat_a, set) else set()
    right = feat_b if isinstance(feat_b, set) else set()
    return {"score": _jaccard_on_sets(left, right), "status": "jaccard_dex"}


def _compare_resource_enhanced(feat_a: dict, feat_b: dict) -> dict:
    """Enhanced resource comparison via resource_view module."""
    if compare_resources is None:
        return {"score": 0.0, "status": "not_available"}
    comparison = compare_resources(feat_a, feat_b)
    return {
        "score": float(comparison.get("resource_jaccard_score", 0.0)),
        "status": "enhanced",
        "details": {
            "added": len(comparison.get("added", [])),
            "removed": len(comparison.get("removed", [])),
            "modified": len(comparison.get("modified", [])),
            "unchanged_count": comparison.get("unchanged_count", 0),
        },
    }


def _compare_component_enhanced(feat_a: dict, feat_b: dict) -> dict:
    """Enhanced component comparison via component_view module."""
    if compare_components is None:
        return {"score": 0.0, "status": "not_available"}
    comparison = compare_components(feat_a, feat_b)
    per_type = comparison.get("per_type", {})
    return {
        "score": float(comparison.get("component_jaccard_score", 0.0)),
        "status": "enhanced",
        "details": {
            key: {"jaccard": section.get("jaccard", 0.0)}
            for key, section in per_type.items()
        },
    }


def _compare_api(chain_a=None, chain_b=None) -> dict:
    """Compare API Markov chains via cosine similarity (R_api layer)."""
    if compare_api is None:
        return {"score": 0.0, "status": "not_available"}
    return compare_api(chain_a, chain_b)


def _include_layer_in_weighted_score(layer: str, layer_result: dict[str, Any]) -> bool:
    """Return whether a layer should contribute to weighted aggregation.

    ``api`` with ``status == "both_empty"`` means extractor returned no API
    signal for either APK. That is treated as missing evidence, not as a real
    similarity/dissimilarity observation, so the layer is removed from the
    weighted average and the remaining weights are renormalized.

    ``one_empty`` stays included: one side has API evidence while the other
    does not, which remains an informative asymmetric signal.
    """
    if layer != "api":
        return True
    return layer_result.get("status") != "both_empty"


def _compare_library_enhanced(feat_a: dict, feat_b: dict) -> dict:
    """Enhanced library comparison via library_view module.

    REPR-19/20: помимо симметричного Жаккара ``score`` возвращает явные
    каналы ``jaccard``, ``tversky_a`` (|A ∩ B| / |A|), ``tversky_b``
    (|A ∩ B| / |B|), ``overlap_min`` (|A ∩ B| / min(|A|,|B|)) и, если
    доступен IDF snapshot, дополняет их ``jaccard_idf``,
    ``tversky_a_idf``, ``tversky_b_idf``. Старый ключ ``score`` =
    plain Jaccard сохранён для обратной совместимости с
    `compare_all.full_similarity_score` до калибровки весов EXEC-086.
    """
    if compare_libraries is None:
        return {"score": 0.0, "status": "not_available"}
    comparison = compare_libraries(feat_a, feat_b)
    if _LIBRARY_V2:
        # v2 compat: keys from compare_libraries_v2.
        # score_tversky_asym_ab (alpha=0.9, beta=0.1) ≈ |A ∩ B| / |A| при
        # больших множествах, но формально = shared / (shared + 0.9·only_a +
        # 0.1·only_b). Для прямых «a-pure» / «b-pure» пересчитываем из
        # shared/only_a/only_b — тогда семантика `tversky_a = |A ∩ B| / |A|`
        # выполняется точно и безусловно.
        shared = len(comparison.get("shared_libraries", []))
        only_a = len(comparison.get("only_in_a", []))
        only_b = len(comparison.get("only_in_b", []))
        size_a = shared + only_a
        size_b = shared + only_b
        jaccard_value = float(comparison.get("jaccard", 0.0))
        tversky_a = (shared / size_a) if size_a > 0 else 0.0
        tversky_b = (shared / size_b) if size_b > 0 else 0.0
        min_size = min(size_a, size_b) if (size_a and size_b) else 0
        overlap_min = (shared / min_size) if min_size > 0 else 0.0
        result = {
            "score": jaccard_value,
            "status": "enhanced_v2",
            "jaccard": jaccard_value,
            "tversky_a": float(tversky_a),
            "tversky_b": float(tversky_b),
            "overlap_min": float(overlap_min),
            # Пробрасываем и исходные score_* из compare_libraries_v2,
            # чтобы downstream-потребителям не пришлось их заново считать.
            "score_jaccard": float(comparison.get("score_jaccard", jaccard_value)),
            "score_tversky_asym_ab": float(comparison.get("score_tversky_asym_ab", 0.0)),
            "score_tversky_asym_ba": float(comparison.get("score_tversky_asym_ba", 0.0)),
            "score_overlap": float(comparison.get("score_overlap", 0.0)),
            "details": {
                "weighted_library_score": jaccard_value,
                "shared_count": shared,
                "a_only_count": only_a,
                "b_only_count": only_b,
            },
        }
        if "jaccard_idf" in comparison:
            result["jaccard_idf"] = float(comparison["jaccard_idf"])
        if "tversky_a_idf" in comparison:
            result["tversky_a_idf"] = float(comparison["tversky_a_idf"])
        if "tversky_b_idf" in comparison:
            result["tversky_b_idf"] = float(comparison["tversky_b_idf"])
        if "jaccard_idf" in result:
            result["details"]["weighted_library_score_idf"] = result["jaccard_idf"]
        return result
    jaccard_value = float(comparison.get("library_jaccard_score", 0.0))
    shared = len(comparison.get("shared", []))
    only_a = len(comparison.get("a_only", []))
    only_b = len(comparison.get("b_only", []))
    size_a = shared + only_a
    size_b = shared + only_b
    tversky_a = (shared / size_a) if size_a > 0 else 0.0
    tversky_b = (shared / size_b) if size_b > 0 else 0.0
    min_size = min(size_a, size_b) if (size_a and size_b) else 0
    overlap_min = (shared / min_size) if min_size > 0 else 0.0
    return {
        "score": jaccard_value,
        "status": "enhanced",
        "jaccard": jaccard_value,
        "tversky_a": float(tversky_a),
        "tversky_b": float(tversky_b),
        "overlap_min": float(overlap_min),
        "details": {
            "weighted_library_score": comparison.get("weighted_library_score", 0.0),
            "shared_count": shared,
            "a_only_count": only_a,
            "b_only_count": only_b,
        },
    }


def compare_m_static_layer(
    layer_name: str,
    features_a: Any,
    features_b: Any,
) -> dict:
    """Dispatch a single-layer comparison to the appropriate backend.

    EXEC-082a-SCORING: exposes a uniform entry point so callers can ask
    for any registered layer by name without having to know which helper
    implements it. New fuzzy-fingerprint layers ``code_v4`` /
    ``code_v4_shingled`` delegate to ``compare_code_v4`` /
    ``compare_code_v4_shingled``; other layers fall back to the existing
    Jaccard-on-sets path (``_compare_layer_quick``).

    Parameters
    ----------
    layer_name:
        One of the values in :data:`ALL_LAYERS`.
    features_a, features_b:
        Side-specific payloads. For ``code_v4`` / ``code_v4_shingled``
        this is the bundle produced by ``extract_code_view_v4``
        (``{"method_fingerprints": ..., "total_methods": ..., "mode": ...}``).
        For set-valued layers it is a plain ``set``.

    Returns
    -------
    dict
        Always contains ``score`` (``float``) and ``status`` (``str``);
        ``code_v4``-family results additionally propagate
        ``matched_methods`` / ``union_methods`` as returned by the
        underlying comparator.
    """
    if layer_name == "code_v4":
        if compare_code_v4 is None:
            return {"score": 0.0, "status": "v4_unavailable"}
        result = compare_code_v4(features_a, features_b)
        return {
            "score": float(result.get("score", 0.0)),
            "status": result.get("status", "jaccard_ok"),
            "matched_methods": result.get("matched_methods", 0),
            "union_methods": result.get("union_methods", 0),
        }
    if layer_name == "code_v4_shingled":
        if compare_code_v4_shingled is None:
            return {"score": 0.0, "status": "v4_shingled_unavailable"}
        result = compare_code_v4_shingled(features_a, features_b)
        return {
            "score": float(result.get("score", 0.0)),
            "status": result.get("status", "jaccard_ok"),
            "matched_methods": result.get("matched_methods", 0),
            "union_methods": result.get("union_methods", 0),
        }
    if layer_name == "resource_v2":
        if compare_resource_view_v2 is None:
            return {"score": 0.0, "status": "v2_unavailable", "combined_score": 0.0}
        if not isinstance(features_a, dict) or not isinstance(features_b, dict):
            return {"score": 0.0, "status": "v2_unavailable", "combined_score": 0.0}
        result = dict(compare_resource_view_v2(features_a, features_b))
        combined = float(result.get("combined_score", 0.0))
        result.setdefault("combined_score", combined)
        result["score"] = combined
        result.setdefault("status", "ok")
        return result
    if layer_name == "library":
        # REPR-19-TVERSKY-WIRE: единый диспетчер для `library` должен
        # отдавать расширенный контракт с Tversky/overlap, когда на вход
        # приходит dict (v2 формат с ключом `libraries`). Для плоского
        # set — fallback на старый Жаккар через `_compare_layer_quick`.
        if isinstance(features_a, dict) and isinstance(features_b, dict):
            return _compare_library_enhanced(features_a, features_b)
        return _compare_layer_quick(layer_name, features_a, features_b)
    # Fallback for the legacy set-valued layers.
    return _compare_layer_quick(layer_name, features_a, features_b)


def _collect_hints(
    features_a: dict,
    features_b: dict,
    per_layer: dict,
) -> list[dict]:
    """Collect explanation hints from enhanced-mode view modules."""
    hints: list[dict] = []

    if per_layer.get("resource", {}).get("status") == "enhanced":
        if resource_explanation_hints is not None and compare_resources is not None:
            try:
                comparison = compare_resources(
                    features_a["resource"], features_b["resource"],
                )
                hints.extend(resource_explanation_hints(comparison))
            except Exception:
                pass

    if per_layer.get("component", {}).get("status") == "enhanced":
        if component_explanation_hints is not None and compare_components is not None:
            try:
                comparison = compare_components(
                    features_a["component"], features_b["component"],
                )
                hints.extend(component_explanation_hints(comparison))
            except Exception:
                pass

    if per_layer.get("library", {}).get("status") == "enhanced":
        if library_explanation_hints is not None and compare_libraries is not None:
            try:
                comparison = compare_libraries(
                    features_a["library"], features_b["library"],
                )
                hints.extend(library_explanation_hints(comparison))
            except Exception:
                pass

    return hints


# ---------------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------------

def compare_all(
    features_a: dict,
    features_b: dict,
    layers: list[str] | None = None,
    code_ged_score: float | None = None,
    code_v2_hash_a: str | None = None,
    code_v2_hash_b: str | None = None,
    code_v3_set_a: Any | None = None,
    code_v3_set_b: Any | None = None,
    api_chain_a: Any | None = None,
    api_chain_b: Any | None = None,
) -> dict:
    """Compare two APKs across selected M_static layers.

    Parameters
    ----------
    features_a, features_b:
        Feature dicts produced by ``extract_all_features``.
    layers:
        Optional subset of layers to use (default: all 6).
    code_ged_score:
        Pre-computed GED similarity for the code layer.  When provided
        the module uses it instead of computing Jaccard on DEX names.
    code_v2_hash_a, code_v2_hash_b:
        Optional TLSH hashes from ``extract_code_v2_hash`` (SOTA-001 v2 mode).
        When provided and code_ged_score is None, v2 TLSH is used instead of
        v1 Jaccard on DEX names.
    code_v3_set_a, code_v3_set_b:
        Optional frozensets from ``extract_code_v3_set`` (MOSDroid v3 mode).
        When provided and code_ged_score is None, v3 method-opcode Jaccard is
        used (priority over v2 TLSH).
    api_chain_a, api_chain_b:
        Optional Markov chain dicts from ``build_markov_chain`` (MaMaDroid R_api mode).
        When provided, enables API call transition cosine similarity.

    Returns
    -------
    dict with full_similarity_score, per_layer, library_reduced_score,
    explanation_hints, layers_used, and mode.
    """
    selected = list(layers) if layers else list(ALL_LAYERS)
    mode_a = features_a.get("mode", "quick")
    mode_b = features_b.get("mode", "quick")
    is_enhanced = mode_a == "enhanced" and mode_b == "enhanced"
    mode = "enhanced" if is_enhanced else "quick"

    per_layer: dict[str, dict] = {}

    for layer in selected:
        feat_a = features_a.get(layer, set())
        feat_b = features_b.get(layer, set())

        if layer == "code":
            per_layer["code"] = _compare_code(
                feat_a, feat_b, code_ged_score,
                code_v2_hash_a=code_v2_hash_a,
                code_v2_hash_b=code_v2_hash_b,
                code_v3_set_a=code_v3_set_a,
                code_v3_set_b=code_v3_set_b,
            )

        elif layer == "metadata":
            per_layer["metadata"] = _compare_layer_quick(layer, feat_a, feat_b)

        elif layer == "resource":
            if is_enhanced and isinstance(feat_a, dict) and isinstance(feat_b, dict):
                per_layer["resource"] = _compare_resource_enhanced(feat_a, feat_b)
            else:
                per_layer["resource"] = _compare_layer_quick(layer, feat_a, feat_b)

        elif layer == "component":
            if is_enhanced and isinstance(feat_a, dict) and isinstance(feat_b, dict):
                per_layer["component"] = _compare_component_enhanced(feat_a, feat_b)
            else:
                per_layer["component"] = _compare_layer_quick(layer, feat_a, feat_b)

        elif layer == "library":
            if is_enhanced and isinstance(feat_a, dict) and isinstance(feat_b, dict):
                per_layer["library"] = _compare_library_enhanced(feat_a, feat_b)
            else:
                per_layer["library"] = _compare_layer_quick(layer, feat_a, feat_b)

        elif layer == "api":
            per_layer["api"] = _compare_api(api_chain_a, api_chain_b)

    # Weighted score — metadata excluded from weighted average.
    weighted_sum = 0.0
    weight_total = 0.0
    for layer in selected:
        weight = LAYER_WEIGHTS.get(layer)
        if weight is None:
            continue
        layer_result = per_layer.get(layer, {})
        if not _include_layer_in_weighted_score(layer, layer_result):
            continue
        layer_score = layer_result.get("score", 0.0)
        weighted_sum += weight * layer_score
        weight_total += weight

    full_similarity_score = weighted_sum / weight_total if weight_total > 0.0 else 0.0

    # Library-reduced score.
    reduced_sum = 0.0
    reduced_total = 0.0
    for layer in selected:
        if layer == "library":
            continue
        weight = LAYER_WEIGHTS.get(layer)
        if weight is None:
            continue
        layer_result = per_layer.get(layer, {})
        if not _include_layer_in_weighted_score(layer, layer_result):
            continue
        layer_score = layer_result.get("score", 0.0)
        reduced_sum += weight * layer_score
        reduced_total += weight

    library_reduced_score = reduced_sum / reduced_total if reduced_total > 0.0 else 0.0

    # Explanation hints.
    if is_enhanced:
        explanation_hints = _collect_hints(features_a, features_b, per_layer)
    else:
        explanation_hints = []

    return {
        "full_similarity_score": float(full_similarity_score),
        "per_layer": per_layer,
        "library_reduced_score": float(library_reduced_score),
        "explanation_hints": explanation_hints,
        "layers_used": selected,
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Ablation
# ---------------------------------------------------------------------------

def run_ablation(
    features_a: dict,
    features_b: dict,
    code_ged_score: float | None = None,
    code_v2_hash_a: str | None = None,
    code_v2_hash_b: str | None = None,
    code_v3_set_a: Any | None = None,
    code_v3_set_b: Any | None = None,
    api_chain_a: Any | None = None,
    api_chain_b: Any | None = None,
) -> dict:
    """Compare with multiple layer combinations for ablation analysis.

    Returns dict of configuration_name -> compare_all() result.
    """
    results: dict[str, dict] = {}
    for config_name, layer_list in ABLATION_CONFIGS.items():
        results[config_name] = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=layer_list,
            code_ged_score=code_ged_score,
            code_v2_hash_a=code_v2_hash_a,
            code_v2_hash_b=code_v2_hash_b,
            code_v3_set_a=code_v3_set_a,
            code_v3_set_b=code_v3_set_b,
            api_chain_a=api_chain_a,
            api_chain_b=api_chain_b,
        )
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    """JSON-safe serializer for sets and tuples."""
    if isinstance(obj, set):
        return sorted(str(item) for item in obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError("Object of type {} is not JSON serializable".format(type(obj).__name__))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="m_static_views",
        description="Unified M_static comparison across all 5 views.",
    )
    sub = parser.add_subparsers(dest="command")

    compare_p = sub.add_parser("compare", help="Compare two APKs across all layers")
    compare_p.add_argument("--a-dir", help="Unpacked APK directory for A (enhanced mode)")
    compare_p.add_argument("--b-dir", help="Unpacked APK directory for B (enhanced mode)")
    compare_p.add_argument("--a-apk", help="APK ZIP path for A (quick mode fallback)")
    compare_p.add_argument("--b-apk", help="APK ZIP path for B (quick mode fallback)")
    compare_p.add_argument("--code-ged-score", type=float, default=None, help="Pre-computed GED score for code layer")
    compare_p.add_argument("--layers", help="Comma-separated layer list (default: all)")
    compare_p.add_argument("--output", help="Write JSON result to file")

    ablation_p = sub.add_parser("ablation", help="Run ablation study across layer combinations")
    ablation_p.add_argument("--a-dir", help="Unpacked APK directory for A")
    ablation_p.add_argument("--b-dir", help="Unpacked APK directory for B")
    ablation_p.add_argument("--a-apk", help="APK ZIP path for A")
    ablation_p.add_argument("--b-apk", help="APK ZIP path for B")
    ablation_p.add_argument("--code-ged-score", type=float, default=None, help="Pre-computed GED score for code layer")
    ablation_p.add_argument("--output", help="Write JSON result to file")

    return parser.parse_args()


def _write_output(payload: dict, output_path: str | None) -> None:
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False, default=_serialize)
    if output_path:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload_json + "\n", encoding="utf-8")
        return
    print(payload_json)


def _resolve_features(args: argparse.Namespace, side: str) -> dict:
    """Resolve feature dict for one side from CLI arguments."""
    apk_dir = getattr(args, "{}_dir".format(side), None)
    apk_path = getattr(args, "{}_apk".format(side), None)
    if apk_dir is None and apk_path is None:
        raise SystemExit("At least --{}-dir or --{}-apk must be provided.".format(side, side))
    return extract_all_features(apk_path=apk_path, unpacked_dir=apk_dir)


def main() -> None:
    args = parse_args()

    if args.command == "compare":
        features_a = _resolve_features(args, "a")
        features_b = _resolve_features(args, "b")

        layers = None
        if args.layers:
            layers = [layer.strip() for layer in args.layers.split(",") if layer.strip()]

        result = compare_all(
            features_a=features_a,
            features_b=features_b,
            layers=layers,
            code_ged_score=args.code_ged_score,
        )
        _write_output(result, args.output)

    elif args.command == "ablation":
        features_a = _resolve_features(args, "a")
        features_b = _resolve_features(args, "b")

        result = run_ablation(
            features_a=features_a,
            features_b=features_b,
            code_ged_score=args.code_ged_score,
        )
        _write_output(result, args.output)

    else:
        raise SystemExit("Usage: m_static_views.py {compare|ablation} [options]")


if __name__ == "__main__":
    main()

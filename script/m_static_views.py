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


ALL_LAYERS = ("code", "component", "resource", "metadata", "library", "api")

# Weights from cascade-config-schema-v1.
# metadata is used as tiebreaker, not included in weighted score.
# api layer weight is additive; existing weights renormalized when api is included.
LAYER_WEIGHTS = {
    "code": 0.45,
    "component": 0.25,
    "resource": 0.20,
    "library": 0.10,
    "api": 0.15,
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
}


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_all_features(
    apk_path: str | None = None,
    unpacked_dir: str | None = None,
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

    Returns
    -------
    dict with keys: code, component, resource, metadata, library, mode.
    """
    if unpacked_dir is not None:
        return _extract_enhanced(unpacked_dir, apk_path)
    if apk_path is not None:
        return _extract_quick(apk_path)
    raise ValueError("Either apk_path or unpacked_dir must be provided.")


def _extract_quick(apk_path: str) -> dict:
    """Quick extraction: string-set layers from APK ZIP."""
    resolved = Path(apk_path).expanduser().resolve()
    layers = extract_layers_from_apk(resolved)
    return {
        "code": layers.get("code", set()),
        "component": layers.get("component", set()),
        "resource": layers.get("resource", set()),
        "metadata": layers.get("metadata", set()),
        "library": layers.get("library", set()),
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
        features["metadata"] = quick_layers.get("metadata", set())
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
    if chain_a is None and chain_b is None:
        return {"score": 0.0, "status": "no_data"}
    if compare_api is None:
        return {"score": 0.0, "status": "not_available"}
    return compare_api(chain_a, chain_b)


def _compare_library_enhanced(feat_a: dict, feat_b: dict) -> dict:
    """Enhanced library comparison via library_view module."""
    if compare_libraries is None:
        return {"score": 0.0, "status": "not_available"}
    comparison = compare_libraries(feat_a, feat_b)
    if _LIBRARY_V2:
        # v2 compat: keys from compare_libraries_v2
        return {
            "score": float(comparison.get("jaccard", 0.0)),
            "status": "enhanced_v2",
            "details": {
                "weighted_library_score": comparison.get("jaccard", 0.0),
                "shared_count": len(comparison.get("shared_libraries", [])),
                "a_only_count": len(comparison.get("only_in_a", [])),
                "b_only_count": len(comparison.get("only_in_b", [])),
            },
        }
    return {
        "score": float(comparison.get("library_jaccard_score", 0.0)),
        "status": "enhanced",
        "details": {
            "weighted_library_score": comparison.get("weighted_library_score", 0.0),
            "shared_count": len(comparison.get("shared", [])),
            "a_only_count": len(comparison.get("a_only", [])),
            "b_only_count": len(comparison.get("b_only", [])),
        },
    }


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
        layer_score = per_layer.get(layer, {}).get("score", 0.0)
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
        layer_score = per_layer.get(layer, {}).get("score", 0.0)
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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    wait,
)
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DETAILED_JSON_SCHEMA_VERSION = "deep-004-v1"

# EXEC-091-EXEC: политика реального сокращения углублённого сравнения.
# Если запись кандидата уже помечена на первичном отборе как
# shortcut_applied=True с причиной "high_confidence_signature_match",
# pairwise-слой пропускает тяжёлые функции (feature extraction, GED,
# и так далее) и возвращает готовый pair_row с verdict="likely_clone_by_signature".
# Финальный shortcut_status="success_shortcut" выставляется именно здесь,
# после реального применения сокращённого пути.
SHORTCUT_REASON_HIGH_CONFIDENCE = "high_confidence_signature_match"
SHORTCUT_STATUS_SUCCESS = "success_shortcut"
DEEP_VERIFICATION_STATUS_SKIPPED = "skipped_shortcut"
SHORTCUT_VERDICT_LIKELY_CLONE = "likely_clone_by_signature"
SEMANTIC_MULTIVIEW_ENABLED_ENV = "ANDROID_SIM_SEMANTIC_MULTIVIEW"
DEEP_M2_SCORE_DECISION_POLICY_ID = "deep_m2_score_decision_policy_v1"
DEEP_M2_SCORE_DECISION_LIMITATIONS = (
    "full_similarity_score_is_diagnostic_not_final_verdict",
    "library_reduced_score_requires_real_computation",
    "packaging_and_signature_are_evidence_only",
    "analysis_failed_similarity_is_undefined_not_zero",
)
CODE_STATS_CONTAINMENT_POLICY_ID = "R_code_stats_containment_corroboration_policy_v1"
CODE_STATS_CONTAINMENT_SCORE_SOURCE = "code_stats_containment_with_resource_corroboration"
CODE_STATS_CONTAINMENT_THRESHOLD = 0.95
CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD = 0.80
CODE_STATS_RESOURCE_CORROBORATION_SIGNAL = "resource_path_digest"
CODE_STATS_ADDED_CODE_POLICY_ID = "R_code_stats_added_code_corroboration_policy_v1"
CODE_STATS_ADDED_CODE_SCORE_SOURCE = "code_stats_added_code_with_resource_support"
CODE_STATS_ADDED_CODE_PRESERVED_CORE_THRESHOLD = 0.80
CODE_STATS_ADDED_CODE_DELTA_THRESHOLD = 0.30
CODE_STATS_ADDED_CODE_RESOURCE_SUPPORT_THRESHOLD = 0.75
CODE_STATS_ADDED_CODE_MIN_PRESERVED_METHODS = 50
CODE_STATS_ADDED_CODE_RESOURCE_SIGNAL = "resource_path_digest"
CODE_STATS_RESOURCE_CHANGE_IDENTITY_POLICY_ID = (
    "R_code_stats_resource_change_identity_policy_v1"
)
CODE_STATS_RESOURCE_CHANGE_IDENTITY_SCORE_SOURCE = (
    "code_stats_resource_change_tolerant_code_identity"
)
CODE_STATS_RESOURCE_CHANGE_IDENTITY_THRESHOLD = 0.99
CODE_STATS_RESOURCE_CHANGE_IDENTITY_MAX_ADDED_DELTA = 0.05
CODE_STATS_RESOURCE_CHANGE_IDENTITY_MIN_METHODS = 10
CODE_STATS_REPACK_CORE_POLICY_ID = "R_code_stats_repack_core_policy_v1"
CODE_STATS_REPACK_CORE_SCORE_SOURCE = (
    "code_stats_repack_core_with_bounded_code_delta"
)
CODE_STATS_REPACK_CORE_THRESHOLD = 0.85
CODE_STATS_REPACK_CORE_MAX_ADDED_DELTA = 0.30
CODE_STATS_REPACK_CORE_MIN_METHODS = 2000
CODE_STATS_PAYLOAD_RESOURCE_POLICY_ID = (
    "R_code_stats_payload_resource_support_policy_v1"
)
CODE_STATS_PAYLOAD_RESOURCE_SCORE_SOURCE = "code_stats_payload_resource_support"
CODE_STATS_PAYLOAD_RESOURCE_CODE_THRESHOLD = 0.70
CODE_STATS_PAYLOAD_RESOURCE_SUPPORT_THRESHOLD = 0.55
CODE_STATS_PAYLOAD_RESOURCE_MIN_METHODS = 50
CODE_STATS_PAYLOAD_RESOURCE_MAX_ADDED_DELTA = 0.90
CODE_STATS_PAYLOAD_RESOURCE_SCORE_THRESHOLD = 0.70
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_POLICY_ID = (
    "R_code_stats_payload_resource_bridge_policy_v1"
)
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SCORE_SOURCE = (
    "code_stats_payload_resource_bridge"
)
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_CODE_THRESHOLD = 0.45
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SUPPORT_THRESHOLD = 0.90
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_FP_COUNTER_THRESHOLD = 0.55
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_METHODS = 50
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_ADDED_DELTA = 0.30
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MAX_ADDED_DELTA = 0.90
CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SCORE_THRESHOLD = 0.70
FRAMEWORK_SHIFT_EVIDENCE_POLICY_ID = "R_framework_shift_anchor_evidence_policy_v1"
FRAMEWORK_SHIFT_EVIDENCE_REF = "R_framework_shift_anchors"
C05_STATIC_EVIDENCE_POLICY_ID = "R_c05_static_evidence_policy_v1"
C05_STATIC_EVIDENCE_REF = "R_c05_static_evidence"
C05_STATIC_MIN_RELATION_SCORE = 0.10
C05_STATIC_SAMPLE_LIMIT = 20
C05_STATIC_SCORE_POLICY_ID = "R_c05_static_manifest_relation_high_score_policy_v1"
C05_STATIC_SCORE_SOURCE = "c05_static_manifest_relation_high_score"
C05_STATIC_SCORE_REF = "R_c05_static_manifest_relation_high_score"
C05_STATIC_SCORE_EVIDENCE_THRESHOLD = 0.70
C05_STATIC_SCORE_RELATION_THRESHOLD = 0.70
C05_STATIC_NAMESPACE_STOPWORDS = {
    "android",
    "androidx",
    "dalvik",
    "java",
    "javax",
    "kotlin",
    "kotlinx",
}
C05_STATIC_NAMESPACE_ROOT_RE = (
    r"(android|androidx|dalvik|java|javax|kotlin|kotlinx|com|org|net|io|ru|cn)"
)
FRAMEWORK_SHIFT_MIN_ANCHOR_CONTAINMENT = 0.10
FRAMEWORK_SHIFT_MIN_COMMON_ANCHORS = 50
FRAMEWORK_SHIFT_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".properties",
    ".txt",
    ".xml",
}
FRAMEWORK_SHIFT_STOPWORDS = {
    "action",
    "activity",
    "android",
    "application",
    "button",
    "category",
    "class",
    "color",
    "content",
    "drawable",
    "false",
    "final",
    "function",
    "height",
    "html",
    "icon",
    "image",
    "intent",
    "java",
    "layout",
    "main",
    "match",
    "name",
    "null",
    "object",
    "parent",
    "private",
    "protected",
    "provider",
    "public",
    "receiver",
    "return",
    "service",
    "static",
    "string",
    "style",
    "super",
    "theme",
    "true",
    "view",
    "void",
    "width",
    "wrap",
}
FRAMEWORK_SHIFT_NOISE_PREFIXES = (
    "android.",
    "androidx.",
    "com.google.",
    "java.",
    "javax.",
    "org.apache.",
)
CODE_CONFLICT_GUARD_POLICY_ID = "score_conflict_guard_zero_code_fingerprint_v1"
CODE_CONFLICT_GUARD_SCORE_SOURCE = "code_conflict_guarded_library_reduced_score"
CODE_CONFLICT_GUARD_REVIEW_THRESHOLD = 0.30
CODE_CONFLICT_GUARD_HIGH_THRESHOLD = 0.70
CODE_CONFLICT_GUARD_REVIEW_CAP = 0.29
CODE_CONFLICT_GUARD_REASON = (
    "zero_code_fingerprint_overlap_for_library_reduced_review_score"
)
SEMANTIC_MULTIVIEW_SCORE_POLICY_ID = (
    "R_semantic_multiview_score_promotion_policy_v1"
)
SEMANTIC_MULTIVIEW_SCORE_SOURCE = (
    "semantic_multiview_high_same_resources_code_stats_match"
)
SEMANTIC_MULTIVIEW_PROMOTION_REF = "R_semantic_multiview_score_promotion"
SEMANTIC_MULTIVIEW_PROMOTION_RELATION = "same_resources_code_stats_match"
SEMANTIC_MULTIVIEW_CODE_STATS_THRESHOLD = 0.70
SEMANTIC_MULTIVIEW_RESOURCE_IDENTITY_THRESHOLD = 0.70
SEMANTIC_MULTIVIEW_RESOURCE_STRUCTURE_THRESHOLD = 0.60
SCORE_DECISION_PRIORITY_CORE = 10
SCORE_DECISION_PRIORITY_P2 = 20
SCORE_DECISION_PRIORITY_P0 = 30
SCORE_DECISION_PRIORITY_P0_GUARD = 40
SCORE_DECISION_PRIORITY_ORDER = "P0_guard > P0 > P2 > core"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FEATURE_CACHE_PATH = PROJECT_ROOT / "experiments" / "artifacts" / ".feature_cache.sqlite"
FEATURE_CACHE_VERSION = "v1"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_parallel_executor(max_workers: int):
    """Create a process pool when allowed, else fall back to threads.

    In restricted sandboxes `ProcessPoolExecutor` can fail during startup with
    `PermissionError`/`NotImplementedError` on semaphore limits. For the test
    harness this is not a functional difference: workers stay isolated enough
    for contract verification, and timeout/order semantics remain the same.
    """
    try:
        return ProcessPoolExecutor(max_workers=max_workers)
    except (OSError, PermissionError, NotImplementedError):
        return ThreadPoolExecutor(max_workers=max_workers)

try:
    from script.system_requirements import verify_required_dependencies
    from script.screening_runner import M_STATIC_LAYERS
    from script.screening_runner import containment_similarity
    from script.screening_runner import cosine_similarity
    from script.screening_runner import dice_similarity
    from script.screening_runner import extract_layers_from_apk
    from script.screening_runner import jaccard_similarity
    from script.screening_runner import normalize_metric_name
    from script.screening_runner import overlap_similarity
    from script.screening_runner import shared_count_similarity
except Exception:
    from system_requirements import verify_required_dependencies
    from screening_runner import M_STATIC_LAYERS
    from screening_runner import containment_similarity
    from screening_runner import cosine_similarity
    from screening_runner import dice_similarity
    from screening_runner import extract_layers_from_apk
    from screening_runner import jaccard_similarity
    from screening_runner import normalize_metric_name
    from screening_runner import overlap_similarity
    from screening_runner import shared_count_similarity

try:
    from script.m_static_views import extract_all_features
    from script import m_static_views
except Exception:
    try:
        from m_static_views import extract_all_features
        import m_static_views  # type: ignore[no-redef]
    except Exception:
        extract_all_features = None
        m_static_views = None  # type: ignore[assignment]

try:
    from script.signing_view import compare_signatures
    from script.signing_view import extract_apk_signature_hash
except Exception:
    try:
        from signing_view import compare_signatures
        from signing_view import extract_apk_signature_hash
    except Exception:
        compare_signatures = None
        extract_apk_signature_hash = None

try:
    from script.evidence_formatter import collect_evidence_from_pairwise
except Exception:
    from evidence_formatter import collect_evidence_from_pairwise  # type: ignore[no-redef]

try:
    from script.added_code_direct_evidence import (
        ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID,
        build_added_code_direct_evidence_fields,
    )
except Exception:
    try:
        from added_code_direct_evidence import (  # type: ignore[no-redef]
            ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID,
            build_added_code_direct_evidence_fields,
        )
    except Exception:
        ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID = (  # type: ignore[assignment]
            "R_added_code_direct_evidence_policy_v1"
        )
        build_added_code_direct_evidence_fields = None  # type: ignore[assignment]

try:
    from script.deleted_code_direct_evidence import (
        DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID,
        build_deleted_code_direct_evidence_fields,
    )
except Exception:
    try:
        from deleted_code_direct_evidence import (  # type: ignore[no-redef]
            DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID,
            build_deleted_code_direct_evidence_fields,
        )
    except Exception:
        DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID = (  # type: ignore[assignment]
            "R_deleted_code_direct_evidence_policy_v1"
        )
        build_deleted_code_direct_evidence_fields = None  # type: ignore[assignment]

try:
    from script.code_core_evidence import (
        CODE_CORE_EVIDENCE_POLICY_ID,
        build_code_core_evidence_fields,
    )
except Exception:
    try:
        from code_core_evidence import (  # type: ignore[no-redef]
            CODE_CORE_EVIDENCE_POLICY_ID,
            build_code_core_evidence_fields,
        )
    except Exception:
        CODE_CORE_EVIDENCE_POLICY_ID = "R_code_core_evidence_policy_v1"  # type: ignore[assignment]
        build_code_core_evidence_fields = None  # type: ignore[assignment]

try:
    from script.packaging_evidence import build_packaging_evidence_fields
except Exception:
    try:
        from packaging_evidence import build_packaging_evidence_fields  # type: ignore[no-redef]
    except Exception:
        build_packaging_evidence_fields = None  # type: ignore[assignment]

try:
    from script.v3_4_contracts import build_pair_aggregation_policy
    from script.v3_4_contracts import build_pair_check_run
    from script.v3_4_contracts import build_pair_similarity_result
except Exception:
    from v3_4_contracts import build_pair_aggregation_policy  # type: ignore[no-redef]
    from v3_4_contracts import build_pair_check_run  # type: ignore[no-redef]
    from v3_4_contracts import build_pair_similarity_result  # type: ignore[no-redef]

try:
    from script.semantic_multiview import (
        PROFILE_ID as SEMANTIC_MULTIVIEW_PROFILE_ID,
        run_semantic_multiview_check,
    )
except Exception:
    try:
        from semantic_multiview import (  # type: ignore[no-redef]
            PROFILE_ID as SEMANTIC_MULTIVIEW_PROFILE_ID,
            run_semantic_multiview_check,
        )
    except Exception:
        SEMANTIC_MULTIVIEW_PROFILE_ID = "R_semantic_multiview_decision_policy_v0"
        run_semantic_multiview_check = None  # type: ignore[assignment]

try:
    from script.timeout_incident_registry import record_timeout_incident
except Exception:
    try:
        from timeout_incident_registry import record_timeout_incident  # type: ignore[no-redef]
    except Exception:
        record_timeout_incident = None  # type: ignore[assignment]

try:
    from script.feature_cache_sqlite import FeatureCacheSqlite
except Exception:
    try:
        from feature_cache_sqlite import FeatureCacheSqlite  # type: ignore[no-redef]
    except Exception:
        FeatureCacheSqlite = None  # type: ignore[assignment]


def collect_signature_match(apk_a: str | None, apk_b: str | None) -> dict:
    """Compute signature match signal between two APK paths.

    Returns a dict with keys `score` and `status` (match/mismatch/missing)
    using compare_signatures from signing_view. If the dependency is
    unavailable or either apk_path is missing, returns a safe default.
    """
    if compare_signatures is None or extract_apk_signature_hash is None:
        return {"score": 0.0, "status": "missing"}
    if not apk_a or not apk_b:
        return {"score": 0.0, "status": "missing"}
    try:
        hash_a = extract_apk_signature_hash(Path(apk_a))
        hash_b = extract_apk_signature_hash(Path(apk_b))
    except Exception:
        return {"score": 0.0, "status": "missing"}
    return compare_signatures(hash_a, hash_b)

try:
    from script.shared_data_store import discover_apk_by_stem
    from script.shared_data_store import discover_decoded_dir_by_stem
    from script.shared_data_store import resolve_path_ref
except Exception:
    from shared_data_store import discover_apk_by_stem  # type: ignore[no-redef]
    from shared_data_store import discover_decoded_dir_by_stem  # type: ignore[no-redef]
    from shared_data_store import resolve_path_ref  # type: ignore[no-redef]


APP_PATH_KEYS = (
    "apk_path",
    "apk",
    "path",
    "app_path",
    "artifact_path",
)
A_SIDE_CANDIDATE_APK_KEYS = (
    "app_a_apk_path",
    "apk_a_path",
    "apk_1",
    "query_apk_path",
    "query_app_apk_path",
    "app_a_path",
)
B_SIDE_CANDIDATE_APK_KEYS = (
    "app_b_apk_path",
    "apk_b_path",
    "apk_2",
    "candidate_apk_path",
    "candidate_app_apk_path",
    "app_b_path",
)
APP_DECODED_DIR_KEYS = (
    "decoded_dir",
    "decoded_apk_dir",
    "unpacked_dir",
    "apk_decoded_dir",
)
A_SIDE_CANDIDATE_DECODED_KEYS = (
    "app_a_decoded_dir",
    "decoded_dir_a",
    "query_decoded_dir",
    "query_app_decoded_dir",
)
B_SIDE_CANDIDATE_DECODED_KEYS = (
    "app_b_decoded_dir",
    "decoded_dir_b",
    "candidate_decoded_dir",
    "candidate_app_decoded_dir",
)
DECODE_REQUIRED_LAYERS = {"component", "resource", "library"}

SUPPORTED_METRICS = {
    "jaccard",
    "cosine",
    "containment",
    "dice",
    "overlap",
    "shared_count",
    "levenshtein",
    "edit_distance",
    "ged",
    "hybrid",
}


class PairwiseAnalysisError(RuntimeError):
    pass


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _sha256_of_file(apk_path: Path) -> str:
    hasher = hashlib.sha256()
    with apk_path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_feature_cache_path(
    feature_cache_path: str | os.PathLike[str] | None = None,
) -> Path:
    raw = feature_cache_path
    if raw is None:
        raw = os.environ.get("FEATURE_CACHE_PATH") or DEFAULT_FEATURE_CACHE_PATH
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@contextmanager
def _feature_cache_path_override(
    feature_cache_path: str | os.PathLike[str] | None = None,
):
    resolved_path = _resolve_feature_cache_path(feature_cache_path)
    previous = os.environ.get("FEATURE_CACHE_PATH")
    os.environ["FEATURE_CACHE_PATH"] = str(resolved_path)
    try:
        yield resolved_path
    finally:
        if previous is None:
            os.environ.pop("FEATURE_CACHE_PATH", None)
        else:
            os.environ["FEATURE_CACHE_PATH"] = previous


def _open_feature_cache(
    feature_cache_path: str | os.PathLike[str] | None = None,
) -> Any | None:
    if FeatureCacheSqlite is None:
        return None
    return FeatureCacheSqlite(_resolve_feature_cache_path(feature_cache_path))


def _get_cached_feature_bundle(
    apk_path: str | os.PathLike[str] | None,
    feature_cache: Any | None,
) -> dict[str, Any] | None:
    if feature_cache is None or apk_path is None:
        return None
    try:
        apk_file = Path(apk_path)
        if not apk_file.is_file():
            return None
        apk_sha256 = _sha256_of_file(apk_file)
        feature_bundle = feature_cache.get(apk_sha256, FEATURE_CACHE_VERSION)
    except Exception:
        return None
    return feature_bundle if isinstance(feature_bundle, dict) else None


@contextmanager
def _process_pool_sysconf_workaround():
    """Sandbox workaround: some hosts deny os.sysconf(SC_SEM_NSEMS_MAX)."""
    try:
        import concurrent.futures.process as _process_mod
    except Exception:
        yield
        return

    original_sysconf = _process_mod.os.sysconf

    def safe_sysconf(name: str):
        if name == "SC_SEM_NSEMS_MAX":
            try:
                return original_sysconf(name)
            except PermissionError:
                return 256
        return original_sysconf(name)

    _process_mod.os.sysconf = safe_sysconf
    try:
        yield
    finally:
        _process_mod.os.sysconf = original_sysconf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pairwise_runner.py",
        description=(
            "Runs pairwise stage using cascade-config and enriched candidates from deepening. "
            "Returns [{app_a, app_b, full_similarity_score, library_reduced_score, status, views_used}]."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to cascade-config YAML/JSON.")
    parser.add_argument(
        "--enriched",
        required=True,
        help="Path to enriched_candidates JSON produced by deepening_runner.",
    )
    parser.add_argument("--output", required=True, help="Path to output JSON.")
    parser.add_argument(
        "--detailed-output",
        required=False,
        default=None,
        help=(
            "Optional path for DEEP-004 detailed JSON report (schema_version "
            "{!r}). Written in addition to --output.".format(DETAILED_JSON_SCHEMA_VERSION)
        ),
    )
    parser.add_argument("--ins-block-sim-threshold", type=float, default=0.80)
    parser.add_argument("--ged-timeout-sec", type=int, default=30)
    parser.add_argument("--processes-count", type=int, default=1)
    parser.add_argument("--threads-count", type=int, default=2)
    parser.add_argument(
        "--feature-cache-path",
        required=False,
        default=None,
        help=(
            "Optional SQLite file for shared worker cache. "
            "Defaults to FEATURE_CACHE_PATH or experiments/artifacts/.feature_cache.sqlite."
        ),
    )
    return parser.parse_args()


def split_top_level_commas(raw: str) -> list[str]:
    parts = []
    chunk = []
    bracket_depth = 0
    brace_depth = 0
    in_single_quote = False
    in_double_quote = False

    for char in raw:
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            chunk.append(char)
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            chunk.append(char)
            continue
        if not in_single_quote and not in_double_quote:
            if char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth -= 1
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
            elif char == "," and bracket_depth == 0 and brace_depth == 0:
                parts.append("".join(chunk).strip())
                chunk = []
                continue
        chunk.append(char)

    tail = "".join(chunk).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_yaml_scalar(token: str) -> Any:
    token = token.strip()
    if token == "[]":
        return []
    if token == "{}":
        return {}

    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        if not inner:
            return []
        return [parse_yaml_scalar(item) for item in split_top_level_commas(inner)]

    if token.startswith("{") and token.endswith("}"):
        inner = token[1:-1].strip()
        if not inner:
            return {}
        result = {}
        for item in split_top_level_commas(inner):
            if ":" not in item:
                raise ValueError("Invalid inline map entry: {!r}".format(item))
            key, value = item.split(":", 1)
            result[key.strip()] = parse_yaml_scalar(value.strip())
        return result

    if (token.startswith("'") and token.endswith("'")) or (
        token.startswith('"') and token.endswith('"')
    ):
        return token[1:-1]

    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None

    if re.fullmatch(r"-?[0-9]+", token):
        return int(token)
    if re.fullmatch(r"-?(?:[0-9]*\.[0-9]+|[0-9]+\.[0-9]*)(?:[eE]-?[0-9]+)?", token):
        return float(token)

    return token


def strip_inline_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    chars = []

    for char in line:
        if char == "\\" and in_double_quote:
            escaped = not escaped
            chars.append(char)
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            chars.append(char)
            continue

        if char == '"' and not in_single_quote and not escaped:
            in_double_quote = not in_double_quote
            chars.append(char)
            continue

        if char == "#" and not in_single_quote and not in_double_quote:
            break

        escaped = False
        chars.append(char)

    return "".join(chars).rstrip()


def parse_simple_yaml(raw: str) -> Any:
    lines = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        without_comment = strip_inline_comment(line)
        if not without_comment.strip():
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        lines.append((indent, without_comment.strip(), line_number))

    if not lines:
        return {}

    def parse_block(index: int, expected_indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            raise ValueError("Unexpected end of YAML input.")

        indent, text, line_number = lines[index]
        if indent != expected_indent:
            raise ValueError(
                "Invalid indentation at line {}: expected {}, got {}.".format(
                    line_number, expected_indent, indent
                )
            )

        if text.startswith("- "):
            return parse_sequence(index, expected_indent)
        return parse_mapping(index, expected_indent)

    def parse_mapping(index: int, expected_indent: int) -> tuple[dict[str, Any], int]:
        mapping: dict[str, Any] = {}

        while index < len(lines):
            indent, text, line_number = lines[index]
            if indent < expected_indent:
                break
            if indent > expected_indent:
                raise ValueError("Unexpected indentation at line {}.".format(line_number))
            if text.startswith("- "):
                break
            if ":" not in text:
                raise ValueError("Expected mapping entry at line {}.".format(line_number))

            key, remainder = text.split(":", 1)
            key = key.strip()
            remainder = remainder.strip()
            index += 1

            if remainder:
                mapping[key] = parse_yaml_scalar(remainder)
                continue

            if index < len(lines) and lines[index][0] > expected_indent:
                nested_indent = lines[index][0]
                nested_value, index = parse_block(index, nested_indent)
                mapping[key] = nested_value
            else:
                mapping[key] = None

        return mapping, index

    def parse_sequence(index: int, expected_indent: int) -> tuple[list[Any], int]:
        sequence = []

        while index < len(lines):
            indent, text, line_number = lines[index]
            if indent < expected_indent:
                break
            if indent > expected_indent:
                raise ValueError("Unexpected indentation at line {}.".format(line_number))
            if not text.startswith("- "):
                break

            remainder = text[2:].strip()
            index += 1

            if not remainder:
                if index < len(lines) and lines[index][0] > expected_indent:
                    nested_indent = lines[index][0]
                    item, index = parse_block(index, nested_indent)
                else:
                    item = None
                sequence.append(item)
                continue

            if ":" in remainder:
                key, value_text = remainder.split(":", 1)
                key = key.strip()
                value_text = value_text.strip()
                item: Any = {}

                if value_text:
                    item[key] = parse_yaml_scalar(value_text)
                elif index < len(lines) and lines[index][0] > expected_indent:
                    nested_indent = lines[index][0]
                    nested_value, index = parse_block(index, nested_indent)
                    item[key] = nested_value
                else:
                    item[key] = None

                if index < len(lines) and lines[index][0] > expected_indent:
                    nested_indent = lines[index][0]
                    extra_mapping, index = parse_block(index, nested_indent)
                    if not isinstance(extra_mapping, dict):
                        raise ValueError(
                            "List item at line {} must continue as mapping.".format(line_number)
                        )
                    item.update(extra_mapping)

                sequence.append(item)
                continue

            sequence.append(parse_yaml_scalar(remainder))

        return sequence, index

    root_indent = lines[0][0]
    root_value, next_index = parse_block(0, root_indent)
    if next_index != len(lines):
        raise ValueError("Unexpected trailing YAML content.")
    return root_value


def load_config(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(raw)
    except ModuleNotFoundError:
        if raw.lstrip().startswith("{"):
            payload = json.loads(raw)
        else:
            payload = parse_simple_yaml(raw)

    if not isinstance(payload, dict):
        raise ValueError("Config must be a mapping at top level.")
    return payload


def looks_like_pair(item: dict[str, Any]) -> bool:
    direct = ("app_a" in item and "app_b" in item) or (
        "query_app_id" in item and "candidate_app_id" in item
    )
    if direct:
        return True
    apps = item.get("apps")
    if isinstance(apps, dict):
        return ("app_a" in apps and "app_b" in apps) or (
            "query_app" in apps and "candidate_app" in apps
        )
    return False


def ensure_enriched_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = None
        for key in ("enriched_candidates", "candidate_list", "candidates", "short_list", "shortlist", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and looks_like_pair(payload):
            items = [payload]
        if items is None:
            raise ValueError("Could not find enriched candidate list in provided JSON.")
    else:
        raise ValueError("Enriched JSON must be an object or array.")

    result = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Enriched candidate at index {} must be an object.".format(index))
        result.append(item)
    return result


def load_enriched_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ensure_enriched_items(payload)


def collect_stage_features(stage: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen = set()

    def add(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if not isinstance(value, str):
                continue
            normalized = value.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)

    add(stage.get("features"))
    views = stage.get("views")
    if isinstance(views, list):
        for view in views:
            if isinstance(view, dict):
                add(view.get("features"))
    return ordered


def parse_pairwise_stage(config: dict[str, Any]) -> tuple[list[str], str, float]:
    stages = config.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("Config field stages must be an object.")
    pairwise = stages.get("pairwise")
    if not isinstance(pairwise, dict):
        raise ValueError("Config field stages.pairwise must be an object.")

    features = collect_stage_features(pairwise)
    if not features:
        raise ValueError("Config field stages.pairwise.features must be a non-empty list.")

    supported_layers = set(M_STATIC_LAYERS)
    for layer in features:
        if layer not in supported_layers:
            raise ValueError("Unsupported layer in stages.pairwise.features: {!r}".format(layer))

    metric_raw = pairwise.get("metric")
    if not isinstance(metric_raw, str) or not metric_raw.strip():
        raise ValueError("Config field stages.pairwise.metric must be a non-empty string.")
    metric = normalize_metric_name(metric_raw)
    if metric not in SUPPORTED_METRICS:
        raise ValueError("Unsupported pairwise metric: {!r}".format(metric_raw))

    threshold_raw = pairwise.get("threshold")
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        raise ValueError("Config field stages.pairwise.threshold must be numeric.") from None

    return features, metric, threshold


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def extract_apps(candidate: dict[str, Any]) -> tuple[Any, Any]:
    if "app_a" in candidate and "app_b" in candidate:
        return candidate["app_a"], candidate["app_b"]

    apps = candidate.get("apps")
    if isinstance(apps, dict):
        if "app_a" in apps and "app_b" in apps:
            return apps["app_a"], apps["app_b"]
        if "query_app" in apps and "candidate_app" in apps:
            return apps["query_app"], apps["candidate_app"]

    app_a = first_present(candidate, ("query_app", "query_app_id", "apk_1", "app_1"))
    app_b = first_present(candidate, ("candidate_app", "candidate_app_id", "apk_2", "app_2"))
    if app_a is None or app_b is None:
        raise ValueError("Candidate pair must contain app_a/app_b or query/candidate fields.")
    return app_a, app_b


def extract_path_from_app(app: Any) -> str | None:
    if isinstance(app, dict):
        for key in APP_PATH_KEYS:
            value = app.get(key)
            if isinstance(value, str) and value:
                return resolve_path_ref(value)
    if isinstance(app, str) and app:
        resolved = resolve_path_ref(app)
        if resolved is None:
            return None
        path = Path(resolved)
        if path.is_file() and path.suffix.lower() == ".apk":
            return resolved
    return None


def resolve_app_label(app: Any, fallback: str) -> str:
    if isinstance(app, dict):
        for key in ("app_id", "id", "name", "query_app_id", "candidate_app_id"):
            value = app.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        app_path = extract_path_from_app(app)
        if app_path:
            return Path(app_path).stem
    if isinstance(app, str) and app.strip():
        value = app.strip()
        if value.lower().endswith(".apk"):
            resolved = resolve_path_ref(value) or value
            return Path(resolved).stem
        return value
    return fallback


def discover_apk_path_by_app_label(app_label: str, cache: dict[str, str | None]) -> str | None:
    if app_label in cache:
        return cache[app_label]

    discovered = None
    apk_root = PROJECT_ROOT / "apk"
    if apk_root.is_dir():
        candidates = sorted(apk_root.rglob("*.apk"))
        for apk_path in candidates:
            if apk_path.stem == app_label:
                discovered = str(apk_path.resolve())
                break
    if discovered is None:
        discovered = discover_apk_by_stem(app_label)
    cache[app_label] = discovered
    return discovered


def resolve_apk_path(
    candidate: dict[str, Any],
    app: Any,
    side: str,
    app_label: str,
    discovery_cache: dict[str, str | None],
) -> str | None:
    path = extract_path_from_app(app)
    if path is not None:
        return path

    keys = A_SIDE_CANDIDATE_APK_KEYS if side == "a" else B_SIDE_CANDIDATE_APK_KEYS
    value = first_present(candidate, keys)
    if isinstance(value, str) and value:
        return resolve_path_ref(value) or value

    apps = candidate.get("apps")
    if isinstance(apps, dict):
        app_key = "app_a" if side == "a" else "app_b"
        nested = apps.get(app_key)
        path = extract_path_from_app(nested)
        if path is not None:
            return path

        fallback_key = "query_app" if side == "a" else "candidate_app"
        path = extract_path_from_app(apps.get(fallback_key))
        if path is not None:
            return path

    discovered = discover_apk_path_by_app_label(app_label, discovery_cache)
    if discovered:
        return discovered
    return None


def aggregate_features(layers: dict[str, set[str]], selected_layers: list[str]) -> set[str]:
    aggregated = set()
    for layer in selected_layers:
        for feature in layers.get(layer, set()):
            aggregated.add("{}:{}".format(layer, feature))
    return aggregated


def extract_decoded_dir_from_app(app: Any) -> str | None:
    if not isinstance(app, dict):
        return None
    for key in APP_DECODED_DIR_KEYS:
        value = app.get(key)
        if isinstance(value, str) and value:
            return resolve_path_ref(value)
    return None


def resolve_decoded_dir(candidate: dict[str, Any], app: Any, side: str) -> str | None:
    decoded_dir = extract_decoded_dir_from_app(app)
    if decoded_dir is not None:
        return decoded_dir

    keys = A_SIDE_CANDIDATE_DECODED_KEYS if side == "a" else B_SIDE_CANDIDATE_DECODED_KEYS
    value = first_present(candidate, keys)
    if isinstance(value, str) and value:
        return resolve_path_ref(value) or value

    apps = candidate.get("apps")
    if isinstance(apps, dict):
        app_key = "app_a" if side == "a" else "app_b"
        nested = apps.get(app_key)
        decoded_dir = extract_decoded_dir_from_app(nested)
        if decoded_dir is not None:
            return decoded_dir

        fallback_key = "query_app" if side == "a" else "candidate_app"
        decoded_dir = extract_decoded_dir_from_app(apps.get(fallback_key))
        if decoded_dir is not None:
            return decoded_dir

    app_label = resolve_app_label(app, "")
    if app_label:
        return discover_decoded_dir_by_stem(app_label)

    return None


def levenshtein_distance(left: list[str], right: list[str]) -> int:
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, token_left in enumerate(left, start=1):
        current = [i]
        for j, token_right in enumerate(right, start=1):
            deletion = previous[j] + 1
            insertion = current[j - 1] + 1
            substitution = previous[j - 1] + (0 if token_left == token_right else 1)
            current.append(min(deletion, insertion, substitution))
        previous = current
    return previous[-1]


def levenshtein_similarity(left: set[str], right: set[str]) -> float:
    left_seq = sorted(left)
    right_seq = sorted(right)
    maximum = max(len(left_seq), len(right_seq))
    if maximum == 0:
        return 0.0
    distance = levenshtein_distance(left_seq, right_seq)
    return max(0.0, 1.0 - (distance / maximum))


def calculate_set_metric(metric: str, left: set[str], right: set[str]) -> float:
    if metric == "jaccard":
        return float(jaccard_similarity(left, right))
    if metric == "cosine":
        return float(cosine_similarity(left, right))
    if metric == "containment":
        return float(containment_similarity(left, right))
    if metric == "dice":
        return float(dice_similarity(left, right))
    if metric == "overlap":
        return float(overlap_similarity(left, right))
    if metric == "shared_count":
        return float(shared_count_similarity(left, right))
    if metric in {"levenshtein", "edit_distance"}:
        return float(levenshtein_similarity(left, right))
    raise PairwiseAnalysisError("Unsupported set metric: {!r}".format(metric))


def stringify_tokens(tokens: set[Any]) -> set[str]:
    return {str(token) for token in tokens}


def normalize_pairwise_layer_tokens(layer_name: str, raw_features: Any) -> set[str]:
    """Normalize quick/enhanced feature bundles to flat string tokens."""
    if layer_name == "component" and isinstance(raw_features, dict):
        return flatten_component_features(raw_features)
    if layer_name == "resource" and isinstance(raw_features, dict):
        return flatten_resource_features(raw_features)
    if layer_name == "library" and isinstance(raw_features, dict):
        return flatten_library_features(raw_features)
    if isinstance(raw_features, (set, list, tuple)):
        return {str(token) for token in raw_features}
    return set()


def flatten_component_features(features: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for component_type in ("activities", "services", "receivers", "providers"):
        for component in features.get(component_type, []):
            if not isinstance(component, dict):
                continue
            name = component.get("name")
            if isinstance(name, str) and name:
                tokens.add("{}:{}".format(component_type, name))

    for permission in features.get("permissions", set()):
        tokens.add("permission:{}".format(permission))
    for feature_name in features.get("features", set()):
        tokens.add("feature:{}".format(feature_name))
    return tokens


def flatten_resource_features(features: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for item in features.get("resource_digests", set()):
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        rel_path, digest = item
        tokens.add("{}:{}".format(rel_path, digest))
    return tokens


def flatten_library_features(features: dict[str, Any]) -> set[str]:
    libraries = features.get("libraries", {})
    if not isinstance(libraries, dict):
        return set()
    return {"lib:{}".format(lib_id) for lib_id in libraries}


def flatten_code_fingerprint_features(features: Any) -> set[str]:
    if not isinstance(features, dict):
        return set()
    fingerprints = features.get("method_fingerprints")
    if not isinstance(fingerprints, dict):
        return set()
    tokens: set[str] = set()
    for method_id, fingerprint in fingerprints.items():
        if not method_id or not fingerprint:
            continue
        tokens.add("method_fp:{}:{}".format(method_id, fingerprint))
    return tokens


def flatten_code_fingerprint_values(features: Any) -> list[str]:
    if not isinstance(features, dict):
        return []
    fingerprints = features.get("method_fingerprints")
    if not isinstance(fingerprints, dict):
        return []
    values: list[str] = []
    for fingerprint in fingerprints.values():
        if fingerprint:
            values.append(str(fingerprint))
    return values


def flatten_code_method_fingerprints(features: Any) -> dict[str, str]:
    if not isinstance(features, dict):
        return {}
    fingerprints = features.get("method_fingerprints")
    if not isinstance(fingerprints, dict):
        return {}
    return {
        str(method_id): str(fingerprint)
        for method_id, fingerprint in fingerprints.items()
        if method_id and fingerprint
    }


def _framework_shift_default_fields(error: str | None = None) -> dict[str, Any]:
    return {
        "framework_shift_evidence_policy_id": FRAMEWORK_SHIFT_EVIDENCE_POLICY_ID,
        "framework_shift_evidence_applied": False,
        "framework_shift_evidence_role": "evidence_only",
        "framework_shift_left_package": "",
        "framework_shift_right_package": "",
        "framework_shift_package_equal": False,
        "framework_shift_left_hybrid_hint": False,
        "framework_shift_right_hybrid_hint": False,
        "framework_shift_hybrid_to_native": False,
        "framework_shift_left_layout_count": 0,
        "framework_shift_right_layout_count": 0,
        "framework_shift_left_anchor_count": 0,
        "framework_shift_right_anchor_count": 0,
        "framework_shift_common_anchor_count": 0,
        "framework_shift_anchor_containment": 0.0,
        "framework_shift_anchor_jaccard": 0.0,
        "framework_shift_common_anchor_sample": [],
        "framework_shift_min_anchor_containment": (
            FRAMEWORK_SHIFT_MIN_ANCHOR_CONTAINMENT
        ),
        "framework_shift_min_common_anchors": FRAMEWORK_SHIFT_MIN_COMMON_ANCHORS,
        "framework_shift_error": error,
    }


def _framework_shift_safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _framework_shift_is_useful_token(token: str) -> bool:
    if len(token) < 4 or token in FRAMEWORK_SHIFT_STOPWORDS:
        return False
    return not token.startswith(FRAMEWORK_SHIFT_NOISE_PREFIXES)


def _framework_shift_tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    pattern = re.compile(r"https?://([^/\s\"']+)|[A-Za-z][A-Za-z0-9_\-.]{3,}")
    for match in pattern.finditer(text):
        token = (match.group(1) or match.group(0)).lower().strip("._-")
        if _framework_shift_is_useful_token(token):
            tokens.add(token)
        for part in re.split(r"[._\-]+", token):
            if _framework_shift_is_useful_token(part):
                tokens.add(part)
    return tokens


def _framework_shift_package_name(decoded_dir: Path) -> str:
    manifest = decoded_dir / "AndroidManifest.xml"
    match = re.search(
        r'package="([^"]+)"',
        _framework_shift_safe_read_text(manifest),
    )
    return match.group(1) if match else ""


def _framework_shift_has_cordova(decoded_dir: Path) -> bool:
    smali_dir = decoded_dir / "smali"
    if not smali_dir.exists():
        return False
    return (smali_dir / "org/apache/cordova").exists()


def _framework_shift_layout_count(decoded_dir: Path) -> int:
    res_dir = decoded_dir / "res"
    if not res_dir.exists():
        return 0
    return sum(1 for _ in res_dir.rglob("layout*/*.xml"))


def _framework_shift_extract_const_strings(smali_text: str) -> list[str]:
    pattern = re.compile(r'const-string(?:/jumbo)?\s+[^,]+,\s+"(.*?)"')
    return [match.group(1) for match in pattern.finditer(smali_text)]


def _framework_shift_extract_anchors(decoded_dir_raw: str | Path | None) -> dict[str, Any]:
    if not decoded_dir_raw:
        return {
            "all": set(),
            "assets": set(),
            "res": set(),
            "smali": set(),
            "names": set(),
            "package": "",
            "has_assets_www": False,
            "has_cordova": False,
            "hybrid_hint": False,
            "layout_count": 0,
        }

    decoded_dir = Path(decoded_dir_raw)
    assets_tokens: set[str] = set()
    res_tokens: set[str] = set()
    smali_tokens: set[str] = set()
    name_tokens: set[str] = set()

    assets_dir = decoded_dir / "assets"
    if assets_dir.exists():
        for path in assets_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in FRAMEWORK_SHIFT_TEXT_SUFFIXES:
                assets_tokens |= _framework_shift_tokenize(
                    _framework_shift_safe_read_text(path)
                )

    res_dir = decoded_dir / "res"
    if res_dir.exists():
        for path in res_dir.rglob("*"):
            if not path.is_file():
                continue
            name_tokens |= _framework_shift_tokenize(path.stem)
            if path.suffix.lower() in FRAMEWORK_SHIFT_TEXT_SUFFIXES:
                res_tokens |= _framework_shift_tokenize(
                    _framework_shift_safe_read_text(path)
                )

    smali_dir = decoded_dir / "smali"
    if smali_dir.exists():
        for path in smali_dir.rglob("*.smali"):
            values = _framework_shift_extract_const_strings(
                _framework_shift_safe_read_text(path)
            )
            if values:
                smali_tokens |= _framework_shift_tokenize("\n".join(values))

    has_assets_www = (assets_dir / "www").exists()
    has_cordova = _framework_shift_has_cordova(decoded_dir)
    return {
        "all": assets_tokens | res_tokens | smali_tokens | name_tokens,
        "assets": assets_tokens,
        "res": res_tokens,
        "smali": smali_tokens,
        "names": name_tokens,
        "package": _framework_shift_package_name(decoded_dir),
        "has_assets_www": has_assets_www,
        "has_cordova": has_cordova,
        "hybrid_hint": has_assets_www or has_cordova,
        "layout_count": _framework_shift_layout_count(decoded_dir),
    }


def _framework_shift_jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _framework_shift_containment(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def build_framework_shift_evidence_fields(
    decoded_a: str | Path | None,
    decoded_b: str | Path | None,
) -> dict[str, Any]:
    """Build guarded framework-shift evidence fields.

    This is evidence-only: it explains a possible hybrid/WebView to native
    rewrite under the same package name, but it never changes
    ``similarity_score`` or ``similarity_score_source``.
    """
    if not decoded_a or not decoded_b:
        return _framework_shift_default_fields(error="missing_decoded_dir")

    left = _framework_shift_extract_anchors(decoded_a)
    right = _framework_shift_extract_anchors(decoded_b)
    common = set(left["all"]) & set(right["all"])
    package_equal = bool(left["package"] and left["package"] == right["package"])
    hybrid_to_native = (
        bool(left["hybrid_hint"]) != bool(right["hybrid_hint"])
        and int(left["layout_count"]) != int(right["layout_count"])
    )
    anchor_containment = _framework_shift_containment(left["all"], right["all"])
    anchor_jaccard = _framework_shift_jaccard(left["all"], right["all"])
    applied = (
        package_equal
        and hybrid_to_native
        and anchor_containment >= FRAMEWORK_SHIFT_MIN_ANCHOR_CONTAINMENT
        and len(common) >= FRAMEWORK_SHIFT_MIN_COMMON_ANCHORS
    )

    fields = _framework_shift_default_fields()
    fields.update(
        {
            "framework_shift_evidence_applied": bool(applied),
            "framework_shift_left_package": str(left["package"]),
            "framework_shift_right_package": str(right["package"]),
            "framework_shift_package_equal": bool(package_equal),
            "framework_shift_left_hybrid_hint": bool(left["hybrid_hint"]),
            "framework_shift_right_hybrid_hint": bool(right["hybrid_hint"]),
            "framework_shift_hybrid_to_native": bool(hybrid_to_native),
            "framework_shift_left_layout_count": int(left["layout_count"]),
            "framework_shift_right_layout_count": int(right["layout_count"]),
            "framework_shift_left_anchor_count": len(left["all"]),
            "framework_shift_right_anchor_count": len(right["all"]),
            "framework_shift_common_anchor_count": len(common),
            "framework_shift_anchor_containment": float(anchor_containment),
            "framework_shift_anchor_jaccard": float(anchor_jaccard),
            "framework_shift_common_anchor_sample": sorted(common)[:30],
        }
    )
    return fields


def load_layers_for_pairwise(
    apk_path: str,
    decoded_dir: str | None,
    selected_layers: list[str],
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    feature_cache: Any | None = None,
) -> dict[str, set[str]]:
    cache_key = (apk_path, decoded_dir)
    if cache_key in layer_cache:
        return layer_cache[cache_key]

    apk_file = Path(apk_path)
    if not apk_file.is_file():
        raise PairwiseAnalysisError("APK does not exist: {}".format(apk_path))

    feature_bundle = None
    apk_sha256 = None
    if feature_cache is not None:
        apk_sha256 = _sha256_of_file(apk_file)
        feature_bundle = feature_cache.get(apk_sha256, FEATURE_CACHE_VERSION)

    if extract_all_features is None:
        raise PairwiseAnalysisError("m_static_views_unavailable")

    if feature_bundle is None:
        try:
            feature_bundle = extract_all_features(
                apk_path=str(apk_file),
                unpacked_dir=decoded_dir,
            )
        except Exception as error:
            raise PairwiseAnalysisError("feature_bundle_error: {}".format(error)) from error
        if feature_cache is not None and apk_sha256 is not None:
            feature_cache.put(apk_sha256, FEATURE_CACHE_VERSION, feature_bundle)

    code_v4_shingled = feature_bundle.get("code_v4_shingled")
    code_v4 = feature_bundle.get("code_v4")
    layers = {
        "code": normalize_pairwise_layer_tokens("code", feature_bundle.get("code", set())),
        "code_fingerprint": flatten_code_fingerprint_features(
            code_v4_shingled
        )
        or flatten_code_fingerprint_features(code_v4),
        "code_fingerprint_values": flatten_code_fingerprint_values(code_v4_shingled)
        or flatten_code_fingerprint_values(code_v4),
        "code_method_fingerprints": flatten_code_method_fingerprints(
            code_v4_shingled
        )
        or flatten_code_method_fingerprints(code_v4),
        "metadata": normalize_pairwise_layer_tokens("metadata", feature_bundle.get("metadata", set())),
        "component": normalize_pairwise_layer_tokens("component", feature_bundle.get("component", {})),
        "resource": normalize_pairwise_layer_tokens("resource", feature_bundle.get("resource", {})),
        "library": normalize_pairwise_layer_tokens("library", feature_bundle.get("library", {})),
    }
    layer_cache[cache_key] = layers
    return layers


def load_ged_modules():
    try:
        from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix as comp
        from script.calculate_apks_similarity.build_model import build_model as model
        from script.calculate_apks_similarity.calculate_models_similarity import (
            calculate_models_similarity as models_similarity,
        )
        from script.calculate_apks_similarity.result_contract import (
            calculate_library_reduced_score as reduced_score,
        )
        from script.calculate_apks_similarity.result_contract import serialize_sim_pairs as sim_pairs_serializer
    except Exception:
        try:
            from calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix as comp
            from calculate_apks_similarity.build_model import build_model as model
            from calculate_apks_similarity.calculate_models_similarity import (
                calculate_models_similarity as models_similarity,
            )
            from calculate_apks_similarity.result_contract import (
                calculate_library_reduced_score as reduced_score,
            )
            from calculate_apks_similarity.result_contract import serialize_sim_pairs as sim_pairs_serializer
        except Exception as error:
            raise PairwiseAnalysisError(
                "GED metric dependencies are unavailable in the current Python environment."
            ) from error
    return comp, model, models_similarity, reduced_score, sim_pairs_serializer


def load_code_model_for_apk(apk_path: str, code_cache: dict[str, list], build_model_fn) -> list:
    if apk_path in code_cache:
        return code_cache[apk_path]

    apk_file = Path(apk_path)
    if not apk_file.is_file():
        raise PairwiseAnalysisError("APK does not exist: {}".format(apk_path))

    with tempfile.TemporaryDirectory(prefix="pairwise_code_") as output_dir:
        with working_directory(PROJECT_ROOT):
            dots = build_model_fn(apk_path, output_dir)
    code_cache[apk_path] = dots
    return dots


def calculate_ged_scores(
    apk_a: str,
    apk_b: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    code_cache: dict[str, list],
) -> tuple[float, float]:
    (
        build_comparison_matrix_fn,
        build_model_fn,
        calculate_models_similarity_fn,
        calculate_library_reduced_score_fn,
        serialize_sim_pairs_fn,
    ) = load_ged_modules()

    dots_1 = load_code_model_for_apk(apk_a, code_cache, build_model_fn)
    dots_2 = load_code_model_for_apk(apk_b, code_cache, build_model_fn)

    if not dots_1 or not dots_2:
        raise PairwiseAnalysisError("feature_extraction_failed")

    m_comp = build_comparison_matrix_fn(
        dots_1,
        dots_2,
        ins_block_sim_threshold=ins_block_sim_threshold,
        ged_timeout_sec=ged_timeout_sec,
        processes_count=processes_count,
        threads_count=threads_count,
    )
    full_similarity_score, sim_pairs = calculate_models_similarity_fn(m_comp, dots_1, dots_2)
    pair_records = serialize_sim_pairs_fn(sim_pairs)
    library_reduced_score = calculate_library_reduced_score_fn(pair_records, dots_1, dots_2)
    return float(full_similarity_score), float(library_reduced_score)


def calculate_set_scores(
    apk_a: str,
    apk_b: str,
    decoded_a: str | None,
    decoded_b: str | None,
    selected_layers: list[str],
    metric: str,
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    feature_cache: Any | None = None,
) -> tuple[float, float]:
    layers_a = load_layers_for_pairwise(
        apk_a,
        decoded_a,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )
    layers_b = load_layers_for_pairwise(
        apk_b,
        decoded_b,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )

    full_left = aggregate_features(layers_a, selected_layers)
    full_right = aggregate_features(layers_b, selected_layers)
    full_similarity_score = calculate_set_metric(metric, full_left, full_right)

    # DEEP-24-LIBRARY-REDUCED-UNIFY: единая каноническая формула из контракта v1
    # раздел 4.4. Ранее здесь была set-метрика на agg_features без library-слоя
    # (вторая из трёх несовместимых формул: см. critic deep-23 пункт 1, discovery
    # `inbox/library-reduced-discovery.md`). Каноническая формула не зависит от
    # `metric` (контракт пункт 1) — поэтому переменная `metric` теперь
    # используется только для `full_similarity_score`.
    library_reduced_score = m_static_views.library_reduced_score_canonical(
        layers_a, layers_b, list(selected_layers),
    )
    return float(full_similarity_score), float(library_reduced_score)


def _coverage_of_larger(left: set[str], right: set[str]) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def _containment_direction(left: set[str], right: set[str]) -> str:
    if not left or not right:
        return "none"
    if left == right:
        return "symmetric"
    if len(left) < len(right):
        return "a_in_b"
    if len(right) < len(left):
        return "b_in_a"
    return "same_size_partial"


def _added_code_direction(left: set[str], right: set[str]) -> str:
    if not left or not right:
        return "none"
    if len(right) > len(left):
        return "a_to_b"
    if len(left) > len(right):
        return "b_to_a"
    return "same_size_or_unknown"


def _added_code_delta(left: set[str], right: set[str]) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 0.0
    return max(len(left - right), len(right - left)) / denominator


def _counter_containment(left: list[str], right: list[str]) -> float:
    left_counter = Counter(left)
    right_counter = Counter(right)
    denominator = min(sum(left_counter.values()), sum(right_counter.values()))
    if denominator == 0:
        return 0.0
    keys = set(left_counter) | set(right_counter)
    intersection = sum(
        min(left_counter.get(key, 0), right_counter.get(key, 0))
        for key in keys
    )
    return intersection / denominator


def build_code_stats_containment_policy_fields(
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build R_code_stats containment diagnostics with resource corroboration.

    This is not a class-specific rule for ``code_deletion``. It is a general
    pairwise decision channel: if one code-token set is almost contained in the
    other and strict resource path+digest tokens independently confirm the pair,
    the selected content score may use the containment signal.
    """
    selected = set(selected_layers)
    fingerprint_a = set(layers_a.get("code_fingerprint", set()))
    fingerprint_b = set(layers_b.get("code_fingerprint", set()))
    if fingerprint_a and fingerprint_b:
        code_a = fingerprint_a
        code_b = fingerprint_b
        code_representation = "code_fingerprint"
    else:
        code_a = set(layers_a.get("code", set()))
        code_b = set(layers_b.get("code", set()))
        code_representation = "code"
    resource_a = set(layers_a.get("resource", set()))
    resource_b = set(layers_b.get("resource", set()))

    code_containment = float(containment_similarity(code_a, code_b))
    resource_corroboration = float(containment_similarity(resource_a, resource_b))
    larger_coverage = float(_coverage_of_larger(code_a, code_b))
    active = "code" in selected and "resource" in selected
    applied = (
        active
        and code_containment >= CODE_STATS_CONTAINMENT_THRESHOLD
        and resource_corroboration >= CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
    )

    return {
        "code_stats_containment_policy_id": CODE_STATS_CONTAINMENT_POLICY_ID,
        "code_stats_containment_policy_applied": bool(applied),
        "code_stats_containment_score": code_containment,
        "code_stats_containment_larger_score": larger_coverage,
        "code_stats_containment_direction": _containment_direction(code_a, code_b),
        "code_stats_containment_representation": code_representation,
        "code_stats_resource_corroboration_score": resource_corroboration,
        "code_stats_resource_corroboration_signal": CODE_STATS_RESOURCE_CORROBORATION_SIGNAL,
        "code_stats_containment_threshold": CODE_STATS_CONTAINMENT_THRESHOLD,
        "code_stats_resource_corroboration_threshold": (
            CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
        ),
        "method_id_inclusion_smaller": code_containment,
        "method_id_inclusion_larger": larger_coverage,
        # Backward-compatible aliases for experiment artifacts produced before
        # the R_code_shape -> R_code_stats naming decision.
        "code_containment_score": code_containment,
        "code_containment_direction": _containment_direction(code_a, code_b),
        "code_containment_resource_score": resource_corroboration,
    }


def build_code_stats_added_code_policy_fields(
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build added-code diagnostics for the general R_code_stats policy.

    This channel addresses injection-like cases without using dataset labels.
    It can promote the selected content score only when a preserved code core
    and independent resource support are both present.
    """
    selected = set(selected_layers)
    fingerprint_a = set(layers_a.get("code_fingerprint", set()))
    fingerprint_b = set(layers_b.get("code_fingerprint", set()))
    if fingerprint_a or fingerprint_b:
        code_a = fingerprint_a
        code_b = fingerprint_b
        code_representation = "code_fingerprint"
    else:
        code_a = set(layers_a.get("code", set()))
        code_b = set(layers_b.get("code", set()))
        code_representation = "code"
    resource_a = set(layers_a.get("resource", set()))
    resource_b = set(layers_b.get("resource", set()))

    preserved_core = float(containment_similarity(code_a, code_b))
    preserved_methods = len(code_a & code_b)
    added_delta = float(_added_code_delta(code_a, code_b))
    resource_support = float(containment_similarity(resource_a, resource_b))
    evidence_score = min(preserved_core, resource_support)
    active = "code" in selected and "resource" in selected
    applied = (
        active
        and preserved_core >= CODE_STATS_ADDED_CODE_PRESERVED_CORE_THRESHOLD
        and added_delta >= CODE_STATS_ADDED_CODE_DELTA_THRESHOLD
        and resource_support >= CODE_STATS_ADDED_CODE_RESOURCE_SUPPORT_THRESHOLD
        and preserved_methods >= CODE_STATS_ADDED_CODE_MIN_PRESERVED_METHODS
    )

    return {
        "code_stats_added_code_policy_id": CODE_STATS_ADDED_CODE_POLICY_ID,
        "code_stats_added_code_policy_applied": bool(applied),
        "preserved_core_similarity": preserved_core,
        "preserved_core_method_count": int(preserved_methods),
        "preserved_core_threshold": CODE_STATS_ADDED_CODE_PRESERVED_CORE_THRESHOLD,
        "added_code_delta": added_delta,
        "added_code_delta_threshold": CODE_STATS_ADDED_CODE_DELTA_THRESHOLD,
        "added_code_direction": _added_code_direction(code_a, code_b),
        "added_code_resource_support_score": resource_support,
        "added_code_resource_support_threshold": (
            CODE_STATS_ADDED_CODE_RESOURCE_SUPPORT_THRESHOLD
        ),
        "added_code_resource_support_signal": CODE_STATS_ADDED_CODE_RESOURCE_SIGNAL,
        "added_code_evidence_score": evidence_score,
        "added_code_representation": code_representation,
        "added_code_min_preserved_methods": (
            CODE_STATS_ADDED_CODE_MIN_PRESERVED_METHODS
        ),
        "payload_or_hook_hint": (
            "added_code_delta_candidate"
            if added_delta >= CODE_STATS_ADDED_CODE_DELTA_THRESHOLD
            else None
        ),
        "permission_or_component_delta": "not_extracted_current_profile",
    }


def _c05_static_default_fields(error: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "c05_static_evidence_policy_id": C05_STATIC_EVIDENCE_POLICY_ID,
        "c05_static_evidence_applied": False,
        "c05_static_evidence_role": "evidence_only",
        "c05_static_evidence_ref": C05_STATIC_EVIDENCE_REF,
        "c05_static_evidence_score": 0.0,
        "c05_static_relation_score": 0.0,
        "c05_static_code_namespace_overlap": 0.0,
        "c05_static_component_namespace_overlap": 0.0,
        "c05_static_code_token_containment": 0.0,
        "c05_static_manifest_delta_count": 0,
        "c05_static_component_delta_count": 0,
        "c05_static_permission_delta_count": 0,
        "c05_static_feature_delta_count": 0,
        "c05_static_container_delta_count": 0,
        "c05_static_extra_dex_delta_count": 0,
        "c05_static_native_lib_delta_count": 0,
        "c05_static_library_delta_count": 0,
        "c05_static_manifest_delta_sample": [],
        "c05_static_container_delta_sample": [],
        "c05_static_library_delta_sample": [],
        "c05_static_relation_namespace_sample": [],
        "c05_static_min_relation_score": C05_STATIC_MIN_RELATION_SCORE,
    }
    if error is not None:
        fields["c05_static_evidence_error"] = error
    return fields


def _c05_static_split_component_tokens(
    component_tokens: set[str],
) -> tuple[set[str], set[str], set[str]]:
    permissions = {
        token for token in component_tokens if token.startswith("permission:")
    }
    features = {token for token in component_tokens if token.startswith("feature:")}
    components = component_tokens - permissions - features
    return components, permissions, features


def _c05_static_container_profile(apk_path: str | None) -> dict[str, set[str]]:
    dex_files: set[str] = set()
    extra_dex_files: set[str] = set()
    native_libs: set[str] = set()
    if not apk_path:
        return {
            "dex": dex_files,
            "extra_dex": extra_dex_files,
            "native": native_libs,
        }

    try:
        with zipfile.ZipFile(apk_path) as archive:
            for raw_name in archive.namelist():
                name = raw_name.strip()
                lower_name = name.lower()
                if not name:
                    continue
                if lower_name.endswith(".dex"):
                    dex_files.add(name)
                    if lower_name != "classes.dex":
                        extra_dex_files.add(name)
                if lower_name.startswith("lib/") and lower_name.endswith(".so"):
                    native_libs.add(name)
    except (OSError, zipfile.BadZipFile):
        pass

    return {
        "dex": dex_files,
        "extra_dex": extra_dex_files,
        "native": native_libs,
    }


def _c05_static_namespace_prefixes(tokens: set[str]) -> set[str]:
    prefixes: set[str] = set()
    for token in tokens:
        normalized = str(token).replace("/", ".")
        for match in re.finditer(
            r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b",
            normalized,
        ):
            namespace = re.sub(
                rf"^[BCDFIJSZV\[]*L(?={C05_STATIC_NAMESPACE_ROOT_RE}\.)",
                "",
                match.group(0),
            )
            namespace = re.sub(
                rf"^[BCDFIJSZV\[]+(?={C05_STATIC_NAMESPACE_ROOT_RE}\.)",
                "",
                namespace,
            )
            parts = [
                part
                for part in namespace.split(".")
                if part and part not in {"method_fp"}
            ]
            if len(parts) < 2:
                continue
            first = parts[0].lower()
            if first in C05_STATIC_NAMESPACE_STOPWORDS:
                continue
            max_size = min(3, len(parts))
            for size in range(2, max_size + 1):
                prefixes.add(".".join(parts[:size]))
    return prefixes


def _c05_static_sample(tokens: set[str]) -> list[str]:
    return sorted(tokens)[:C05_STATIC_SAMPLE_LIMIT]


def build_c05_static_evidence_fields(
    apk_a: str,
    apk_b: str,
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build evidence-only static diagnostics for added-code cases.

    This channel records static signs that one APK received extra code or
    container-level payloads. It never updates ``similarity_score``.
    """
    selected = set(selected_layers)
    active = "code" in selected
    if not active:
        return _c05_static_default_fields()

    component_a = set(layers_a.get("component", set()))
    component_b = set(layers_b.get("component", set()))
    code_a = set(layers_a.get("code_fingerprint", set())) or set(
        layers_a.get("code", set())
    )
    code_b = set(layers_b.get("code_fingerprint", set())) or set(
        layers_b.get("code", set())
    )
    library_a = set(layers_a.get("library", set()))
    library_b = set(layers_b.get("library", set()))

    components_a, permissions_a, features_a = _c05_static_split_component_tokens(
        component_a
    )
    components_b, permissions_b, features_b = _c05_static_split_component_tokens(
        component_b
    )
    component_delta = components_a ^ components_b
    permission_delta = permissions_a ^ permissions_b
    feature_delta = features_a ^ features_b
    manifest_delta = component_a ^ component_b

    container_a = _c05_static_container_profile(apk_a)
    container_b = _c05_static_container_profile(apk_b)
    extra_dex_delta = container_a["extra_dex"] ^ container_b["extra_dex"]
    native_lib_delta = container_a["native"] ^ container_b["native"]
    container_delta = extra_dex_delta | native_lib_delta
    library_delta = library_a ^ library_b

    code_namespaces_a = _c05_static_namespace_prefixes(code_a)
    code_namespaces_b = _c05_static_namespace_prefixes(code_b)
    component_namespaces_a = _c05_static_namespace_prefixes(component_a)
    component_namespaces_b = _c05_static_namespace_prefixes(component_b)
    common_code_namespaces = code_namespaces_a & code_namespaces_b
    common_component_namespaces = component_namespaces_a & component_namespaces_b

    code_namespace_overlap = float(
        containment_similarity(code_namespaces_a, code_namespaces_b)
    )
    component_namespace_overlap = float(
        containment_similarity(component_namespaces_a, component_namespaces_b)
    )
    code_token_containment = float(containment_similarity(code_a, code_b))
    relation_score = max(
        code_namespace_overlap,
        component_namespace_overlap,
        code_token_containment,
    )

    static_delta_categories = sum(
        1
        for count in (
            len(manifest_delta),
            len(extra_dex_delta),
            len(native_lib_delta),
            len(library_delta),
        )
        if count > 0
    )
    static_delta_score = min(1.0, static_delta_categories / 4)
    evidence_score = min(
        1.0,
        (relation_score * 0.6) + (static_delta_score * 0.4),
    )
    applied = (
        static_delta_categories > 0
        and relation_score >= C05_STATIC_MIN_RELATION_SCORE
    )

    fields = _c05_static_default_fields()
    fields.update(
        {
            "c05_static_evidence_applied": bool(applied),
            "c05_static_evidence_score": float(evidence_score if applied else 0.0),
            "c05_static_relation_score": float(relation_score),
            "c05_static_code_namespace_overlap": float(code_namespace_overlap),
            "c05_static_component_namespace_overlap": float(
                component_namespace_overlap
            ),
            "c05_static_code_token_containment": float(code_token_containment),
            "c05_static_manifest_delta_count": len(manifest_delta),
            "c05_static_component_delta_count": len(component_delta),
            "c05_static_permission_delta_count": len(permission_delta),
            "c05_static_feature_delta_count": len(feature_delta),
            "c05_static_container_delta_count": len(container_delta),
            "c05_static_extra_dex_delta_count": len(extra_dex_delta),
            "c05_static_native_lib_delta_count": len(native_lib_delta),
            "c05_static_library_delta_count": len(library_delta),
            "c05_static_manifest_delta_sample": _c05_static_sample(
                manifest_delta
            ),
            "c05_static_container_delta_sample": _c05_static_sample(
                container_delta
            ),
            "c05_static_library_delta_sample": _c05_static_sample(
                library_delta
            ),
            "c05_static_relation_namespace_sample": _c05_static_sample(
                common_code_namespaces | common_component_namespaces
            ),
        }
    )
    return fields


def build_code_stats_resource_change_identity_policy_fields(
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    selected = set(selected_layers)
    fingerprint_a = set(layers_a.get("code_fingerprint", set()))
    fingerprint_b = set(layers_b.get("code_fingerprint", set()))
    resource_a = set(layers_a.get("resource", set()))
    resource_b = set(layers_b.get("resource", set()))

    preserved_core = float(containment_similarity(fingerprint_a, fingerprint_b))
    preserved_methods = len(fingerprint_a & fingerprint_b)
    added_delta = float(_added_code_delta(fingerprint_a, fingerprint_b))
    resource_support = float(containment_similarity(resource_a, resource_b))
    active = "code" in selected and "resource" in selected
    fingerprint_available = bool(fingerprint_a or fingerprint_b)
    resource_changed = resource_support < CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
    applied = (
        active
        and fingerprint_available
        and preserved_core >= CODE_STATS_RESOURCE_CHANGE_IDENTITY_THRESHOLD
        and added_delta <= CODE_STATS_RESOURCE_CHANGE_IDENTITY_MAX_ADDED_DELTA
        and preserved_methods >= CODE_STATS_RESOURCE_CHANGE_IDENTITY_MIN_METHODS
        and resource_changed
    )

    return {
        "code_stats_resource_change_identity_policy_id": (
            CODE_STATS_RESOURCE_CHANGE_IDENTITY_POLICY_ID
        ),
        "code_stats_resource_change_identity_policy_applied": bool(applied),
        "resource_change_identity_score": preserved_core,
        "resource_change_identity_code_similarity": preserved_core,
        "resource_change_identity_threshold": (
            CODE_STATS_RESOURCE_CHANGE_IDENTITY_THRESHOLD
        ),
        "resource_change_identity_method_count": int(preserved_methods),
        "resource_change_identity_min_methods": (
            CODE_STATS_RESOURCE_CHANGE_IDENTITY_MIN_METHODS
        ),
        "resource_change_identity_added_code_delta": added_delta,
        "resource_change_identity_max_added_delta": (
            CODE_STATS_RESOURCE_CHANGE_IDENTITY_MAX_ADDED_DELTA
        ),
        "resource_change_identity_resource_support_score": resource_support,
        "resource_change_identity_resource_change_threshold": (
            CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
        ),
        "resource_change_identity_representation": "code_fingerprint",
    }


def build_code_stats_repack_core_policy_fields(
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build diagnostics for a large preserved code core.

    This channel addresses repackaging-like cases without dataset labels: a
    large method-fingerprint core remains, code delta is bounded, and resources
    changed enough that ordinary resource corroboration is not expected.
    """
    selected = set(selected_layers)
    fingerprint_a = set(layers_a.get("code_fingerprint", set()))
    fingerprint_b = set(layers_b.get("code_fingerprint", set()))
    resource_a = set(layers_a.get("resource", set()))
    resource_b = set(layers_b.get("resource", set()))

    preserved_core = float(containment_similarity(fingerprint_a, fingerprint_b))
    preserved_methods = len(fingerprint_a & fingerprint_b)
    added_delta = float(_added_code_delta(fingerprint_a, fingerprint_b))
    repack_core_score = min(preserved_core, max(0.0, 1.0 - added_delta))
    resource_support = float(containment_similarity(resource_a, resource_b))
    active = "code" in selected and "resource" in selected
    fingerprint_available = bool(fingerprint_a or fingerprint_b)
    resource_changed = resource_support < CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
    applied = (
        active
        and fingerprint_available
        and preserved_core >= CODE_STATS_REPACK_CORE_THRESHOLD
        and added_delta <= CODE_STATS_REPACK_CORE_MAX_ADDED_DELTA
        and preserved_methods >= CODE_STATS_REPACK_CORE_MIN_METHODS
        and resource_changed
    )

    return {
        "code_stats_repack_core_policy_id": CODE_STATS_REPACK_CORE_POLICY_ID,
        "code_stats_repack_core_policy_applied": bool(applied),
        "repack_core_score": repack_core_score,
        "repack_core_similarity": preserved_core,
        "repack_core_threshold": CODE_STATS_REPACK_CORE_THRESHOLD,
        "repack_core_method_count": int(preserved_methods),
        "repack_core_min_methods": CODE_STATS_REPACK_CORE_MIN_METHODS,
        "repack_core_added_code_delta": added_delta,
        "repack_core_max_added_delta": CODE_STATS_REPACK_CORE_MAX_ADDED_DELTA,
        "repack_core_resource_support_score": resource_support,
        "repack_core_max_resource_support": (
            CODE_STATS_RESOURCE_CORROBORATION_THRESHOLD
        ),
        "repack_core_representation": "code_fingerprint",
    }


def build_code_stats_payload_resource_policy_fields(
    layers_a: dict[str, set[str]],
    layers_b: dict[str, set[str]],
    selected_layers: list[str],
) -> dict[str, Any]:
    """Build diagnostics for code/resource supported payload-like changes.

    This channel does not use dataset labels. It requires independent evidence
    from method fingerprints and resource digests, and limits the accepted code
    delta so near-total replacement is not promoted.
    """
    selected = set(selected_layers)
    fingerprint_a = set(layers_a.get("code_fingerprint", set()))
    fingerprint_b = set(layers_b.get("code_fingerprint", set()))
    fingerprint_values_a = list(layers_a.get("code_fingerprint_values", []))
    fingerprint_values_b = list(layers_b.get("code_fingerprint_values", []))
    resource_a = set(layers_a.get("resource", set()))
    resource_b = set(layers_b.get("resource", set()))

    code_similarity = float(containment_similarity(fingerprint_a, fingerprint_b))
    preserved_methods = len(fingerprint_a & fingerprint_b)
    added_delta = float(_added_code_delta(fingerprint_a, fingerprint_b))
    resource_support = float(containment_similarity(resource_a, resource_b))
    fp_counter_containment = float(
        _counter_containment(fingerprint_values_a, fingerprint_values_b)
    )
    payload_score = (code_similarity + resource_support) / 2.0
    bridge_score = (code_similarity + 2.0 * resource_support) / 3.0
    active = "code" in selected and "resource" in selected
    fingerprint_available = bool(fingerprint_a or fingerprint_b)
    applied = (
        active
        and fingerprint_available
        and code_similarity >= CODE_STATS_PAYLOAD_RESOURCE_CODE_THRESHOLD
        and resource_support >= CODE_STATS_PAYLOAD_RESOURCE_SUPPORT_THRESHOLD
        and preserved_methods >= CODE_STATS_PAYLOAD_RESOURCE_MIN_METHODS
        and added_delta <= CODE_STATS_PAYLOAD_RESOURCE_MAX_ADDED_DELTA
        and payload_score >= CODE_STATS_PAYLOAD_RESOURCE_SCORE_THRESHOLD
    )
    bridge_applied = (
        active
        and fingerprint_available
        and code_similarity >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_CODE_THRESHOLD
        and resource_support >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SUPPORT_THRESHOLD
        and (
            fp_counter_containment
            >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_FP_COUNTER_THRESHOLD
        )
        and preserved_methods >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_METHODS
        and added_delta >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_ADDED_DELTA
        and added_delta <= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MAX_ADDED_DELTA
        and bridge_score >= CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SCORE_THRESHOLD
    )

    return {
        "code_stats_payload_resource_policy_id": (
            CODE_STATS_PAYLOAD_RESOURCE_POLICY_ID
        ),
        "code_stats_payload_resource_policy_applied": bool(applied),
        "payload_resource_score": payload_score,
        "payload_resource_code_similarity": code_similarity,
        "payload_resource_code_threshold": CODE_STATS_PAYLOAD_RESOURCE_CODE_THRESHOLD,
        "payload_resource_method_count": int(preserved_methods),
        "payload_resource_min_methods": CODE_STATS_PAYLOAD_RESOURCE_MIN_METHODS,
        "payload_resource_added_code_delta": added_delta,
        "payload_resource_max_added_delta": (
            CODE_STATS_PAYLOAD_RESOURCE_MAX_ADDED_DELTA
        ),
        "payload_resource_support_score": resource_support,
        "payload_resource_support_threshold": (
            CODE_STATS_PAYLOAD_RESOURCE_SUPPORT_THRESHOLD
        ),
        "payload_resource_score_threshold": CODE_STATS_PAYLOAD_RESOURCE_SCORE_THRESHOLD,
        "payload_resource_representation": "code_fingerprint",
        "code_stats_payload_resource_bridge_policy_id": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_POLICY_ID
        ),
        "code_stats_payload_resource_bridge_policy_applied": bool(
            bridge_applied
        ),
        "payload_resource_bridge_score": bridge_score,
        "payload_resource_bridge_formula": "resource_weighted_2x",
        "payload_resource_bridge_code_similarity": code_similarity,
        "payload_resource_bridge_code_threshold": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_CODE_THRESHOLD
        ),
        "payload_resource_bridge_support_score": resource_support,
        "payload_resource_bridge_support_threshold": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SUPPORT_THRESHOLD
        ),
        "payload_resource_fp_counter_containment": fp_counter_containment,
        "payload_resource_bridge_fp_counter_threshold": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_FP_COUNTER_THRESHOLD
        ),
        "payload_resource_bridge_method_count": int(preserved_methods),
        "payload_resource_bridge_min_methods": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_METHODS
        ),
        "payload_resource_bridge_added_code_delta": added_delta,
        "payload_resource_bridge_min_added_code_delta": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MIN_ADDED_DELTA
        ),
        "payload_resource_bridge_max_added_code_delta": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_MAX_ADDED_DELTA
        ),
        "payload_resource_bridge_score_threshold": (
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SCORE_THRESHOLD
        ),
        "payload_resource_bridge_representation": "code_fingerprint_value_counter",
    }


def build_code_stats_policy_fields_for_pair(
    apk_a: str,
    apk_b: str,
    decoded_a: str | None,
    decoded_b: str | None,
    selected_layers: list[str],
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    feature_cache: Any | None = None,
) -> dict[str, Any]:
    layers_a = load_layers_for_pairwise(
        apk_a,
        decoded_a,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )
    layers_b = load_layers_for_pairwise(
        apk_b,
        decoded_b,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )
    fields: dict[str, Any] = {}
    fields.update(
        build_code_stats_containment_policy_fields(
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    fields.update(
        build_code_stats_added_code_policy_fields(
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    fields.update(
        build_code_stats_resource_change_identity_policy_fields(
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    fields.update(
        build_code_stats_repack_core_policy_fields(
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    fields.update(
        build_code_stats_payload_resource_policy_fields(
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    fields.update(
        build_c05_static_evidence_fields(
            apk_a=apk_a,
            apk_b=apk_b,
            layers_a=layers_a,
            layers_b=layers_b,
            selected_layers=selected_layers,
        )
    )
    if build_added_code_direct_evidence_fields is not None:
        fields.update(
            build_added_code_direct_evidence_fields(
                layers_a=layers_a,
                layers_b=layers_b,
                selected_layers=selected_layers,
            )
        )
    if build_deleted_code_direct_evidence_fields is not None:
        fields.update(
            build_deleted_code_direct_evidence_fields(
                layers_a=layers_a,
                layers_b=layers_b,
                selected_layers=selected_layers,
            )
        )
    if build_code_core_evidence_fields is not None:
        fields.update(
            build_code_core_evidence_fields(
                layers_a=layers_a,
                layers_b=layers_b,
                selected_layers=selected_layers,
            )
        )
    return fields


def build_code_stats_containment_policy_fields_for_pair(
    apk_a: str,
    apk_b: str,
    decoded_a: str | None,
    decoded_b: str | None,
    selected_layers: list[str],
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    feature_cache: Any | None = None,
) -> dict[str, Any]:
    layers_a = load_layers_for_pairwise(
        apk_a,
        decoded_a,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )
    layers_b = load_layers_for_pairwise(
        apk_b,
        decoded_b,
        selected_layers,
        layer_cache,
        feature_cache=feature_cache,
    )
    return build_code_stats_containment_policy_fields(
        layers_a=layers_a,
        layers_b=layers_b,
        selected_layers=selected_layers,
    )


def apply_code_stats_score_policy(
    pair_row: dict[str, Any],
    threshold: float,
) -> None:
    base_score = pair_row.get("library_reduced_score")
    base_source = "library_reduced_score"
    if base_score is None:
        base_score = pair_row.get("full_similarity_score")
        base_source = "full_similarity_score"

    selected_score = base_score
    score_source = base_source
    selected_priority_label = "core"
    selected_priority_rank = SCORE_DECISION_PRIORITY_CORE
    selected_score_numeric: float | None
    try:
        selected_score_numeric = (
            float(selected_score) if selected_score is not None else None
        )
    except (TypeError, ValueError):
        selected_score_numeric = None

    score_candidates = (
        (
            pair_row.get("code_stats_containment_policy_applied") is True,
            pair_row.get("code_stats_containment_score"),
            CODE_STATS_CONTAINMENT_SCORE_SOURCE,
            "P0",
            SCORE_DECISION_PRIORITY_P0,
        ),
        (
            pair_row.get("code_stats_added_code_policy_applied") is True,
            pair_row.get("added_code_evidence_score"),
            CODE_STATS_ADDED_CODE_SCORE_SOURCE,
            "P2",
            SCORE_DECISION_PRIORITY_P2,
        ),
        (
            pair_row.get("code_stats_resource_change_identity_policy_applied") is True,
            pair_row.get("resource_change_identity_score"),
            CODE_STATS_RESOURCE_CHANGE_IDENTITY_SCORE_SOURCE,
            "P0",
            SCORE_DECISION_PRIORITY_P0,
        ),
        (
            pair_row.get("code_stats_repack_core_policy_applied") is True,
            pair_row.get("repack_core_score"),
            CODE_STATS_REPACK_CORE_SCORE_SOURCE,
            "P0",
            SCORE_DECISION_PRIORITY_P0,
        ),
        (
            pair_row.get("code_stats_payload_resource_policy_applied") is True
            and pair_row.get("code_stats_containment_policy_applied") is not True
            and pair_row.get("code_stats_added_code_policy_applied") is not True
            and pair_row.get("code_stats_resource_change_identity_policy_applied")
            is not True
            and pair_row.get("code_stats_repack_core_policy_applied") is not True,
            pair_row.get("payload_resource_score"),
            CODE_STATS_PAYLOAD_RESOURCE_SCORE_SOURCE,
            "P2",
            SCORE_DECISION_PRIORITY_P2,
        ),
        (
            pair_row.get("code_stats_payload_resource_bridge_policy_applied")
            is True
            and pair_row.get("code_stats_containment_policy_applied") is not True
            and pair_row.get("code_stats_added_code_policy_applied") is not True
            and pair_row.get("code_stats_resource_change_identity_policy_applied")
            is not True
            and pair_row.get("code_stats_repack_core_policy_applied") is not True
            and pair_row.get("code_stats_payload_resource_policy_applied")
            is not True,
            pair_row.get("payload_resource_bridge_score"),
            CODE_STATS_PAYLOAD_RESOURCE_BRIDGE_SCORE_SOURCE,
            "P2",
            SCORE_DECISION_PRIORITY_P2,
        ),
    )
    for (
        is_applied,
        candidate_score,
        candidate_source,
        candidate_priority_label,
        candidate_priority_rank,
    ) in score_candidates:
        if not is_applied:
            continue
        try:
            candidate_value = float(candidate_score)
        except (TypeError, ValueError):
            continue
        if (
            candidate_priority_rank > selected_priority_rank
            or (
                candidate_priority_rank == selected_priority_rank
                and (
                    selected_score_numeric is None
                    or candidate_value > selected_score_numeric
                )
            )
        ):
            selected_score = candidate_value
            selected_score_numeric = candidate_value
            score_source = candidate_source
            selected_priority_label = candidate_priority_label
            selected_priority_rank = candidate_priority_rank

    pair_row["score_conflict_guard_policy_id"] = CODE_CONFLICT_GUARD_POLICY_ID
    pair_row["score_conflict_guard_applied"] = False
    pair_row["score_conflict_guard_reason"] = None
    pair_row["score_conflict_guard_original_score"] = None
    pair_row["score_conflict_guard_adjusted_score"] = None
    pair_row["score_conflict_guard_review_threshold"] = (
        CODE_CONFLICT_GUARD_REVIEW_THRESHOLD
    )
    pair_row["score_conflict_guard_high_threshold"] = (
        CODE_CONFLICT_GUARD_HIGH_THRESHOLD
    )
    pair_row["score_conflict_guard_cap"] = CODE_CONFLICT_GUARD_REVIEW_CAP
    if score_source == "library_reduced_score" and selected_score_numeric is not None:
        try:
            preserved_core = float(pair_row.get("preserved_core_similarity"))
        except (TypeError, ValueError):
            preserved_core = None
        try:
            preserved_methods = int(pair_row.get("preserved_core_method_count"))
        except (TypeError, ValueError):
            preserved_methods = None
        has_code_fingerprint_conflict = (
            pair_row.get("added_code_representation") == "code_fingerprint"
            and preserved_core == 0.0
            and preserved_methods == 0
        )
        is_review_score = (
            selected_score_numeric >= CODE_CONFLICT_GUARD_REVIEW_THRESHOLD
            and selected_score_numeric < CODE_CONFLICT_GUARD_HIGH_THRESHOLD
        )
        if has_code_fingerprint_conflict and is_review_score:
            original_score = selected_score_numeric
            selected_score = min(
                selected_score_numeric,
                CODE_CONFLICT_GUARD_REVIEW_CAP,
            )
            selected_score_numeric = float(selected_score)
            score_source = CODE_CONFLICT_GUARD_SCORE_SOURCE
            selected_priority_label = "P0_guard"
            selected_priority_rank = SCORE_DECISION_PRIORITY_P0_GUARD
            pair_row["score_conflict_guard_applied"] = True
            pair_row["score_conflict_guard_reason"] = CODE_CONFLICT_GUARD_REASON
            pair_row["score_conflict_guard_original_score"] = original_score
            pair_row["score_conflict_guard_adjusted_score"] = selected_score_numeric

    pair_row["score_decision_priority_order"] = SCORE_DECISION_PRIORITY_ORDER
    pair_row["score_decision_selected_priority"] = selected_priority_label
    pair_row["score_decision_selected_priority_rank"] = selected_priority_rank
    pair_row["similarity_score"] = selected_score
    pair_row["selected_similarity_score"] = selected_score
    pair_row["similarity_score_source"] = score_source
    pair_row["library_reduced_status"] = (
        "computed" if pair_row.get("library_reduced_score") is not None else "not_computed"
    )
    pair_row["failure_similarity_semantics"] = None
    pair_row["score_decision_policy_id"] = DEEP_M2_SCORE_DECISION_POLICY_ID
    pair_row["packaging_evidence_role"] = "evidence_only"
    pair_row["packaging_score_included"] = False

    try:
        decision_score = float(selected_score) if selected_score is not None else None
    except (TypeError, ValueError):
        decision_score = None
    if decision_score is not None:
        pair_row["status"] = "success" if decision_score >= threshold else "low_similarity"


def _score_float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _score_priority_rank_or_core(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return SCORE_DECISION_PRIORITY_CORE


def apply_semantic_multiview_score_policy(
    pair_row: dict[str, Any],
    threshold: float,
) -> None:
    """Promote a guarded semantic_multiview high relation to score source.

    The policy is intentionally narrow: it promotes only
    ``same_resources_code_stats_match`` when both independent anchors are
    strong enough. Review-band semantic results remain evidence-only.
    """
    pair_row["semantic_multiview_score_policy_id"] = (
        SEMANTIC_MULTIVIEW_SCORE_POLICY_ID
    )
    pair_row["semantic_multiview_score_policy_applied"] = False
    pair_row["semantic_multiview_score_selected"] = False
    pair_row["semantic_multiview_score_policy_reason"] = None
    pair_row["semantic_multiview_score_source"] = SEMANTIC_MULTIVIEW_SCORE_SOURCE
    pair_row["semantic_multiview_promotion_ref"] = SEMANTIC_MULTIVIEW_PROMOTION_REF
    pair_row["semantic_multiview_promotion_relation"] = None
    pair_row["semantic_multiview_promotion_score"] = None
    pair_row["semantic_multiview_code_stats_score"] = None
    pair_row["semantic_multiview_resource_identity_score"] = None
    pair_row["semantic_multiview_resource_structure_score"] = None
    pair_row["semantic_multiview_code_stats_threshold"] = (
        SEMANTIC_MULTIVIEW_CODE_STATS_THRESHOLD
    )
    pair_row["semantic_multiview_resource_identity_threshold"] = (
        SEMANTIC_MULTIVIEW_RESOURCE_IDENTITY_THRESHOLD
    )
    pair_row["semantic_multiview_resource_structure_threshold"] = (
        SEMANTIC_MULTIVIEW_RESOURCE_STRUCTURE_THRESHOLD
    )

    if pair_row.get("status") == "analysis_failed":
        pair_row["semantic_multiview_score_policy_reason"] = "analysis_failed_status"
        return

    semantic = pair_row.get("semantic_multiview")
    if not isinstance(semantic, dict):
        pair_row["semantic_multiview_score_policy_reason"] = (
            "missing_semantic_multiview"
        )
        return

    if semantic.get("status") != "success":
        pair_row["semantic_multiview_score_policy_reason"] = (
            "semantic_status_not_success"
        )
        return

    band = str(semantic.get("semantic_band") or "")
    relation = str(semantic.get("semantic_relation") or "")
    pair_row["semantic_multiview_promotion_relation"] = relation or None
    if band != "high":
        pair_row["semantic_multiview_score_policy_reason"] = "semantic_band_not_high"
        return
    if relation != SEMANTIC_MULTIVIEW_PROMOTION_RELATION:
        pair_row["semantic_multiview_score_policy_reason"] = (
            "semantic_relation_not_promoted"
        )
        return

    scores = semantic.get("scores")
    if not isinstance(scores, dict):
        pair_row["semantic_multiview_score_policy_reason"] = "missing_semantic_scores"
        return

    code_stats = _score_float_or_none(scores.get("R_code_stats"))
    resource_identity = _score_float_or_none(scores.get("R_resource_identity"))
    resource_structure = _score_float_or_none(scores.get("R_resource_structure"))
    pair_row["semantic_multiview_code_stats_score"] = code_stats
    pair_row["semantic_multiview_resource_identity_score"] = resource_identity
    pair_row["semantic_multiview_resource_structure_score"] = resource_structure
    if (
        code_stats is None
        or resource_identity is None
        or resource_structure is None
    ):
        pair_row["semantic_multiview_score_policy_reason"] = "missing_guard_scores"
        return
    if (
        code_stats < SEMANTIC_MULTIVIEW_CODE_STATS_THRESHOLD
        or resource_identity < SEMANTIC_MULTIVIEW_RESOURCE_IDENTITY_THRESHOLD
        or resource_structure < SEMANTIC_MULTIVIEW_RESOURCE_STRUCTURE_THRESHOLD
    ):
        pair_row["semantic_multiview_score_policy_reason"] = (
            "guard_scores_below_threshold"
        )
        return

    promotion_score = min(code_stats, resource_identity)
    pair_row["semantic_multiview_score_policy_applied"] = True
    pair_row["semantic_multiview_promotion_score"] = promotion_score

    current_rank = _score_priority_rank_or_core(
        pair_row.get("score_decision_selected_priority_rank")
    )
    current_score = _score_float_or_none(pair_row.get("similarity_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("selected_similarity_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("library_reduced_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("full_similarity_score"))

    semantic_priority_label = "P0_guard"
    semantic_priority_rank = SCORE_DECISION_PRIORITY_P0_GUARD
    should_select = (
        semantic_priority_rank > current_rank
        or (
            semantic_priority_rank == current_rank
            and (current_score is None or promotion_score > current_score)
        )
    )
    if not should_select:
        pair_row["semantic_multiview_score_policy_reason"] = (
            "existing_score_has_equal_or_higher_priority"
        )
        return

    pair_row["semantic_multiview_score_selected"] = True
    pair_row["semantic_multiview_score_policy_reason"] = (
        "selected_as_similarity_score"
    )
    pair_row["score_decision_priority_order"] = SCORE_DECISION_PRIORITY_ORDER
    pair_row["score_decision_selected_priority"] = semantic_priority_label
    pair_row["score_decision_selected_priority_rank"] = semantic_priority_rank
    pair_row["similarity_score"] = promotion_score
    pair_row["selected_similarity_score"] = promotion_score
    pair_row["similarity_score_source"] = SEMANTIC_MULTIVIEW_SCORE_SOURCE
    pair_row["score_decision_policy_id"] = DEEP_M2_SCORE_DECISION_POLICY_ID
    pair_row["failure_similarity_semantics"] = None
    pair_row["status"] = "success" if promotion_score >= threshold else "low_similarity"


def apply_c05_static_score_policy(
    pair_row: dict[str, Any],
    threshold: float,
) -> None:
    """Promote only the guarded high-only C05 static evidence score.

    This policy follows the M3 C05 gate: it may raise the selected score only
    when manifest delta, relation score, and evidence score are all strong. It
    never promotes container-only evidence and never lowers an existing score.
    """
    pair_row["c05_static_score_policy_id"] = C05_STATIC_SCORE_POLICY_ID
    pair_row["c05_static_score_policy_applied"] = False
    pair_row["c05_static_score_selected"] = False
    pair_row["c05_static_score_policy_reason"] = None
    pair_row["c05_static_score_source"] = C05_STATIC_SCORE_SOURCE
    pair_row["c05_static_score_ref"] = C05_STATIC_SCORE_REF
    pair_row["c05_static_score_evidence_threshold"] = (
        C05_STATIC_SCORE_EVIDENCE_THRESHOLD
    )
    pair_row["c05_static_score_relation_threshold"] = (
        C05_STATIC_SCORE_RELATION_THRESHOLD
    )
    pair_row["c05_static_score_candidate"] = None

    if pair_row.get("status") == "analysis_failed":
        pair_row["c05_static_score_policy_reason"] = "analysis_failed_status"
        return
    if pair_row.get("c05_static_evidence_applied") is not True:
        pair_row["c05_static_score_policy_reason"] = "static_evidence_not_applied"
        return

    try:
        manifest_delta = int(pair_row.get("c05_static_manifest_delta_count") or 0)
    except (TypeError, ValueError):
        manifest_delta = 0
    if manifest_delta <= 0:
        pair_row["c05_static_score_policy_reason"] = "missing_manifest_delta"
        return

    relation_score = _score_float_or_none(pair_row.get("c05_static_relation_score"))
    if (
        relation_score is None
        or relation_score < C05_STATIC_SCORE_RELATION_THRESHOLD
    ):
        pair_row["c05_static_score_policy_reason"] = "relation_below_threshold"
        return

    evidence_score = _score_float_or_none(pair_row.get("c05_static_evidence_score"))
    if (
        evidence_score is None
        or evidence_score < C05_STATIC_SCORE_EVIDENCE_THRESHOLD
    ):
        pair_row["c05_static_score_policy_reason"] = "evidence_score_below_threshold"
        return

    pair_row["c05_static_score_policy_applied"] = True
    pair_row["c05_static_score_candidate"] = evidence_score

    current_score = _score_float_or_none(pair_row.get("similarity_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("selected_similarity_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("library_reduced_score"))
    if current_score is None:
        current_score = _score_float_or_none(pair_row.get("full_similarity_score"))

    if current_score is not None and evidence_score <= current_score:
        pair_row["c05_static_score_policy_reason"] = "existing_score_is_equal_or_higher"
        return

    pair_row["c05_static_score_selected"] = True
    pair_row["c05_static_score_policy_reason"] = "selected_as_similarity_score"
    pair_row["score_decision_priority_order"] = SCORE_DECISION_PRIORITY_ORDER
    pair_row["score_decision_selected_priority"] = "P0_guard"
    pair_row["score_decision_selected_priority_rank"] = SCORE_DECISION_PRIORITY_P0_GUARD
    pair_row["similarity_score"] = evidence_score
    pair_row["selected_similarity_score"] = evidence_score
    pair_row["similarity_score_source"] = C05_STATIC_SCORE_SOURCE
    pair_row["score_decision_policy_id"] = DEEP_M2_SCORE_DECISION_POLICY_ID
    pair_row["failure_similarity_semantics"] = None
    pair_row["status"] = "success" if evidence_score >= threshold else "low_similarity"


def apply_code_stats_containment_score_policy(
    pair_row: dict[str, Any],
    threshold: float,
) -> None:
    apply_code_stats_score_policy(pair_row, threshold=threshold)


def calculate_pair_scores(
    apk_a: str,
    apk_b: str,
    decoded_a: str | None,
    decoded_b: str | None,
    selected_layers: list[str],
    metric: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    code_cache: dict[str, list],
    feature_cache: Any | None = None,
) -> tuple[float, float, list[str]]:
    if metric == "ged":
        if "code" not in selected_layers:
            raise PairwiseAnalysisError("GED metric requires 'code' layer in pairwise.features.")
        full, reduced = calculate_ged_scores(
            apk_a=apk_a,
            apk_b=apk_b,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
            code_cache=code_cache,
        )
        return full, reduced, ["code"]

    if metric == "hybrid":
        full_parts = []
        reduced_parts = []
        layers_used = []

        if "code" in selected_layers:
            code_full, code_reduced = calculate_ged_scores(
                apk_a=apk_a,
                apk_b=apk_b,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
                code_cache=code_cache,
            )
            full_parts.append(code_full)
            reduced_parts.append(code_reduced)
            layers_used.append("code")

        non_code_layers = [layer for layer in selected_layers if layer != "code"]
        if non_code_layers:
            non_code_full, non_code_reduced = calculate_set_scores(
                apk_a=apk_a,
                apk_b=apk_b,
                decoded_a=decoded_a,
                decoded_b=decoded_b,
                selected_layers=non_code_layers,
                metric="cosine",
                layer_cache=layer_cache,
                feature_cache=feature_cache,
            )
            full_parts.append(non_code_full)
            reduced_parts.append(non_code_reduced)
            layers_used.extend(non_code_layers)

        if not full_parts:
            raise PairwiseAnalysisError("Hybrid metric has no usable layers.")

        full_score = sum(full_parts) / len(full_parts)
        reduced_score = sum(reduced_parts) / len(reduced_parts)
        return float(full_score), float(reduced_score), list(dict.fromkeys(layers_used))

    full, reduced = calculate_set_scores(
        apk_a=apk_a,
        apk_b=apk_b,
        decoded_a=decoded_a,
        decoded_b=decoded_b,
        selected_layers=selected_layers,
        metric=metric,
        layer_cache=layer_cache,
        feature_cache=feature_cache,
    )
    return full, reduced, list(selected_layers)


def _should_skip_deep_verification(candidate: dict[str, Any]) -> bool:
    """EXEC-091-EXEC: решение о реальном пропуске тяжёлых функций углублённого сравнения.

    Сокращённый путь применяется только при одновременном выполнении:
      - ``candidate["shortcut_applied"] is True`` (флаг из screening);
      - ``candidate["shortcut_reason"] == "high_confidence_signature_match"``;
      - ``candidate["signature_match"]["status"] == "match"`` (страховка
        от рассинхрона: если подпись больше не match, пропускать нельзя).

    Если хотя бы одно условие не выполнено — возвращаем False и пара идёт
    обычным (тяжёлым) путём.
    """
    if candidate.get("shortcut_applied") is not True:
        return False
    if candidate.get("shortcut_reason") != SHORTCUT_REASON_HIGH_CONFIDENCE:
        return False
    signature_match = candidate.get("signature_match")
    if not isinstance(signature_match, dict):
        return False
    if signature_match.get("status") != "match":
        return False
    return True


def _coerce_unit_float(value: Any, default: float = 0.0) -> float:
    """DEEP-26-SHORTCUT-EVIDENCE-FILL: безопасно привести к float в [0, 1].

    Используется для переноса score-полей из screening (retrieval_score,
    signature_match.score) в pair_row shortcut-пары. Невалидные значения
    дают ``default``, выход за [0, 1] зажимается.
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    if result < 0.0:
        return 0.0
    if result > 1.0:
        return 1.0
    return result


def _shortcut_full_similarity_score(candidate: dict[str, Any]) -> float:
    """DEEP-26-SHORTCUT-EVIDENCE-FILL: вывести full_similarity_score для shortcut-пары.

    Источники по приоритету (single-source-of-truth — screening signal,
    тяжёлая feature extraction в shortcut path не вызывается):

    1. ``candidate["retrieval_score"]`` — агрегированный screening score,
       заведомо высокий когда shortcut применяется (см.
       ``screening_runner._compute_shortcut_flags``).
    2. ``candidate["signature_match"]["score"]`` — fallback, когда retrieval
       по какой-то причине не пробросился (искусственные тесты, legacy).

    Возвращает float в [0, 1]; гарантированно не None.
    """
    retrieval = candidate.get("retrieval_score")
    if retrieval is not None:
        return _coerce_unit_float(retrieval, default=0.0)
    signature_match = candidate.get("signature_match")
    if isinstance(signature_match, dict):
        return _coerce_unit_float(signature_match.get("score"), default=0.0)
    return 0.0


def _build_shortcut_pair_row(
    candidate: dict[str, Any],
    selected_layers: list[str],
    elapsed_ms_deep: int,
) -> dict[str, Any]:
    """EXEC-091-EXEC: сформировать pair_row для пары, реально пропущенной по короткому пути.

    Запись помечается как «углублённое подтверждение пропущено по политике
    короткого пути», а не как успешное подтверждение сходства тяжёлыми
    функциями. Поле ``shortcut_status="success_shortcut"`` выставляется
    именно здесь — после реального пропускания тяжёлых функций.

    DEEP-26-SHORTCUT-EVIDENCE-FILL: shortcut-пара заполняет
    ``full_similarity_score`` и ``library_reduced_score`` non-None
    значениями из screening signal (retrieval_score / signature_match.score),
    переносит ``per_view_scores`` из candidate в pair_row, чтобы
    ``collect_evidence_from_pairwise`` построил per-layer Evidence.

    Контракт значений:
      - ``full_similarity_score`` — float в [0, 1], берётся из
        ``candidate["retrieval_score"]`` (screening), fallback на
        ``signature_match["score"]``;
      - ``library_reduced_score`` — float в [0, 1], равен
        ``full_similarity_score`` (shortcut path не вызывает heavy feature
        extraction; при высоком retrieval_score и signature_match=match
        library_reduced близок к full — безопасное приближение, и контроль
        реального расхождения уже делается через DEEP-21-SHORTCUT-LIBRARY-
        REDUCED-CONTROL);
      - ``per_view_scores`` — переносится из candidate, чтобы Evidence
        получил per-layer magnitude;
      - ``evidence`` — non-empty: signature_match + по одной layer_score
        записи на каждый присутствующий слой (если per_view_scores не
        переданы, fallback на общий layer_score = library_reduced_score).
    """
    app_a_raw, app_b_raw = extract_apps(candidate)
    app_a = resolve_app_label(app_a_raw, "unknown_app_a")
    app_b = resolve_app_label(app_b_raw, "unknown_app_b")

    signature_match = candidate.get("signature_match")
    if not isinstance(signature_match, dict):
        signature_match = {"score": 0.0, "status": "missing"}

    full_similarity_score = _shortcut_full_similarity_score(candidate)
    # library_reduced_score: fallback на full (shortcut не делает heavy
    # feature extraction; реальный контроль расхождения — отдельной
    # задачей DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL).
    library_reduced_score = full_similarity_score

    pair_row: dict[str, Any] = {
        "app_a": app_a,
        "app_b": app_b,
        "verdict": SHORTCUT_VERDICT_LIKELY_CLONE,
        "deep_verification_status": DEEP_VERIFICATION_STATUS_SKIPPED,
        "shortcut_status": SHORTCUT_STATUS_SUCCESS,
        "shortcut_applied": True,
        "shortcut_reason": SHORTCUT_REASON_HIGH_CONFIDENCE,
        "elapsed_ms_deep": int(elapsed_ms_deep),
        "analysis_failed_reason": None,
        "full_similarity_score": float(full_similarity_score),
        "library_reduced_score": float(library_reduced_score),
        "status": "success_shortcut",
        "views_used": list(selected_layers),
        "signature_match": dict(signature_match),
    }

    # DEEP-26-SHORTCUT-EVIDENCE-FILL: пробросить per_view_scores из screening,
    # чтобы collect_evidence_from_pairwise построил per-layer Evidence.
    per_view_scores = candidate.get("per_view_scores")
    if isinstance(per_view_scores, dict) and per_view_scores:
        pair_row["per_view_scores"] = dict(per_view_scores)

    pair_row["evidence"] = collect_evidence_from_pairwise(pair_row)
    return pair_row


def _semantic_multiview_enabled() -> bool:
    raw = os.environ.get(SEMANTIC_MULTIVIEW_ENABLED_ENV, "1")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _attach_semantic_multiview_check(
    *,
    pair_row: dict[str, Any],
    apk_a: str | None,
    apk_b: str | None,
    decoded_a: str | Path | None,
    decoded_b: str | Path | None,
    feature_bundle_a: dict[str, Any] | None = None,
    feature_bundle_b: dict[str, Any] | None = None,
) -> None:
    """Attach a best-effort semantic check; score selection is separate."""
    if not _semantic_multiview_enabled():
        return
    if run_semantic_multiview_check is None:
        return
    try:
        pair_row["semantic_multiview"] = run_semantic_multiview_check(
            apk_a=apk_a,
            apk_b=apk_b,
            decoded_a=decoded_a,
            decoded_b=decoded_b,
            app_a=pair_row.get("app_a"),
            app_b=pair_row.get("app_b"),
            feature_bundle_a=feature_bundle_a,
            feature_bundle_b=feature_bundle_b,
        )
    except Exception as exc:
        pair_row["semantic_multiview"] = {
            "profile_id": SEMANTIC_MULTIVIEW_PROFILE_ID,
            "status": "comparison_failed",
            "error": "{}: {}".format(type(exc).__name__, exc),
            "scores": {},
        }


def _compute_pair_row_with_caches(
    candidate: dict[str, Any],
    selected_layers: list[str],
    metric: str,
    threshold: float,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]],
    code_cache: dict[str, list],
    apk_discovery_cache: dict[str, str | None],
    feature_cache: Any | None = None,
) -> dict[str, Any]:
    """Compute a single pair_row using provided caches.

    This is the canonical body of `run_pairwise` per-candidate loop. It is
    called both from the sequential path (with shared caches) and from the
    isolated subprocess worker (with empty caches).
    """
    # EXEC-091-EXEC: ранний возврат для пар, помеченных сокращённым путём
    # на первичном отборе (shortcut_applied=True + signature_match=match).
    # Тяжёлые функции (resolve_apk_path, calculate_pair_scores — GED,
    # feature extraction и так далее) не вызываются.
    shortcut_start = time.perf_counter()
    if _should_skip_deep_verification(candidate):
        elapsed_ms_deep = int(round((time.perf_counter() - shortcut_start) * 1000))
        return _build_shortcut_pair_row(
            candidate=candidate,
            selected_layers=selected_layers,
            elapsed_ms_deep=elapsed_ms_deep,
        )

    deep_start = time.perf_counter()
    app_a_raw, app_b_raw = extract_apps(candidate)
    app_a = resolve_app_label(app_a_raw, "unknown_app_a")
    app_b = resolve_app_label(app_b_raw, "unknown_app_b")

    pair_row: dict[str, Any] = {
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "views_used": list(selected_layers),
        "signature_match": {"score": 0.0, "status": "missing"},
    }

    apk_a = None
    apk_b = None
    decoded_a = None
    decoded_b = None
    try:
        apk_a = resolve_apk_path(
            candidate=candidate,
            app=app_a_raw,
            side="a",
            app_label=app_a,
            discovery_cache=apk_discovery_cache,
        )
        apk_b = resolve_apk_path(
            candidate=candidate,
            app=app_b_raw,
            side="b",
            app_label=app_b,
            discovery_cache=apk_discovery_cache,
        )
        if not apk_a or not apk_b:
            raise PairwiseAnalysisError("missing_apk_path")

        decoded_a = resolve_decoded_dir(candidate, app_a_raw, "a")
        decoded_b = resolve_decoded_dir(candidate, app_b_raw, "b")

        score_kwargs = {
            "apk_a": apk_a,
            "apk_b": apk_b,
            "decoded_a": decoded_a,
            "decoded_b": decoded_b,
            "selected_layers": selected_layers,
            "metric": metric,
            "ins_block_sim_threshold": ins_block_sim_threshold,
            "ged_timeout_sec": ged_timeout_sec,
            "processes_count": processes_count,
            "threads_count": threads_count,
            "layer_cache": layer_cache,
            "code_cache": code_cache,
        }
        if feature_cache is not None:
            score_kwargs["feature_cache"] = feature_cache

        full_score, reduced_score, layers_used = calculate_pair_scores(**score_kwargs)

        decision_score = reduced_score
        status = "success" if decision_score >= threshold else "low_similarity"
        pair_row.update(
            {
                "full_similarity_score": float(full_score),
                "library_reduced_score": float(reduced_score),
                "status": status,
                "views_used": layers_used,
            }
        )
        if "code" in selected_layers and "resource" in selected_layers:
            try:
                pair_row.update(
                    build_code_stats_policy_fields_for_pair(
                        apk_a=apk_a,
                        apk_b=apk_b,
                        decoded_a=decoded_a,
                        decoded_b=decoded_b,
                        selected_layers=selected_layers,
                        layer_cache=layer_cache,
                        feature_cache=feature_cache,
                    )
                )
                pair_row.update(
                    build_framework_shift_evidence_fields(
                        decoded_a=decoded_a,
                        decoded_b=decoded_b,
                    )
                )
            except Exception as policy_error:
                pair_row.update(
                    {
                        "code_stats_containment_policy_id": (
                            CODE_STATS_CONTAINMENT_POLICY_ID
                        ),
                        "code_stats_containment_policy_applied": False,
                        "code_stats_containment_policy_error": str(policy_error),
                        "code_stats_added_code_policy_id": (
                            CODE_STATS_ADDED_CODE_POLICY_ID
                        ),
                        "code_stats_added_code_policy_applied": False,
                        "code_stats_added_code_policy_error": str(policy_error),
                        "c05_static_evidence_policy_id": (
                            C05_STATIC_EVIDENCE_POLICY_ID
                        ),
                        "c05_static_evidence_applied": False,
                        "c05_static_evidence_role": "evidence_only",
                        "c05_static_evidence_error": str(policy_error),
                        "code_core_evidence_policy_id": (
                            CODE_CORE_EVIDENCE_POLICY_ID
                        ),
                        "code_core_evidence_applied": False,
                        "code_core_evidence_role": "evidence_only",
                        "code_core_score_effect": "none",
                        "code_core_score_included": False,
                        "code_core_evidence_error": str(policy_error),
                        "added_code_direct_evidence_policy_id": (
                            ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID
                        ),
                        "added_code_direct_evidence_applied": False,
                        "added_code_direct_evidence_role": "evidence_only",
                        "added_code_direct_score_effect": "none",
                        "added_code_direct_score_included": False,
                        "added_code_direct_evidence_error": str(policy_error),
                        "deleted_code_direct_evidence_policy_id": (
                            DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID
                        ),
                        "deleted_code_direct_evidence_applied": False,
                        "deleted_code_direct_evidence_role": "evidence_only",
                        "deleted_code_direct_score_effect": "none",
                        "deleted_code_direct_score_included": False,
                        "deleted_code_direct_evidence_error": str(policy_error),
                        "framework_shift_evidence_policy_id": (
                            FRAMEWORK_SHIFT_EVIDENCE_POLICY_ID
                        ),
                        "framework_shift_evidence_applied": False,
                        "framework_shift_evidence_error": str(policy_error),
                    }
                )
        else:
            pair_row.update(
                {
                    "code_stats_containment_policy_id": CODE_STATS_CONTAINMENT_POLICY_ID,
                    "code_stats_containment_policy_applied": False,
                    "code_stats_added_code_policy_id": CODE_STATS_ADDED_CODE_POLICY_ID,
                    "code_stats_added_code_policy_applied": False,
                    "c05_static_evidence_policy_id": C05_STATIC_EVIDENCE_POLICY_ID,
                    "c05_static_evidence_applied": False,
                    "c05_static_evidence_role": "evidence_only",
                    "code_core_evidence_policy_id": CODE_CORE_EVIDENCE_POLICY_ID,
                    "code_core_evidence_applied": False,
                    "code_core_evidence_role": "evidence_only",
                    "code_core_score_effect": "none",
                    "code_core_score_included": False,
                    "added_code_direct_evidence_policy_id": (
                        ADDED_CODE_DIRECT_EVIDENCE_POLICY_ID
                    ),
                    "added_code_direct_evidence_applied": False,
                    "added_code_direct_evidence_role": "evidence_only",
                    "added_code_direct_score_effect": "none",
                    "added_code_direct_score_included": False,
                    "deleted_code_direct_evidence_policy_id": (
                        DELETED_CODE_DIRECT_EVIDENCE_POLICY_ID
                    ),
                    "deleted_code_direct_evidence_applied": False,
                    "deleted_code_direct_evidence_role": "evidence_only",
                    "deleted_code_direct_score_effect": "none",
                    "deleted_code_direct_score_included": False,
                    "framework_shift_evidence_policy_id": (
                        FRAMEWORK_SHIFT_EVIDENCE_POLICY_ID
                    ),
                    "framework_shift_evidence_applied": False,
                    "framework_shift_evidence_role": "evidence_only",
                }
            )
        apply_code_stats_score_policy(pair_row, threshold=threshold)
        apply_c05_static_score_policy(pair_row, threshold=threshold)
    except Exception:
        pair_row.update(
            {
                "full_similarity_score": None,
                "library_reduced_score": None,
                "status": "analysis_failed",
            }
        )

    _attach_semantic_multiview_check(
        pair_row=pair_row,
        apk_a=apk_a,
        apk_b=apk_b,
        decoded_a=decoded_a,
        decoded_b=decoded_b,
        feature_bundle_a=_get_cached_feature_bundle(apk_a, feature_cache),
        feature_bundle_b=_get_cached_feature_bundle(apk_b, feature_cache),
    )
    apply_semantic_multiview_score_policy(pair_row, threshold=threshold)
    pair_row["signature_match"] = collect_signature_match(apk_a, apk_b)
    if build_packaging_evidence_fields is not None:
        try:
            pair_row.update(build_packaging_evidence_fields(apk_a, apk_b))
        except Exception as packaging_error:
            pair_row.update(
                {
                    "packaging_evidence_policy_id": (
                        "R_apk_packaging_evidence_policy_v1"
                    ),
                    "packaging_evidence_role": "evidence_only",
                    "packaging_score_effect": "none",
                    "packaging_score_included": False,
                    "packaging_evidence_applied": False,
                    "packaging_evidence_error": str(packaging_error),
                }
            )
    pair_row["elapsed_ms_deep"] = int(round((time.perf_counter() - deep_start) * 1000))
    pair_row["evidence"] = collect_evidence_from_pairwise(pair_row)
    return pair_row


def _build_timeout_row(
    candidate: dict[str, Any],
    selected_layers: list[str],
    pair_timeout_sec: int,
    pair_id: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Build an incident pair_row for a pair that exceeded the hard timeout.

    Per D-2026-04-094, timeout is an incident, not a normal mode. The row
    preserves app labels (via extract_apps + resolve_app_label) and carries
    `analysis_failed_reason = "budget_exceeded"` plus a `timeout_info` block.
    `pair_id` and `duration_ms` are duplicated at top level because the
    incident registry consumes them directly for JSONL audit records.
    """
    try:
        app_a_raw, app_b_raw = extract_apps(candidate)
        app_a = resolve_app_label(app_a_raw, "unknown_app_a")
        app_b = resolve_app_label(app_b_raw, "unknown_app_b")
    except Exception:
        app_a = "unknown_app_a"
        app_b = "unknown_app_b"

    return {
        "pair_id": pair_id or candidate.get("pair_id"),
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "analysis_failed_reason": "budget_exceeded",
        "duration_ms": duration_ms,
        "views_used": list(selected_layers),
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "timeout_info": {
            "pair_timeout_sec": pair_timeout_sec,
            "duration_ms": duration_ms,
            "stage": "pairwise",
        },
    }


def _record_timeout_incident_safe(pair_row: dict[str, Any]) -> None:
    """Best-effort timeout incident logging; never fail the whole run."""
    if record_timeout_incident is None:
        return
    try:
        record_timeout_incident(pair_row)
    except Exception:
        pass


def _shutdown_executor(
    executor: Any,
    *,
    wait_for_workers: bool,
    cancel_futures: bool,
) -> None:
    """Shutdown helper tolerant to legacy test doubles without `.shutdown()`."""
    shutdown = getattr(executor, "shutdown", None)
    if not callable(shutdown):
        return
    try:
        shutdown(wait=wait_for_workers, cancel_futures=cancel_futures)
    except TypeError:
        shutdown(wait=wait_for_workers)


def _pair_worker_isolated(
    candidate_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    feature_cache_path_str: str | None = None,
) -> str:
    """Top-level worker for ProcessPoolExecutor (pickle-compatible).

    Computes a single pair_row with fresh empty caches and returns its
    JSON-serialized form. Imports happen inside the function to keep a
    clean subprocess environment.
    """
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    _project_root = _Path(__file__).resolve().parent.parent
    if str(_project_root) not in _sys.path:
        _sys.path.insert(0, str(_project_root))

    try:
        from script.pairwise_runner import (
            _compute_pair_row_with_caches as _compute,
            load_config as _load_config,
            parse_pairwise_stage as _parse_pairwise_stage,
        )
    except Exception:
        from pairwise_runner import (  # type: ignore[no-redef]
            _compute_pair_row_with_caches as _compute,
            load_config as _load_config,
            parse_pairwise_stage as _parse_pairwise_stage,
        )

    candidate = _json.loads(candidate_json)
    config = _load_config(_Path(config_path_str))
    selected_layers, metric, threshold = _parse_pairwise_stage(config)

    layer_cache: dict[tuple[str, str | None], dict[str, set[str]]] = {}
    code_cache: dict[str, list] = {}
    apk_discovery_cache: dict[str, str | None] = {}
    feature_cache = _open_feature_cache(feature_cache_path_str)
    try:
        row = _compute(
            candidate=candidate,
            selected_layers=selected_layers,
            metric=metric,
            threshold=threshold,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
            layer_cache=layer_cache,
            code_cache=code_cache,
            apk_discovery_cache=apk_discovery_cache,
            feature_cache=feature_cache,
        )
    finally:
        if feature_cache is not None:
            feature_cache.close()
    return _json.dumps(row)


def _build_worker_crash_row(
    candidate: dict[str, Any],
    selected_layers: list[str],
) -> dict[str, Any]:
    """EXEC-PAIRWISE-PARALLEL: pair_row для случая падения параллельного воркера.

    Если процесс-воркер упал (RuntimeError, MemoryError, BrokenProcessPool, и т.п.)
    до возврата результата, мы не можем молча проглотить ошибку — помечаем пару
    как ``status="analysis_failed"`` с ``analysis_failed_reason="worker_crashed"``
    и сохраняем метки приложений для аудита.
    """
    try:
        app_a_raw, app_b_raw = extract_apps(candidate)
        app_a = resolve_app_label(app_a_raw, "unknown_app_a")
        app_b = resolve_app_label(app_b_raw, "unknown_app_b")
    except Exception:
        app_a = "unknown_app_a"
        app_b = "unknown_app_b"

    return {
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "analysis_failed_reason": "worker_crashed",
        "views_used": list(selected_layers),
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
    }


def _run_single_pair_with_timeout(
    candidate: dict[str, Any],
    selected_layers: list[str],
    config_path: Path,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    pair_timeout_sec: int,
    pair_index: int = 0,
    feature_cache_path_str: str | None = None,
) -> dict[str, Any]:
    """EXEC-090: один pair_row в изолированном executor(max_workers=1).

    `pair_timeout_sec` — это wall-clock budget на ожидание результата future,
    а не cooperative interrupt внутри анализа APK. При истечении бюджета
    runner возвращает timeout-row сразу и выполняет
    `shutdown(wait=False, cancel_futures=True)`, чтобы не блокироваться на
    завершении executor. Уже запущенный worker может ещё короткое время жить
    в фоне; контракт здесь честный: мы перестали ждать и зафиксировали
    инцидент.
    """
    candidate_json = json.dumps(candidate)
    config_path_str = str(config_path)
    pair_id = resolve_pair_id(candidate, pair_index)
    executor = None
    timed_out = False
    started_at = time.perf_counter()
    try:
        with _process_pool_sysconf_workaround():
            executor = ProcessPoolExecutor(max_workers=1)
        future = executor.submit(
            _pair_worker_isolated,
            candidate_json,
            config_path_str,
            ins_block_sim_threshold,
            ged_timeout_sec,
            processes_count,
            threads_count,
            feature_cache_path_str,
        )
        result_json = future.result(timeout=pair_timeout_sec)
        return json.loads(result_json)
    except FuturesTimeoutError:
        timed_out = True
        duration_ms = int(round((time.perf_counter() - started_at) * 1000))
        pair_row = _build_timeout_row(
            candidate=candidate,
            selected_layers=selected_layers,
            pair_timeout_sec=pair_timeout_sec,
            pair_id=pair_id,
            duration_ms=duration_ms,
        )
        _record_timeout_incident_safe(pair_row)
        return pair_row
    except Exception:
        return _build_worker_crash_row(
            candidate=candidate,
            selected_layers=selected_layers,
        )
    finally:
        if executor is not None:
            if timed_out:
                _shutdown_executor(
                    executor,
                    wait_for_workers=False,
                    cancel_futures=True,
                )
            else:
                _shutdown_executor(
                    executor,
                    wait_for_workers=True,
                    cancel_futures=False,
                )


def run_pairwise(
    config_path: Path,
    enriched_path: Path,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    processes_count: int = 1,
    threads_count: int = 2,
    pair_timeout_sec: int | None = None,
    workers: int = 1,
    feature_cache_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """Запустить pairwise-этап по кандидатам из ``enriched_path``.

    EXEC-PAIRWISE-PARALLEL: параметр ``workers`` управляет параллельной
    обработкой пар.

    - ``workers=1`` (по умолчанию) — последовательное поведение, полностью
      совместимо с прежним интерфейсом.
    - ``workers>1`` — каждая пара выполняется в ``ProcessPoolExecutor``
      (``max_workers=workers``). Порядок результатов сохраняется таким же,
      как при ``workers=1``.

    Shortcut-пары (EXEC-091-EXEC: ``shortcut_applied=True`` +
    ``signature_match.status=="match"``) никогда не отправляются в пул —
    они возвращаются сразу, в том же процессе, чтобы не тратить ресурс
    пула на дешёвые операции.

    Ошибки воркера (``RuntimeError``, ``MemoryError`` и прочие исключения
    процесса) не глотаются: соответствующий ``pair_row`` помечается
    ``status="analysis_failed"`` и ``analysis_failed_reason="worker_crashed"``.

    `pair_timeout_sec`, если это положительное целое, задаёт честный контракт
    ожидания future после submit. Это не signal внутрь worker-кода и не
    гарантия мгновенного убийства уже запущенного процесса/потока. Контракт
    такой:
    - timeout => `pair_row` с `analysis_failed_reason="budget_exceeded"` и
      incident registry;
    - `workers=1` => отдельный executor на пару завершается через
      `shutdown(wait=False, cancel_futures=True)`;
    - `workers>1` => как только одна или несколько submitted-пар исчерпали
      бюджет ожидания, runner сохраняет уже готовые результаты, помечает все
      ещё не завершившиеся пары timeout-инцидентами и рвёт текущий executor
      через `shutdown(wait=False, cancel_futures=True)`, чтобы не зависнуть на
      `__exit__`.
    """
    use_main_feature_cache = (
        feature_cache_path is not None or os.environ.get("FEATURE_CACHE_PATH") is not None
    )
    with _feature_cache_path_override(feature_cache_path):
        if os.environ.get("SIMILARITY_SKIP_REQ_CHECK") != "1":
            verify_required_dependencies()

        config = load_config(config_path)
        selected_layers, metric, threshold = parse_pairwise_stage(config)
        candidates = load_enriched_candidates(enriched_path)
        resolved_feature_cache_path = str(_resolve_feature_cache_path(feature_cache_path))

        layer_cache: dict[tuple[str, str | None], dict[str, set[str]]] = {}
        code_cache: dict[str, list] = {}
        apk_discovery_cache: dict[str, str | None] = {}

        use_hard_timeout = isinstance(pair_timeout_sec, int) and pair_timeout_sec > 0
        use_parallel = isinstance(workers, int) and workers > 1 and len(candidates) > 1

        def run_one_sequential(index: int, candidate: dict[str, Any]) -> dict[str, Any]:
            """Один pair_row в основном процессе (workers=1 или < 2 кандидатов)."""
            if _should_skip_deep_verification(candidate):
                return _compute_pair_row_with_caches(
                    candidate=candidate,
                    selected_layers=selected_layers,
                    metric=metric,
                    threshold=threshold,
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec,
                    processes_count=processes_count,
                    threads_count=threads_count,
                    layer_cache=layer_cache,
                    code_cache=code_cache,
                    apk_discovery_cache=apk_discovery_cache,
                    feature_cache=None,
                )
            if use_hard_timeout:
                return _run_single_pair_with_timeout(
                    candidate=candidate,
                    selected_layers=selected_layers,
                    config_path=config_path,
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec,
                    processes_count=processes_count,
                    threads_count=threads_count,
                    pair_timeout_sec=pair_timeout_sec,
                    pair_index=index,
                    feature_cache_path_str=resolved_feature_cache_path,
                )
            feature_cache = (
                _open_feature_cache(resolved_feature_cache_path)
                if use_main_feature_cache
                else None
            )
            try:
                return _compute_pair_row_with_caches(
                    candidate=candidate,
                    selected_layers=selected_layers,
                    metric=metric,
                    threshold=threshold,
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec,
                    processes_count=processes_count,
                    threads_count=threads_count,
                    layer_cache=layer_cache,
                    code_cache=code_cache,
                    apk_discovery_cache=apk_discovery_cache,
                    feature_cache=feature_cache,
                )
            finally:
                if feature_cache is not None:
                    feature_cache.close()

        # workers=1 — полностью прежнее последовательное поведение.
        if not use_parallel:
            results: list[dict[str, Any]] = []
            for index, candidate in enumerate(candidates):
                results.append(run_one_sequential(index, candidate))
            return results

        # workers>1 — параллельный путь. Shortcut-пары отделяем и считаем сразу,
        # тяжёлые пары отправляем в ProcessPoolExecutor. Порядок результатов
        # восстанавливается по исходному индексу кандидата.
        results_by_index: dict[int, dict[str, Any]] = {}
        heavy_indices: list[int] = []

        for index, candidate in enumerate(candidates):
            if _should_skip_deep_verification(candidate):
                # EXEC-091-EXEC: shortcut-пара не уходит в пул — считаем в основном
                # процессе теми же функциями, что и при workers=1.
                results_by_index[index] = run_one_sequential(index, candidate)
            else:
                heavy_indices.append(index)

        if not heavy_indices:
            return [results_by_index[i] for i in range(len(candidates))]

    if heavy_indices:
        config_path_str = str(config_path)
        executor = None
        timed_out = False
        future_to_index: dict[Any, int] = {}
        submitted_at: dict[Any, float] = {}
        try:
            with _process_pool_sysconf_workaround():
                executor = _make_parallel_executor(max_workers=workers)

            for index in heavy_indices:
                candidate = candidates[index]
                candidate_json = json.dumps(candidate)
                future = executor.submit(
                    _pair_worker_isolated,
                    candidate_json,
                    config_path_str,
                    ins_block_sim_threshold,
                    ged_timeout_sec,
                    processes_count,
                    threads_count,
                    resolved_feature_cache_path,
                )
                future_to_index[future] = index
                submitted_at[future] = time.perf_counter()

            pending = set(future_to_index)
            if use_hard_timeout:
                poll_timeout_sec = min(max(float(pair_timeout_sec) / 10.0, 0.05), 0.25)
                while pending:
                    done, not_done = wait(
                        pending,
                        timeout=poll_timeout_sec,
                        return_when=FIRST_COMPLETED,
                    )

                    for future in done:
                        index = future_to_index[future]
                        candidate = candidates[index]
                        try:
                            result_json = future.result()
                            results_by_index[index] = json.loads(result_json)
                        except Exception:
                            results_by_index[index] = _build_worker_crash_row(
                                candidate=candidate,
                                selected_layers=selected_layers,
                            )

                    if not not_done:
                        break

                    now = time.perf_counter()
                    overdue = [
                        future
                        for future in not_done
                        if (now - submitted_at[future]) >= pair_timeout_sec
                    ]
                    if overdue:
                        timed_out = True
                        timeout_marked_at = time.perf_counter()
                        for future in not_done:
                            index = future_to_index[future]
                            candidate = candidates[index]
                            duration_ms = int(
                                round((timeout_marked_at - submitted_at[future]) * 1000)
                            )
                            timeout_row = _build_timeout_row(
                                candidate=candidate,
                                selected_layers=selected_layers,
                                pair_timeout_sec=pair_timeout_sec,
                                pair_id=resolve_pair_id(candidate, index),
                                duration_ms=duration_ms,
                            )
                            _record_timeout_incident_safe(timeout_row)
                            results_by_index[index] = timeout_row
                        break

                    pending = set(not_done)
            else:
                for future, index in future_to_index.items():
                    candidate = candidates[index]
                    try:
                        result_json = future.result()
                        results_by_index[index] = json.loads(result_json)
                    except Exception:
                        # RuntimeError, MemoryError, BrokenProcessPool, и любые
                        # другие отказы воркера — не глотаем, а помечаем пару
                        # worker_crashed, как требует EXEC-PAIRWISE-PARALLEL.
                        results_by_index[index] = _build_worker_crash_row(
                            candidate=candidate,
                            selected_layers=selected_layers,
                        )
        finally:
            if executor is not None:
                if timed_out:
                    _shutdown_executor(
                        executor,
                        wait_for_workers=False,
                        cancel_futures=True,
                    )
                else:
                    _shutdown_executor(
                        executor,
                        wait_for_workers=True,
                        cancel_futures=False,
                    )

        return [results_by_index[i] for i in range(len(candidates))]


def resolve_pair_id(candidate: dict[str, Any], index: int) -> str:
    value = candidate.get("pair_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "PAIR-{:06d}".format(index + 1)


def build_app_contract(app: Any, label: str) -> dict[str, Any]:
    payload = {"app_id": label}
    if isinstance(app, dict):
        apk_path = extract_path_from_app(app)
        decoded_dir = extract_decoded_dir_from_app(app)
        if apk_path:
            payload["apk_path"] = apk_path
        if decoded_dir:
            payload["decoded_dir"] = decoded_dir
    return payload


def normalize_detailed_analysis_status(summary_row: dict[str, Any]) -> str:
    status = summary_row.get("status")
    if status == "analysis_failed":
        return "analysis_failed"
    return "success"


def infer_failure_reason(
    candidate: dict[str, Any],
    app_a_raw: Any,
    app_b_raw: Any,
    selected_layers: list[str],
    analysis_status: str,
) -> str | None:
    if analysis_status != "analysis_failed":
        return None

    explicit = candidate.get("failure_reason")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    requires_decoded = any(layer in DECODE_REQUIRED_LAYERS for layer in selected_layers)
    if requires_decoded:
        decoded_a = resolve_decoded_dir(candidate, app_a_raw, "a")
        decoded_b = resolve_decoded_dir(candidate, app_b_raw, "b")
        if not decoded_a or not decoded_b:
            return "view_build_failed"

    return "internal_pipeline_error"


def build_detailed_scores(summary_row: dict[str, Any], analysis_status: str) -> dict[str, Any]:
    if analysis_status == "analysis_failed":
        return {
            "similarity_score": None,
            "full_similarity_score": None,
            "library_reduced_score": None,
            "selected_similarity_score": None,
            "similarity_score_source": "analysis_failed",
            "library_reduced_status": "not_applicable",
            "failure_similarity_semantics": "undefined_not_zero",
        }

    full_score = summary_row.get("full_similarity_score")
    reduced_score = summary_row.get("library_reduced_score")
    explicit_score = summary_row.get("similarity_score")
    if explicit_score is not None:
        selected_score = explicit_score
        score_source = str(summary_row.get("similarity_score_source") or "similarity_score")
    elif reduced_score is not None:
        selected_score = reduced_score
        score_source = "library_reduced_score"
    else:
        selected_score = full_score
        score_source = "full_similarity_score"
    return {
        "similarity_score": selected_score,
        "full_similarity_score": full_score,
        "library_reduced_score": reduced_score,
        "selected_similarity_score": selected_score,
        "similarity_score_source": score_source,
        "library_reduced_status": "computed" if reduced_score is not None else "not_computed",
        "failure_similarity_semantics": None,
    }


def build_detailed_views(
    selected_layers: list[str],
    views_used: list[str],
    analysis_status: str,
    failure_reason: str | None,
) -> dict[str, Any]:
    canonical_views = ("code", "api", "component", "resource", "library", "cfg_ged")
    selected = set(selected_layers)
    used = set(views_used)
    views: dict[str, Any] = {}

    for view in canonical_views:
        if view not in selected and view != "cfg_ged":
            views[view] = {
                "view_status": "not_requested",
                "warnings": [],
                "errors": [],
            }
            continue

        if view == "cfg_ged" and "code" not in selected:
            views[view] = {
                "view_status": "not_requested",
                "warnings": [],
                "errors": [],
            }
            continue

        if analysis_status == "analysis_failed":
            errors = []
            if failure_reason == "view_build_failed" and view in DECODE_REQUIRED_LAYERS:
                errors.append("missing_decoded_dir")
            elif failure_reason:
                errors.append(failure_reason)
            views[view] = {
                "view_status": "failed" if view in selected else "not_requested",
                "warnings": [],
                "errors": errors,
            }
            continue

        view_status = "success" if view in used else "not_requested"
        if view == "cfg_ged":
            view_status = "success" if "code" in used else "not_requested"
        views[view] = {
            "view_status": view_status,
            "warnings": [],
            "errors": [],
        }

    return views


def _resource_change_total(summary: dict[str, Any]) -> int:
    total = 0
    for key in ("modified_count", "added_count", "removed_count"):
        try:
            total += int(summary.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _resource_change_hint(pair_row: dict[str, Any]) -> dict[str, Any] | None:
    summary = pair_row.get("resource_change_summary")
    if not isinstance(summary, dict) or _resource_change_total(summary) <= 0:
        return None

    supporting_score_field = None
    supporting_score = None
    for field in ("code_policy_score", "code_multiview_score", "code_similarity_score"):
        value = pair_row.get(field)
        if value is None:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score >= 0.70:
            supporting_score_field = field
            supporting_score = score
            break

    if supporting_score_field is None:
        return None

    return {
        "hint_type": "ResourceChangeWithCodeSupport",
        "severity": "medium",
        "summary": "resource files changed while code evidence still supports application relation",
        "resource_change_summary": dict(summary),
        "supporting_score_field": supporting_score_field,
        "supporting_score": supporting_score,
    }


def build_detailed_explanation(
    scores: dict[str, Any],
    analysis_status: str,
    pair_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_score = scores.get("full_similarity_score")
    reduced_score = scores.get("library_reduced_score")
    library_impact_flag = False
    if full_score is not None and reduced_score is not None:
        library_impact_flag = bool(abs(float(full_score) - float(reduced_score)) >= 0.05)

    hints = []
    if analysis_status != "analysis_failed" and isinstance(pair_row, dict):
        resource_hint = _resource_change_hint(pair_row)
        if resource_hint is not None:
            hints.append(resource_hint)

    return {
        "explanation_status": "available" if hints else "not_available",
        "hint_count": len(hints),
        "top_hint_types": [str(hint.get("hint_type")) for hint in hints[:3]],
        "hints": hints,
        "library_impact_flag": library_impact_flag if analysis_status != "analysis_failed" else False,
    }


def build_detailed_result(
    candidate: dict[str, Any],
    summary_row: dict[str, Any],
    selected_layers: list[str],
    metric: str,
    threshold: float,
    config_path: Path,
    enriched_path: Path,
    index: int,
) -> dict[str, Any]:
    app_a_raw, app_b_raw = extract_apps(candidate)
    app_a_label = resolve_app_label(app_a_raw, "unknown_app_a")
    app_b_label = resolve_app_label(app_b_raw, "unknown_app_b")
    pair_id = resolve_pair_id(candidate, index)
    representation_mode = str(candidate.get("representation_mode") or "R_multiview_partial")
    analysis_status = normalize_detailed_analysis_status(summary_row)
    failure_reason = infer_failure_reason(
        candidate=candidate,
        app_a_raw=app_a_raw,
        app_b_raw=app_b_raw,
        selected_layers=selected_layers,
        analysis_status=analysis_status,
    )
    scores = build_detailed_scores(summary_row, analysis_status)
    views_used = summary_row.get("views_used")
    if not isinstance(views_used, list):
        views_used = []

    return {
        "pair_id": pair_id,
        "apps": {
            "app_a": build_app_contract(app_a_raw, app_a_label),
            "app_b": build_app_contract(app_b_raw, app_b_label),
        },
        "analysis_status": analysis_status,
        "failure_reason": failure_reason,
        "representation_mode": representation_mode,
        "views": build_detailed_views(
            selected_layers=selected_layers,
            views_used=[str(view) for view in views_used],
            analysis_status=analysis_status,
            failure_reason=failure_reason,
        ),
        "scores": scores,
        "explanation": build_detailed_explanation(scores, analysis_status, summary_row),
        "artifacts": {
            "artifacts_path": candidate.get("artifacts_path") or "pairwise://{}".format(pair_id),
            "enriched_candidates_ref": str(enriched_path),
            "candidate_list_row_ref": candidate.get("candidate_list_row_ref"),
            "screening_explanation_ref": candidate.get("screening_explanation_ref"),
            "noise_summary_ref": candidate.get("noise_summary_ref"),
            "noise_profile_ref": candidate.get("noise_profile_ref"),
            "deepening_artifact_refs": candidate.get("deepening_artifact_refs") or [],
        },
        "run_context": {
            "dataset_id": candidate.get("dataset_id"),
            "prototype_id": candidate.get("prototype_id"),
            "prototype_sha": candidate.get("prototype_sha"),
            "representation_mode": representation_mode,
            "config_ref": str(config_path),
            "pairwise_config": {
                "features": list(selected_layers),
                "metric": metric,
                "threshold": threshold,
            },
        },
    }


def run_pairwise_detailed(
    config_path: Path,
    enriched_path: Path,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    processes_count: int = 1,
    threads_count: int = 2,
) -> list[dict[str, Any]]:
    config = load_config(config_path)
    selected_layers, metric, threshold = parse_pairwise_stage(config)
    candidates = load_enriched_candidates(enriched_path)
    summary_rows = run_pairwise(
        config_path=config_path,
        enriched_path=enriched_path,
        ins_block_sim_threshold=ins_block_sim_threshold,
        ged_timeout_sec=ged_timeout_sec,
        processes_count=processes_count,
        threads_count=threads_count,
    )

    detailed = []
    for index, (candidate, summary_row) in enumerate(zip(candidates, summary_rows)):
        detailed.append(
            build_detailed_result(
                candidate=candidate,
                summary_row=summary_row,
                selected_layers=selected_layers,
                metric=metric,
                threshold=threshold,
                config_path=config_path,
                enriched_path=enriched_path,
                index=index,
            )
        )
    return detailed


_DETAILED_JSON_REQUIRED_FIELDS: tuple[str, ...] = (
    "app_a",
    "app_b",
    "status",
    "analysis_failed_reason",
    "similarity_score",
    "full_similarity_score",
    "library_reduced_score",
    "views_used",
    "signature_match",
    "evidence",
    "timeout_info",
)


def _nested_score_value(pair_row: dict[str, Any], field: str) -> Any:
    value = pair_row.get(field)
    if value is not None:
        return value
    nested_scores = pair_row.get("scores")
    if isinstance(nested_scores, dict):
        return nested_scores.get(field)
    return None


def _apply_deep_m2_score_decision(item: dict[str, Any], item_status: str) -> None:
    if item_status == "analysis_failed":
        item["similarity_score"] = None
        item["full_similarity_score"] = None
        item["library_reduced_score"] = None
        item["selected_similarity_score"] = None
        item["similarity_score_source"] = "analysis_failed"
        item["library_reduced_status"] = "not_applicable"
        item["failure_similarity_semantics"] = "undefined_not_zero"
    else:
        full_score = item.get("full_similarity_score")
        reduced_score = item.get("library_reduced_score")
        explicit_score = item.get("similarity_score")
        if explicit_score is not None:
            selected_score = explicit_score
            score_source = str(item.get("similarity_score_source") or "similarity_score")
        elif reduced_score is not None:
            selected_score = reduced_score
            score_source = "library_reduced_score"
        else:
            selected_score = full_score
            score_source = "full_similarity_score"

        item["similarity_score"] = selected_score
        item["selected_similarity_score"] = selected_score
        item["similarity_score_source"] = score_source
        item["library_reduced_status"] = (
            "computed" if reduced_score is not None else "not_computed"
        )
        item["failure_similarity_semantics"] = None

    item["score_decision_policy_id"] = DEEP_M2_SCORE_DECISION_POLICY_ID
    item["packaging_evidence_role"] = "evidence_only"
    item["packaging_score_included"] = False


def _build_detailed_json_item(pair_row: dict[str, Any], index: int) -> dict[str, Any]:
    """Shape a single pair_row into a DEEP-004 detailed JSON item.

    Guarantees:
      - required fields always present (None-filled when absent in pair_row);
      - pair_id is stable sequential "PAIR-{index+1:06d}" unless pair_row
        already carries a non-empty str pair_id;
      - any extra fields from pair_row are preserved verbatim (forward-compat).
    """
    item: dict[str, Any] = {}
    existing_pair_id = pair_row.get("pair_id") if isinstance(pair_row, dict) else None
    if isinstance(existing_pair_id, str) and existing_pair_id.strip():
        item["pair_id"] = existing_pair_id.strip()
    else:
        item["pair_id"] = "PAIR-{:06d}".format(index + 1)

    for field in _DETAILED_JSON_REQUIRED_FIELDS:
        item[field] = pair_row.get(field) if isinstance(pair_row, dict) else None

    # Preserve any additional fields without loss.
    if isinstance(pair_row, dict):
        for key, value in pair_row.items():
            if key in item:
                continue
            if key == "pair_id":
                continue
            item[key] = value

    item["schema_version"] = DETAILED_JSON_SCHEMA_VERSION
    views_used = item.get("views_used")
    if not isinstance(views_used, list):
        views_used = []
    item_status = str(item.get("status") or "analysis_failed")
    for score_field in (
        "similarity_score",
        "full_similarity_score",
        "library_reduced_score",
    ):
        item[score_field] = _nested_score_value(item, score_field)
    _apply_deep_m2_score_decision(item, item_status)
    scores = {
        "similarity_score": item.get("similarity_score"),
        "full_similarity_score": item.get("full_similarity_score"),
        "library_reduced_score": item.get("library_reduced_score"),
        "selected_similarity_score": item.get("selected_similarity_score"),
        "similarity_score_source": item.get("similarity_score_source"),
        "library_reduced_status": item.get("library_reduced_status"),
    }
    if not isinstance(item.get("explanation"), dict):
        item["explanation"] = build_detailed_explanation(scores, item_status, item)
    content_check_run = build_pair_check_run(
        pair_id=item["pair_id"],
        check_id="set_based_multiview_similarity",
        status=item_status,
        duration_ms=item.get("elapsed_ms_deep") or item.get("duration_ms"),
        inputs={
            "app_a": item.get("app_a"),
            "app_b": item.get("app_b"),
            "views_used": list(views_used),
        },
        outputs=scores,
        profile_ref=item.get("profile_ref"),
        representation_spec_ref=item.get("representation_spec_ref"),
        representation_spec_hash=item.get("representation_spec_hash"),
        view_schema_versions=item.get("view_schema_versions"),
    )
    signature_match = item.get("signature_match")
    if not isinstance(signature_match, dict):
        signature_match = {}
    signature_status = str(signature_match.get("status") or "unknown")
    normalized_signature_status = signature_status.strip().lower()
    if normalized_signature_status in {"match", "mismatch"}:
        signature_check_status = "success"
    elif normalized_signature_status in {"analysis_failed", "failed", "error"}:
        signature_check_status = "analysis_failed"
    else:
        signature_check_status = "partial_result"
    signature_check_run = build_pair_check_run(
        pair_id=item["pair_id"],
        check_id="apk_signature_match",
        status=signature_check_status,
        duration_ms=None,
        inputs={
            "app_a": item.get("app_a"),
            "app_b": item.get("app_b"),
        },
        outputs={
            "signature_status": signature_status,
            "signature_score": signature_match.get("score"),
            "signature_message": signature_match.get("message"),
        },
        profile_ref=item.get("profile_ref"),
        representation_spec_ref=item.get("representation_spec_ref"),
        representation_spec_hash=item.get("representation_spec_hash"),
        view_schema_versions=item.get("view_schema_versions"),
    )
    pair_check_runs = [content_check_run]
    semantic_multiview = item.get("semantic_multiview")
    if isinstance(semantic_multiview, dict):
        semantic_inputs = semantic_multiview.get("inputs")
        if not isinstance(semantic_inputs, dict):
            semantic_inputs = {
                "app_a": item.get("app_a"),
                "app_b": item.get("app_b"),
            }
        semantic_status = str(semantic_multiview.get("status") or "partial_result")
        pair_check_runs.append(
            build_pair_check_run(
                pair_id=item["pair_id"],
                check_id="semantic_multiview_similarity",
                status=semantic_status,
                duration_ms=semantic_multiview.get("duration_ms"),
                inputs=semantic_inputs,
                outputs=semantic_multiview,
                profile_ref=(
                    semantic_multiview.get("profile_id")
                    or item.get("profile_ref")
                    or SEMANTIC_MULTIVIEW_PROFILE_ID
                ),
                representation_spec_ref=item.get("representation_spec_ref"),
                representation_spec_hash=item.get("representation_spec_hash"),
                view_schema_versions=(
                    semantic_multiview.get("view_schema_versions")
                    or item.get("view_schema_versions")
                ),
            )
        )
    pair_check_runs.append(signature_check_run)
    incoming_aggregation_policy = item.get("aggregation_policy")
    if isinstance(incoming_aggregation_policy, dict):
        aggregation_policy = incoming_aggregation_policy
    else:
        aggregation_policy = build_pair_aggregation_policy(
            policy_id=DEEP_M2_SCORE_DECISION_POLICY_ID,
            strategy="select_content_similarity_score_with_evidence_guards",
            weights={"similarity_score": 1.0},
            selected_score_field="similarity_score",
            limitations=list(DEEP_M2_SCORE_DECISION_LIMITATIONS),
        )
    item["pair_check_runs"] = pair_check_runs
    item["aggregation_policy"] = aggregation_policy
    pair_similarity_result = build_pair_similarity_result(item, pair_id=item["pair_id"])
    item["pair_evidence_record"] = pair_similarity_result["evidence_record"]
    item["compatibility_check"] = pair_similarity_result["compatibility_check"]
    item["pair_similarity_result"] = pair_similarity_result
    return item


def export_pairwise_detailed_json(results: list[dict], output_path: Path) -> None:
    """Export DEEP-004 detailed JSON report for machine-readable audit.

    Top-level object shape (schema_version = "deep-004-v1"):
        {
          "schema_version": "deep-004-v1",
          "total_pairs": int,
          "generated_at": "<ISO-8601 UTC>",
          "pairs": [<detailed pair item>, ...]
        }

    Each detailed pair item preserves every field of the source pair_row
    and adds a stable sequential `pair_id` plus per-item `schema_version`.
    No field from pair_row is dropped (forward-compat with future extensions).
    """
    if not isinstance(results, list):
        raise TypeError("export_pairwise_detailed_json: results must be a list.")

    items: list[dict[str, Any]] = []
    for index, row in enumerate(results):
        if not isinstance(row, dict):
            raise TypeError(
                "export_pairwise_detailed_json: pair_row at index {} is not a dict.".format(index)
            )
        items.append(_build_detailed_json_item(row, index))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "schema_version": DETAILED_JSON_SCHEMA_VERSION,
        "total_pairs": len(items),
        "generated_at": generated_at,
        "pairs": items,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    # SYS-INT-16-VERIFY-DEPS-WIRE: fail-fast при отсутствии обязательных
    # зависимостей similarity-системы. Дублирует проверку внутри run_pairwise
    # намеренно — явный вызов в main() документирует контракт точки входа
    # и ловит ошибку до парсинга CLI-аргументов.
    if os.environ.get("SIMILARITY_SKIP_REQ_CHECK") != "1":
        verify_required_dependencies()

    args = parse_args()
    payload = run_pairwise(
        config_path=Path(args.config),
        enriched_path=Path(args.enriched),
        ins_block_sim_threshold=args.ins_block_sim_threshold,
        ged_timeout_sec=args.ged_timeout_sec,
        processes_count=args.processes_count,
        threads_count=args.threads_count,
        feature_cache_path=args.feature_cache_path,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.detailed_output:
        export_pairwise_detailed_json(
            results=payload,
            output_path=Path(args.detailed_output),
        )


if __name__ == "__main__":
    main()

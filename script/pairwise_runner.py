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
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
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


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FEATURE_CACHE_PATH = PROJECT_ROOT / "experiments" / "artifacts" / ".feature_cache.sqlite"
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
except Exception:
    try:
        from m_static_views import extract_all_features
    except Exception:
        extract_all_features = None

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

    requires_decoded = any(layer in DECODE_REQUIRED_LAYERS for layer in selected_layers)
    if requires_decoded and not decoded_dir:
        raise PairwiseAnalysisError("missing_decoded_dir")

    feature_bundle = None
    apk_sha256 = None
    if feature_cache is not None:
        apk_sha256 = _sha256_of_file(apk_file)
        feature_bundle = feature_cache.get(apk_sha256)

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
            feature_cache.set(apk_sha256, feature_bundle)

    layers = {
        "code": stringify_tokens(feature_bundle.get("code", set())),
        "metadata": stringify_tokens(feature_bundle.get("metadata", set())),
        "component": flatten_component_features(feature_bundle.get("component", {})),
        "resource": flatten_resource_features(feature_bundle.get("resource", {})),
        "library": flatten_library_features(feature_bundle.get("library", {})),
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

    reduced_layers = [layer for layer in selected_layers if layer != "library"]
    if reduced_layers:
        reduced_left = aggregate_features(layers_a, reduced_layers)
        reduced_right = aggregate_features(layers_b, reduced_layers)
        library_reduced_score = calculate_set_metric(metric, reduced_left, reduced_right)
    else:
        library_reduced_score = 0.0
    return float(full_similarity_score), float(library_reduced_score)


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
    """
    app_a_raw, app_b_raw = extract_apps(candidate)
    app_a = resolve_app_label(app_a_raw, "unknown_app_a")
    app_b = resolve_app_label(app_b_raw, "unknown_app_b")

    signature_match = candidate.get("signature_match")
    if not isinstance(signature_match, dict):
        signature_match = {"score": 0.0, "status": "missing"}

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
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "success_shortcut",
        "views_used": list(selected_layers),
        "signature_match": dict(signature_match),
    }
    pair_row["evidence"] = collect_evidence_from_pairwise(pair_row)
    return pair_row


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

        full_score, reduced_score, layers_used = calculate_pair_scores(
            apk_a=apk_a,
            apk_b=apk_b,
            decoded_a=decoded_a,
            decoded_b=decoded_b,
            selected_layers=selected_layers,
            metric=metric,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
            layer_cache=layer_cache,
            code_cache=code_cache,
            feature_cache=feature_cache,
        )

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
    except Exception:
        pair_row.update(
            {
                "full_similarity_score": None,
                "library_reduced_score": None,
                "status": "analysis_failed",
            }
        )

    pair_row["signature_match"] = collect_signature_match(apk_a, apk_b)
    pair_row["elapsed_ms_deep"] = int(round((time.perf_counter() - deep_start) * 1000))
    pair_row["evidence"] = collect_evidence_from_pairwise(pair_row)
    return pair_row


def _build_timeout_row(
    candidate: dict[str, Any],
    selected_layers: list[str],
    pair_timeout_sec: int,
) -> dict[str, Any]:
    """Build an incident pair_row for a pair that exceeded the hard timeout.

    Per D-2026-04-094, timeout is an incident, not a normal mode. The row
    preserves app labels (via extract_apps + resolve_app_label) and carries
    `analysis_failed_reason = "budget_exceeded"` plus a `timeout_info` block.
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
        "analysis_failed_reason": "budget_exceeded",
        "views_used": list(selected_layers),
        "signature_match": {"score": 0.0, "status": "missing"},
        "evidence": [],
        "timeout_info": {
            "pair_timeout_sec": pair_timeout_sec,
            "stage": "pairwise",
        },
    }


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
    feature_cache_path_str: str | None = None,
) -> dict[str, Any]:
    """EXEC-090: один pair_row в изолированном ProcessPoolExecutor(max_workers=1)
    с жёстким таймаутом. Таймаут => budget_exceeded; другая ошибка воркера =>
    worker_crashed (см. _build_worker_crash_row).
    """
    candidate_json = json.dumps(candidate)
    config_path_str = str(config_path)
    try:
        with _process_pool_sysconf_workaround(), ProcessPoolExecutor(max_workers=1) as executor:
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
        pair_row = _build_timeout_row(
            candidate=candidate,
            selected_layers=selected_layers,
            pair_timeout_sec=pair_timeout_sec,
        )
        # D-2026-04-094: каждый таймаут — инцидент, не штатный режим.
        # Журнал инцидентов отделён от pair_row и не должен валить
        # прогон при ошибке записи (disk full, permission, и т.п.).
        if record_timeout_incident is not None:
            try:
                record_timeout_incident(pair_row)
            except Exception:
                pass
        return pair_row
    except Exception:
        return _build_worker_crash_row(
            candidate=candidate,
            selected_layers=selected_layers,
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
    """
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

        def run_one_sequential(candidate: dict[str, Any]) -> dict[str, Any]:
            """Один pair_row в основном процессе (workers=1 или < 2 кандидатов)."""
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
                    feature_cache_path_str=resolved_feature_cache_path,
                )
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
            )

        # workers=1 — полностью прежнее последовательное поведение.
        if not use_parallel:
            results: list[dict[str, Any]] = []
            for candidate in candidates:
                results.append(run_one_sequential(candidate))
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
                results_by_index[index] = _compute_pair_row_with_caches(
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
                )
            else:
                heavy_indices.append(index)

        if not heavy_indices:
            return [results_by_index[i] for i in range(len(candidates))]

    if heavy_indices:
        config_path_str = str(config_path)
        with _process_pool_sysconf_workaround(), _make_parallel_executor(max_workers=workers) as executor:
            future_to_index: dict[Any, int] = {}
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

            for future, index in future_to_index.items():
                candidate = candidates[index]
                try:
                    if use_hard_timeout:
                        result_json = future.result(timeout=pair_timeout_sec)
                    else:
                        result_json = future.result()
                    results_by_index[index] = json.loads(result_json)
                except FuturesTimeoutError:
                    timeout_row = _build_timeout_row(
                        candidate=candidate,
                        selected_layers=selected_layers,
                        pair_timeout_sec=pair_timeout_sec,
                    )
                    if record_timeout_incident is not None:
                        try:
                            record_timeout_incident(timeout_row)
                        except Exception:
                            pass
                    results_by_index[index] = timeout_row
                except Exception:
                    # RuntimeError, MemoryError, BrokenProcessPool, и любые
                    # другие отказы воркера — не глотаем, а помечаем пару
                    # worker_crashed, как требует EXEC-PAIRWISE-PARALLEL.
                    results_by_index[index] = _build_worker_crash_row(
                        candidate=candidate,
                        selected_layers=selected_layers,
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
        }

    full_score = summary_row.get("full_similarity_score")
    reduced_score = summary_row.get("library_reduced_score")
    selected_score = reduced_score if reduced_score is not None else full_score
    return {
        "similarity_score": selected_score,
        "full_similarity_score": full_score,
        "library_reduced_score": reduced_score,
        "selected_similarity_score": selected_score,
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


def build_detailed_explanation(scores: dict[str, Any], analysis_status: str) -> dict[str, Any]:
    full_score = scores.get("full_similarity_score")
    reduced_score = scores.get("library_reduced_score")
    library_impact_flag = False
    if full_score is not None and reduced_score is not None:
        library_impact_flag = bool(abs(float(full_score) - float(reduced_score)) >= 0.05)

    return {
        "explanation_status": "not_available",
        "hint_count": 0,
        "top_hint_types": [],
        "hints": [],
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
        "explanation": build_detailed_explanation(scores, analysis_status),
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
    "full_similarity_score",
    "library_reduced_score",
    "views_used",
    "signature_match",
    "evidence",
    "timeout_info",
)


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

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script.system_requirements import verify_required_dependencies
from script.calculate_apks_similarity.build_model import build_model

try:
    from script.m_static_views import extract_all_features
except Exception:
    try:
        from m_static_views import extract_all_features
    except Exception:
        extract_all_features = None

try:
    from script.shared_data_store import discover_apk_by_stem
    from script.shared_data_store import discover_decoded_dir_by_stem
    from script.shared_data_store import resolve_path_ref
    from script.shared_data_store import sanitize_token
    from script.shared_data_store import shared_apktool_cache_root
except Exception:
    from shared_data_store import discover_apk_by_stem  # type: ignore[no-redef]
    from shared_data_store import discover_decoded_dir_by_stem  # type: ignore[no-redef]
    from shared_data_store import resolve_path_ref  # type: ignore[no-redef]
    from shared_data_store import sanitize_token  # type: ignore[no-redef]
    from shared_data_store import shared_apktool_cache_root  # type: ignore[no-redef]


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
APP_DECODED_DIR_KEYS = (
    "decoded_dir",
    "decoded_apk_dir",
    "unpacked_dir",
    "apk_decoded_dir",
)
DECODE_REQUIRED_LAYERS = {"component", "resource", "library"}
FALLBACK_APKTOOL_JAR_DIR = Path(tempfile.gettempdir()) / "phd-bor005-tools"


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="deepening_runner.py",
        description=(
            "Builds extra M_static layers for shortlist candidates using "
            "cascade-config stages.deepening."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to cascade-config YAML/JSON file.")
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to candidate_list JSON produced by screening.",
    )
    parser.add_argument("--output", required=True, help="Path to output JSON file.")
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


def ensure_candidate_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = None
        for key in ("candidate_list", "candidates", "short_list", "shortlist", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and looks_like_candidate(payload):
            items = [payload]
        if items is None:
            raise ValueError("Could not find candidate list in provided JSON.")
    else:
        raise ValueError("Candidates JSON must be an object or array.")

    result = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Candidate at index {} must be an object.".format(index))
        result.append(item)
    return result


def looks_like_candidate(item: dict[str, Any]) -> bool:
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


def load_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ensure_candidate_items(payload)


def collect_stage_features(stage: dict[str, Any]) -> list[str]:
    ordered = []
    seen = set()

    def add(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if not isinstance(value, str):
                continue
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)

    add(stage.get("features"))
    views = stage.get("views")
    if isinstance(views, list):
        for view in views:
            if isinstance(view, dict):
                add(view.get("features"))
    return ordered


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def extract_per_view_scores(candidate: dict[str, Any]) -> dict[str, float] | None:
    """Read ``per_view_scores`` from an enriched_candidate (EXEC-087.1).

    The field can live either at the top level of the candidate row (as emitted
    by ``screening_runner.build_candidate_list``) or under the legacy ``apps``
    wrapper. Missing / malformed values yield ``None`` so downstream logic can
    omit the field entirely.
    """
    for container in (candidate, candidate.get("apps") if isinstance(candidate.get("apps"), dict) else None):
        if not isinstance(container, dict):
            continue
        raw = container.get("per_view_scores")
        if not isinstance(raw, dict) or not raw:
            continue
        coerced: dict[str, float] = {}
        for key, value in raw.items():
            try:
                coerced[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        if coerced:
            return coerced
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

    app_a = first_present(
        candidate,
        ("query_app", "query_app_id", "apk_1", "app_1"),
    )
    app_b = first_present(
        candidate,
        ("candidate_app", "candidate_app_id", "apk_2", "app_2"),
    )
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
        if path.is_file():
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


def resolve_apk_path(candidate: dict[str, Any], app: Any, side: str) -> str | None:
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

    app_label = resolve_app_label(app, "")
    if app_label:
        return discover_apk_by_stem(app_label)

    return None


def extract_decoded_dir_from_app(app: Any) -> str | None:
    if not isinstance(app, dict):
        return None
    for key in APP_DECODED_DIR_KEYS:
        value = app.get(key)
        if isinstance(value, str) and value:
            return resolve_path_ref(value)
    return None


def resolve_decoded_dir_path(candidate: dict[str, Any], app: Any, side: str) -> str | None:
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


def looks_like_decoded_dir(path: Path) -> bool:
    return path.is_dir() and (
        (path / "AndroidManifest.xml").is_file()
        or (path / "res").is_dir()
        or (path / "assets").is_dir()
    )


def build_decode_cache_dir(apk_path: str) -> Path:
    apk_file = Path(apk_path).expanduser().resolve()
    stats = apk_file.stat()
    fingerprint = hashlib.sha256(
        "{}|{}|{}".format(apk_file, stats.st_size, stats.st_mtime_ns).encode("utf-8")
    ).hexdigest()[:16]
    namespace = sanitize_token(os.environ.get("PHD_APKTOOL_CACHE_NAMESPACE", "apktool-default"))
    return shared_apktool_cache_root(namespace) / "{}-{}".format(apk_file.stem, fingerprint)


def resolve_apktool_command() -> list[str] | None:
    apktool_path = os.environ.get("APKTOOL_PATH")
    if apktool_path:
        candidate = Path(apktool_path).expanduser().resolve()
        if candidate.is_file():
            return [str(candidate)]

    discovered = shutil.which("apktool")
    if discovered:
        return [discovered]

    jar_candidates: list[Path] = []
    apktool_jar = os.environ.get("APKTOOL_JAR_PATH") or os.environ.get("APKTOOL_JAR")
    if apktool_jar:
        jar_candidates.append(Path(apktool_jar).expanduser().resolve())
    if FALLBACK_APKTOOL_JAR_DIR.is_dir():
        jar_candidates.extend(sorted(FALLBACK_APKTOOL_JAR_DIR.glob("apktool*.jar")))

    java_path = shutil.which("java")
    if java_path:
        for candidate in jar_candidates:
            if candidate.is_file():
                return [java_path, "-jar", str(candidate)]

    return None


def materialize_decoded_dir(apk_path: str) -> str:
    apk_file = Path(apk_path).expanduser().resolve()
    if not apk_file.is_file():
        raise FileNotFoundError("APK does not exist: {}".format(apk_file))

    cache_dir = build_decode_cache_dir(str(apk_file))
    if looks_like_decoded_dir(cache_dir):
        return str(cache_dir)

    command = resolve_apktool_command()
    if command is None:
        raise RuntimeError("apktool is not available; set APKTOOL_PATH or APKTOOL_JAR_PATH")

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    partial_dir = cache_dir.with_name(cache_dir.name + ".partial")
    shutil.rmtree(partial_dir, ignore_errors=True)
    shutil.rmtree(cache_dir, ignore_errors=True)

    process = subprocess.run(
        [*command, "d", "-f", str(apk_file), "-o", str(partial_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        shutil.rmtree(partial_dir, ignore_errors=True)
        message = process.stderr.strip() or process.stdout.strip() or "apktool_failed"
        raise RuntimeError(message)

    partial_dir.rename(cache_dir)
    return str(cache_dir)


def resolve_or_materialize_decoded_dir(
    candidate: dict[str, Any],
    app: Any,
    side: str,
    apk_path: str,
    decoded_cache: dict[str, str],
) -> str:
    explicit = resolve_decoded_dir_path(candidate, app, side)
    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        if looks_like_decoded_dir(explicit_path):
            decoded_cache[apk_path] = str(explicit_path)
            return str(explicit_path)
        raise FileNotFoundError("Decoded APK directory does not exist: {}".format(explicit_path))

    if apk_path in decoded_cache:
        return decoded_cache[apk_path]

    decoded_dir = materialize_decoded_dir(apk_path)
    decoded_cache[apk_path] = decoded_dir
    return decoded_dir


def load_enhanced_features(
    apk_path: str,
    decoded_dir: str,
    feature_cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cache_key = (apk_path, decoded_dir)
    if cache_key in feature_cache:
        return feature_cache[cache_key]

    if extract_all_features is None:
        raise RuntimeError("m_static_views.extract_all_features is unavailable")

    features = extract_all_features(apk_path=apk_path, unpacked_dir=decoded_dir)
    feature_cache[cache_key] = features
    return features


def build_code_layer(apk_path: str, cache: dict[str, int]) -> tuple[int, bool]:
    if apk_path in cache:
        return cache[apk_path], True

    apk_file = Path(apk_path)
    if not apk_file.is_file():
        raise FileNotFoundError("APK does not exist: {}".format(apk_path))

    with tempfile.TemporaryDirectory(prefix="deepening_code_") as output_dir:
        with working_directory(PROJECT_ROOT):
            dots = build_model(apk_path, output_dir)
    cfg_count = len(dots)
    cache[apk_path] = cfg_count
    return cfg_count, False


def enrich_candidate(
    candidate: dict[str, Any],
    layers_to_enrich: list[str],
    code_cache: dict[str, int],
    decoded_cache: dict[str, str],
    feature_cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    app_a, app_b = extract_apps(candidate)
    app_a_payload = dict(app_a) if isinstance(app_a, dict) else app_a
    app_b_payload = dict(app_b) if isinstance(app_b, dict) else app_b
    prior_per_view_scores = extract_per_view_scores(candidate)
    start = time.perf_counter()
    enriched_views = []
    apk_a = resolve_apk_path(candidate, app_a, "a")
    apk_b = resolve_apk_path(candidate, app_b, "b")
    decoded_a: str | None = None
    decoded_b: str | None = None
    decoded_error: Exception | None = None
    features_a: dict[str, Any] | None = None
    features_b: dict[str, Any] | None = None

    if any(layer in DECODE_REQUIRED_LAYERS for layer in layers_to_enrich):
        if apk_a is not None and apk_b is not None:
            try:
                decoded_a = resolve_or_materialize_decoded_dir(
                    candidate=candidate,
                    app=app_a,
                    side="a",
                    apk_path=apk_a,
                    decoded_cache=decoded_cache,
                )
                decoded_b = resolve_or_materialize_decoded_dir(
                    candidate=candidate,
                    app=app_b,
                    side="b",
                    apk_path=apk_b,
                    decoded_cache=decoded_cache,
                )
                if isinstance(app_a_payload, dict):
                    app_a_payload["decoded_dir"] = decoded_a
                if isinstance(app_b_payload, dict):
                    app_b_payload["decoded_dir"] = decoded_b
                features_a = load_enhanced_features(apk_a, decoded_a, feature_cache)
                features_b = load_enhanced_features(apk_b, decoded_b, feature_cache)
            except Exception as error:
                decoded_error = error

    for layer in layers_to_enrich:
        layer_start = time.perf_counter()
        if layer == "code":
            if apk_a is None or apk_b is None:
                enriched_views.append(
                    {
                        "view_id": "code",
                        "view_status": "missing_apk_path",
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
                continue

            try:
                cfg_count_a, cached_a = build_code_layer(apk_a, code_cache)
                cfg_count_b, cached_b = build_code_layer(apk_b, code_cache)
                status = "success" if cfg_count_a > 0 and cfg_count_b > 0 else "analysis_failed"
                enriched_views.append(
                    {
                        "view_id": "code",
                        "view_status": status,
                        "app_a_cfg_count": cfg_count_a,
                        "app_b_cfg_count": cfg_count_b,
                        "cache_hit_app_a": cached_a,
                        "cache_hit_app_b": cached_b,
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
            except Exception as error:
                enriched_views.append(
                    {
                        "view_id": "code",
                        "view_status": "analysis_failed",
                        "error": str(error),
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
            continue

        if layer == "metadata":
            status = "success" if apk_a is not None and apk_b is not None else "missing_apk_path"
            enriched_views.append(
                {
                    "view_id": "metadata",
                    "view_status": status,
                    "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                }
            )
            continue

        if layer in DECODE_REQUIRED_LAYERS:
            if apk_a is None or apk_b is None:
                enriched_views.append(
                    {
                        "view_id": layer,
                        "view_status": "missing_apk_path",
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
                continue

            try:
                if decoded_error is not None:
                    raise decoded_error
                _ = features_a.get(layer)
                _ = features_b.get(layer)
                enriched_views.append(
                    {
                        "view_id": layer,
                        "view_status": "success",
                        "app_a_decoded_dir": decoded_a,
                        "app_b_decoded_dir": decoded_b,
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
            except Exception as error:
                enriched_views.append(
                    {
                        "view_id": layer,
                        "view_status": "analysis_failed",
                        "error": str(error),
                        "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
                    }
                )
            continue

        enriched_views.append(
            {
                "view_id": layer,
                "view_status": "not_implemented",
                "cost_ms": int(round((time.perf_counter() - layer_start) * 1000)),
            }
        )

    result: dict[str, Any] = {
        "app_a": app_a_payload,
        "app_b": app_b_payload,
        "app_a_path": apk_a,
        "app_b_path": apk_b,
        "app_a_decoded_dir": app_a_payload.get("decoded_dir") if isinstance(app_a_payload, dict) else None,
        "app_b_decoded_dir": app_b_payload.get("decoded_dir") if isinstance(app_b_payload, dict) else None,
        "enriched_views": enriched_views,
        "deepening_cost_ms": int(round((time.perf_counter() - start) * 1000)),
    }
    if prior_per_view_scores is not None:
        result["prior_per_view_scores"] = prior_per_view_scores
    # Пробрасываем shortcut-флаги из candidate в result, чтобы pairwise_runner
    # мог активировать ветку EXEC-091-EXEC в каскаде screening → deepening → pairwise.
    for _shortcut_key in ("shortcut_applied", "shortcut_reason", "signature_match"):
        if _shortcut_key in candidate:
            result[_shortcut_key] = candidate[_shortcut_key]
    return result


def run_deepening(config_path: Path, candidates_path: Path) -> dict[str, Any]:
    if os.environ.get("SIMILARITY_SKIP_REQ_CHECK") != "1":
        verify_required_dependencies()

    config = load_config(config_path)
    stages = config.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("Config field stages must be an object.")

    screening = stages.get("screening")
    deepening = stages.get("deepening")
    if not isinstance(screening, dict):
        raise ValueError("Config field stages.screening must be an object.")
    if not isinstance(deepening, dict):
        raise ValueError("Config field stages.deepening must be an object.")

    screening_features = set(collect_stage_features(screening))
    deepening_features = collect_stage_features(deepening)
    pairwise = stages.get("pairwise")
    pairwise_features = collect_stage_features(pairwise) if isinstance(pairwise, dict) else []

    layers_to_enrich = []
    seen_layers = set()
    for layer in [*deepening_features, *pairwise_features]:
        if layer in seen_layers:
            continue
        if layer in screening_features and layer not in DECODE_REQUIRED_LAYERS:
            continue
        seen_layers.add(layer)
        layers_to_enrich.append(layer)

    candidates = load_candidates(candidates_path)
    code_cache: dict[str, int] = {}
    decoded_cache: dict[str, str] = {}
    feature_cache: dict[tuple[str, str], dict[str, Any]] = {}
    enriched_candidates = [
        enrich_candidate(
            candidate,
            layers_to_enrich,
            code_cache,
            decoded_cache,
            feature_cache,
        )
        for candidate in candidates
    ]

    return {
        "enriched_candidates": enriched_candidates,
    }


def main() -> None:
    args = parse_args()
    payload = run_deepening(Path(args.config), Path(args.candidates))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

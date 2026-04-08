#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def import_optional(module_name: str):
    try:
        return __import__(module_name)
    except Exception:
        return None


SCREENING_RUNNER = import_optional("screening_runner")
DEEPENING_RUNNER = import_optional("deepening_runner")
PAIRWISE_RUNNER = import_optional("pairwise_runner")


M_STATIC_LAYERS = ("code", "component", "resource", "metadata", "library")
MANIFEST_COMPONENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])(\.?[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*(?:Activity|Service|Receiver|Provider|Application))(?![A-Za-z0-9_$.])"
)


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
        prog="batch_config_runner.py",
        description=(
            "Run cascade screening -> deepening -> pairwise for multiple configs "
            "and APK pairs, then export consolidated CSV."
        ),
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        required=True,
        help="List of cascade-config files and/or directories with *.yml/*.yaml/*.json configs.",
    )
    parser.add_argument(
        "--pairs",
        required=True,
        help="Path to JSON with pairs: [{app_a_path, app_b_path}].",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output summary CSV.",
    )
    parser.add_argument("--ins-block-sim-threshold", type=float, default=0.80)
    parser.add_argument("--ged-timeout-sec", type=int, default=30)
    parser.add_argument("--processes-count", type=int, default=1)
    parser.add_argument("--threads-count", type=int, default=2)
    return parser.parse_args()


def normalize_metric_name(metric: str) -> str:
    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "normalize_metric_name"):
        return str(SCREENING_RUNNER.normalize_metric_name(metric))
    aliases = {
        "jac": "jaccard",
        "jaccard_similarity": "jaccard",
        "cos": "cosine",
        "cosine_similarity": "cosine",
        "cnt": "containment",
        "intersection_over_min": "containment",
        "overlap_coefficient": "containment",
        "dice_coefficient": "dice",
        "shared_graph_count_v1": "shared_count",
    }
    return aliases.get(metric.strip().lower(), metric.strip().lower())


def load_config_payload(path: Path) -> dict[str, Any]:
    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "load_yaml_or_json"):
        payload = SCREENING_RUNNER.load_yaml_or_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be mapping")
        return payload

    if DEEPENING_RUNNER and hasattr(DEEPENING_RUNNER, "load_config"):
        payload = DEEPENING_RUNNER.load_config(path)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be mapping")
        return payload

    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        payload = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "YAML parser is unavailable and screening/deepening loader was not imported"
            ) from exc
        payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("Config root must be mapping")
    return payload


def collect_stage_features(stage: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def append_features(raw: Any) -> None:
        if not isinstance(raw, list):
            return
        for value in raw:
            normalized = str(value).strip().lower()
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)

    append_features(stage.get("features"))
    views = stage.get("views")
    if isinstance(views, list):
        for view in views:
            if isinstance(view, dict):
                append_features(view.get("features"))
    return result


def extract_screening_stage(config: dict[str, Any]) -> tuple[list[str], str, float]:
    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "extract_screening_stage"):
        layers, metric, threshold = SCREENING_RUNNER.extract_screening_stage(config)
        return [str(layer).strip().lower() for layer in layers], normalize_metric_name(str(metric)), float(
            threshold
        )

    stages = config.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("Missing config.stages")
    screening = stages.get("screening")
    if not isinstance(screening, dict):
        raise ValueError("Missing config.stages.screening")

    features = collect_stage_features(screening)
    if not features:
        raise ValueError("stages.screening.features must be non-empty")

    normalized_layers = []
    for layer in features:
        if layer not in M_STATIC_LAYERS:
            raise ValueError("Unsupported screening feature: {!r}".format(layer))
        normalized_layers.append(layer)

    metric = normalize_metric_name(str(screening.get("metric", "jaccard")))
    threshold = float(screening.get("threshold", 0.0))
    return normalized_layers, metric, threshold


def extract_pairwise_stage(config: dict[str, Any]) -> tuple[list[str], str, float, str]:
    stages = config.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("Missing config.stages")
    pairwise = stages.get("pairwise")
    if not isinstance(pairwise, dict):
        raise ValueError("Missing config.stages.pairwise")

    features = collect_stage_features(pairwise)
    if not features:
        raise ValueError("stages.pairwise.features must be non-empty")

    normalized_layers = []
    for layer in features:
        if layer not in M_STATIC_LAYERS:
            raise ValueError("Unsupported pairwise feature: {!r}".format(layer))
        normalized_layers.append(layer)

    metric = normalize_metric_name(str(pairwise.get("metric", "ged")))
    threshold = float(pairwise.get("threshold", 0.0))
    library_exclusion_mode = str(
        pairwise.get("library_exclusion_mode", config.get("library_exclusion_mode", "heuristic_v1"))
    ).strip()
    if not library_exclusion_mode:
        library_exclusion_mode = "heuristic_v1"
    if library_exclusion_mode not in {"disabled", "heuristic_v1"}:
        library_exclusion_mode = "heuristic_v1"
    return normalized_layers, metric, threshold, library_exclusion_mode


def collect_config_paths(config_entries: list[str]) -> list[Path]:
    allowed_suffixes = {".yml", ".yaml", ".json"}
    resolved_paths: list[Path] = []

    for raw in config_entries:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            files = sorted(
                [
                    path
                    for path in candidate.iterdir()
                    if path.is_file() and path.suffix.lower() in allowed_suffixes
                ]
            )
            resolved_paths.extend(files)
            continue

        if candidate.is_file():
            resolved_paths.append(candidate)
            continue

        raise FileNotFoundError("Config path does not exist: {}".format(candidate))

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in resolved_paths:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)

    if not deduped:
        raise ValueError("No config files found")
    return deduped


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def resolve_pair_path(raw: str, base_dir: Path) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def load_pairs(pairs_path: Path) -> list[dict[str, str]]:
    payload = json.loads(pairs_path.read_text(encoding="utf-8"))

    if isinstance(payload, dict):
        entries = None
        for key in ("pairs", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = value
                break
        if entries is None:
            if "app_a_path" in payload and "app_b_path" in payload:
                entries = [payload]
            else:
                raise ValueError("pairs JSON object must contain list under pairs/items/data")
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("pairs JSON must be list or object")

    result: list[dict[str, str]] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError("Pair at index {} must be an object".format(index))

        app_a_raw = first_present(
            item,
            (
                "app_a_path",
                "apk_a_path",
                "left_apk_path",
                "apk_1",
                "app_a",
            ),
        )
        app_b_raw = first_present(
            item,
            (
                "app_b_path",
                "apk_b_path",
                "right_apk_path",
                "apk_2",
                "app_b",
            ),
        )

        if not isinstance(app_a_raw, str) or not app_a_raw.strip():
            raise ValueError("Pair at index {} is missing app_a_path".format(index))
        if not isinstance(app_b_raw, str) or not app_b_raw.strip():
            raise ValueError("Pair at index {} is missing app_b_path".format(index))

        app_a_path = resolve_pair_path(app_a_raw.strip(), pairs_path.parent)
        app_b_path = resolve_pair_path(app_b_raw.strip(), pairs_path.parent)

        result.append(
            {
                "app_a_path": str(app_a_path),
                "app_b_path": str(app_b_path),
            }
        )

    if not result:
        raise ValueError("No pairs found in pairs JSON")
    return result


def size_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value <= 3:
        return "1_3"
    if value <= 7:
        return "4_7"
    if value <= 15:
        return "8_15"
    if value <= 31:
        return "16_31"
    if value <= 63:
        return "32_63"
    return "64_plus"


def decode_manifest_candidates(manifest_bytes: bytes) -> list[str]:
    variants = []
    for encoding in ("utf-8", "utf-16le"):
        decoded = manifest_bytes.decode(encoding, errors="ignore")
        if decoded:
            variants.append(decoded)
    return variants


def extract_layers_from_apk_inline(apk_path: Path) -> dict[str, set[str]]:
    with zipfile.ZipFile(apk_path, "r") as archive:
        entries = [entry for entry in archive.namelist() if entry and not entry.endswith("/")]
        entry_set = set(entries)

        dex_entries = sorted(
            [entry for entry in entries if entry.startswith("classes") and entry.endswith(".dex")]
        )
        has_manifest = "AndroidManifest.xml" in entry_set
        has_resources_arsc = "resources.arsc" in entry_set

        manifest_component_features: set[str] = set()
        if has_manifest:
            try:
                manifest_bytes = archive.read("AndroidManifest.xml")
                for text in decode_manifest_candidates(manifest_bytes):
                    for match in MANIFEST_COMPONENT_PATTERN.findall(text):
                        value = match.strip().replace("/", ".")
                        if value:
                            manifest_component_features.add("manifest_component:{}".format(value))
            except KeyError:
                has_manifest = False

        metadata = {
            "apk_name:{}".format(apk_path.stem),
            "entry_bin:{}".format(size_bucket(len(entries))),
            "dex_count_bin:{}".format(size_bucket(len(dex_entries))),
            "manifest_present:{}".format(1 if has_manifest else 0),
            "resources_arsc_present:{}".format(1 if has_resources_arsc else 0),
        }

        resource: set[str] = set()
        component: set[str] = set(manifest_component_features)
        library: set[str] = set()
        code: set[str] = set("dex:{}".format(entry) for entry in dex_entries)

        for entry in entries:
            if entry.startswith("res/"):
                parts = entry.split("/")
                if len(parts) >= 2 and parts[1]:
                    res_type = parts[1].split("-", 1)[0]
                    resource.add("res_type:{}".format(res_type))
                    if res_type.startswith("layout"):
                        layout_name = Path(entry).stem
                        if layout_name:
                            component.add("layout:{}".format(layout_name))
                suffix = Path(entry).suffix.lower().lstrip(".")
                if suffix:
                    resource.add("res_ext:{}".format(suffix))
            elif entry.startswith("assets/"):
                suffix = Path(entry).suffix.lower().lstrip(".")
                if suffix:
                    resource.add("asset_ext:{}".format(suffix))
            elif entry.startswith("lib/"):
                parts = entry.split("/")
                if len(parts) >= 2 and parts[1]:
                    library.add("lib_abi:{}".format(parts[1]))
            elif entry.startswith("META-INF/"):
                suffix = Path(entry).suffix.upper().lstrip(".")
                if suffix:
                    library.add("meta_inf_ext:{}".format(suffix))

        if not code:
            code.add("dex:absent")

        return {
            "code": code,
            "component": component,
            "resource": resource,
            "metadata": metadata,
            "library": library,
        }


def build_app_record(apk_path: str, app_record_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    resolved = Path(apk_path).expanduser().resolve()
    key = str(resolved)
    if key in app_record_cache:
        return app_record_cache[key]

    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "extract_layers_from_apk"):
        layers = SCREENING_RUNNER.extract_layers_from_apk(resolved)
    else:
        layers = extract_layers_from_apk_inline(resolved)

    record = {
        "app_id": key,
        "apk_path": key,
        "layers": layers,
    }
    app_record_cache[key] = record
    return record


def aggregate_features(app_record: dict[str, Any], selected_layers: list[str]) -> set[str]:
    features: set[str] = set()
    layers = app_record.get("layers", {})
    if not isinstance(layers, dict):
        return features
    for layer in selected_layers:
        layer_features = layers.get(layer, set())
        if not isinstance(layer_features, (set, list, tuple)):
            continue
        for feature in layer_features:
            if feature is None:
                continue
            features.add("{}:{}".format(layer, str(feature).strip()))
    return features


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    denominator = len(left | right)
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def cosine_similarity(left: set[str], right: set[str]) -> float:
    denominator = math.sqrt(len(left)) * math.sqrt(len(right))
    if denominator == 0.0:
        return 0.0
    return len(left & right) / denominator


def containment_similarity(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def dice_similarity(left: set[str], right: set[str]) -> float:
    denominator = len(left) + len(right)
    if denominator == 0:
        return 0.0
    return (2.0 * len(left & right)) / denominator


def overlap_similarity(left: set[str], right: set[str]) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def shared_count_similarity(left: set[str], right: set[str]) -> float:
    return float(len(left & right))


def levenshtein_similarity(left: set[str], right: set[str]) -> float:
    import textdistance

    left_seq = sorted(left)
    right_seq = sorted(right)
    maximum = max(len(left_seq), len(right_seq))
    if maximum == 0:
        return 0.0
    distance = textdistance.levenshtein.distance(left_seq, right_seq)
    return max(0.0, 1.0 - (distance / maximum))


def calculate_cfg_ged_similarity(
    apk_a: str,
    apk_b: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> float:
    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "calculate_cfg_ged_similarity"):
        return float(
            SCREENING_RUNNER.calculate_cfg_ged_similarity(
                apk_a=apk_a,
                apk_b=apk_b,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
        )

    from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
    from script.calculate_apks_similarity.build_model import build_model
    from script.calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity

    with TemporaryDirectory(prefix="batch_cfg_ged_") as tmp_dir:
        output_1 = Path(tmp_dir) / "first"
        output_2 = Path(tmp_dir) / "second"
        output_1.mkdir(parents=True, exist_ok=True)
        output_2.mkdir(parents=True, exist_ok=True)
        with working_directory(PROJECT_ROOT):
            dots_1 = build_model(apk_a, str(output_1))
            dots_2 = build_model(apk_b, str(output_2))
        if not dots_1 or not dots_2:
            return 0.0
        matrix = build_comparison_matrix(
            dots_1,
            dots_2,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )
        similarity_score, _ = calculate_models_similarity(matrix, dots_1, dots_2)
        return float(similarity_score)


def calculate_pair_score_local(
    app_a: dict[str, Any],
    app_b: dict[str, Any],
    metric: str,
    selected_layers: list[str],
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> float:
    normalized_metric = normalize_metric_name(metric)

    if normalized_metric == "ged":
        if selected_layers == ["code"]:
            apk_a = str(app_a.get("apk_path", ""))
            apk_b = str(app_b.get("apk_path", ""))
            if not apk_a or not apk_b:
                return 0.0
            return calculate_cfg_ged_similarity(
                apk_a=apk_a,
                apk_b=apk_b,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
        raise ValueError("metric=ged is currently supported only for selected_layers=['code']")

    left = aggregate_features(app_a, selected_layers)
    right = aggregate_features(app_b, selected_layers)

    if normalized_metric == "jaccard":
        return jaccard_similarity(left, right)
    if normalized_metric == "cosine":
        return cosine_similarity(left, right)
    if normalized_metric == "containment":
        return containment_similarity(left, right)
    if normalized_metric == "dice":
        return dice_similarity(left, right)
    if normalized_metric == "overlap":
        return overlap_similarity(left, right)
    if normalized_metric == "shared_count":
        return shared_count_similarity(left, right)
    if normalized_metric in {"levenshtein", "edit_distance"}:
        return levenshtein_similarity(left, right)

    raise ValueError("Unsupported metric: {!r}".format(metric))


def calculate_screening_score(
    app_a_record: dict[str, Any],
    app_b_record: dict[str, Any],
    selected_layers: list[str],
    metric: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> float:
    if SCREENING_RUNNER and hasattr(SCREENING_RUNNER, "calculate_pair_score"):
        try:
            return float(
                SCREENING_RUNNER.calculate_pair_score(
                    app_a=app_a_record,
                    app_b=app_b_record,
                    metric=metric,
                    selected_layers=selected_layers,
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec,
                    processes_count=processes_count,
                    threads_count=threads_count,
                )
            )
        except Exception:
            pass

    return calculate_pair_score_local(
        app_a=app_a_record,
        app_b=app_b_record,
        metric=metric,
        selected_layers=selected_layers,
        ins_block_sim_threshold=ins_block_sim_threshold,
        ged_timeout_sec=ged_timeout_sec,
        processes_count=processes_count,
        threads_count=threads_count,
    )


def write_temp_candidates(
    app_a_record: dict[str, Any],
    app_b_record: dict[str, Any],
    screening_score: float,
) -> tuple[Path, TemporaryDirectory]:
    tmp = TemporaryDirectory(prefix="batch_deepening_")
    temp_root = Path(tmp.name)
    candidates_path = temp_root / "candidates.json"
    payload = [
        {
            "app_a": {
                "app_id": app_a_record["app_id"],
                "apk_path": app_a_record["apk_path"],
            },
            "app_b": {
                "app_id": app_b_record["app_id"],
                "apk_path": app_b_record["apk_path"],
            },
            "app_a_path": app_a_record["apk_path"],
            "app_b_path": app_b_record["apk_path"],
            "retrieval_score": float(screening_score),
        }
    ]
    candidates_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return candidates_path, tmp


def run_deepening(
    config_path: Path,
    candidates_path: Path,
) -> dict[str, Any]:
    if DEEPENING_RUNNER and hasattr(DEEPENING_RUNNER, "run_deepening"):
        return DEEPENING_RUNNER.run_deepening(config_path, candidates_path)

    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        entries = payload.get("candidate_list") if isinstance(payload.get("candidate_list"), list) else []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []

    enriched = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        enriched.append(
            {
                "app_a": item.get("app_a"),
                "app_b": item.get("app_b"),
                "enriched_views": [
                    {
                        "view_id": "inline_fallback",
                        "view_status": "not_implemented",
                        "cost_ms": 0,
                    }
                ],
                "deepening_cost_ms": 0,
            }
        )
    return {"enriched_candidates": enriched}


def build_pairwise_failure_result(reason: str) -> dict[str, Any]:
    return {
        "analysis_status": "analysis_failed",
        "failure_reason": reason,
        "scores": {
            "similarity_score": None,
            "full_similarity_score": None,
            "library_reduced_score": None,
        },
    }


def run_pairwise_code_ged(
    app_a_path: str,
    app_b_path: str,
    library_exclusion_mode: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> dict[str, Any]:
    try:
        from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
        from script.calculate_apks_similarity.build_model import build_model
        from script.calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity
        from script.calculate_apks_similarity.result_contract import build_scores
        from script.calculate_apks_similarity.result_contract import classify_failure_reason
        from script.calculate_apks_similarity.result_contract import serialize_sim_pairs
    except Exception as exc:
        return build_pairwise_failure_result("pairwise_import_failed: {}".format(exc))

    try:
        with TemporaryDirectory(prefix="batch_pairwise_ged_") as tmp_dir:
            output_1 = Path(tmp_dir) / "first"
            output_2 = Path(tmp_dir) / "second"
            output_1.mkdir(parents=True, exist_ok=True)
            output_2.mkdir(parents=True, exist_ok=True)

            with working_directory(PROJECT_ROOT):
                dots_1 = build_model(app_a_path, str(output_1))
                dots_2 = build_model(app_b_path, str(output_2))

            if not dots_1 or not dots_2:
                failure_reason, _diagnostics = classify_failure_reason(
                    app_a_path,
                    app_b_path,
                    len(dots_1),
                    len(dots_2),
                )
                return build_pairwise_failure_result(failure_reason)

            matrix = build_comparison_matrix(
                dots_1,
                dots_2,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
            full_similarity_score, sim_pairs = calculate_models_similarity(matrix, dots_1, dots_2)
            pair_records = serialize_sim_pairs(sim_pairs)
            scores = build_scores(
                full_similarity_score,
                pair_records,
                dots_1,
                dots_2,
                library_exclusion_mode,
            )
            return {
                "analysis_status": "success",
                "failure_reason": None,
                "scores": scores,
            }
    except Exception as exc:
        return build_pairwise_failure_result("internal_pipeline_error: {}".format(exc))


def run_pairwise_feature_metric(
    app_a_record: dict[str, Any],
    app_b_record: dict[str, Any],
    pairwise_features: list[str],
    pairwise_metric: str,
    library_exclusion_mode: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> dict[str, Any]:
    try:
        full_score = calculate_pair_score_local(
            app_a=app_a_record,
            app_b=app_b_record,
            metric=pairwise_metric,
            selected_layers=pairwise_features,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )
    except Exception as exc:
        return build_pairwise_failure_result("pairwise_metric_error: {}".format(exc))

    if library_exclusion_mode == "disabled":
        library_reduced_score = full_score
    else:
        without_library = [layer for layer in pairwise_features if layer != "library"]
        if not without_library:
            library_reduced_score = 0.0
        else:
            try:
                library_reduced_score = calculate_pair_score_local(
                    app_a=app_a_record,
                    app_b=app_b_record,
                    metric=pairwise_metric,
                    selected_layers=without_library,
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec,
                    processes_count=processes_count,
                    threads_count=threads_count,
                )
            except Exception:
                library_reduced_score = full_score

    similarity_score = library_reduced_score if library_exclusion_mode != "disabled" else full_score
    return {
        "analysis_status": "success",
        "failure_reason": None,
        "scores": {
            "similarity_score": float(similarity_score),
            "full_similarity_score": float(full_score),
            "library_reduced_score": float(library_reduced_score),
        },
    }


def normalize_pairwise_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            return build_pairwise_failure_result("empty_pairwise_payload")
        return normalize_pairwise_payload(payload[0])

    if not isinstance(payload, dict):
        return build_pairwise_failure_result("invalid_pairwise_payload")

    if "scores" in payload and isinstance(payload["scores"], dict):
        scores = payload["scores"]
        return {
            "analysis_status": str(payload.get("analysis_status", "success")),
            "failure_reason": payload.get("failure_reason"),
            "scores": {
                "similarity_score": scores.get("similarity_score"),
                "full_similarity_score": scores.get("full_similarity_score"),
                "library_reduced_score": scores.get("library_reduced_score"),
            },
        }

    for key in ("pairwise_results", "results", "pairs", "items", "data"):
        nested = payload.get(key)
        if isinstance(nested, list) and nested:
            return normalize_pairwise_payload(nested[0])

    full_score = payload.get("full_similarity_score")
    reduced_score = payload.get("library_reduced_score")
    similarity_score = payload.get("similarity_score")
    if reduced_score in (None, "") and full_score not in (None, ""):
        reduced_score = full_score
    if similarity_score in (None, ""):
        similarity_score = reduced_score if reduced_score not in (None, "") else full_score

    analysis_status = str(payload.get("analysis_status", "success" if full_score not in (None, "") else "analysis_failed"))
    return {
        "analysis_status": analysis_status,
        "failure_reason": payload.get("failure_reason"),
        "scores": {
            "similarity_score": similarity_score,
            "full_similarity_score": full_score,
            "library_reduced_score": reduced_score,
        },
    }


def call_pairwise_runner_module(
    config_path: Path,
    deepening_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not PAIRWISE_RUNNER or not hasattr(PAIRWISE_RUNNER, "run_pairwise"):
        return None

    run_pairwise = getattr(PAIRWISE_RUNNER, "run_pairwise")

    with TemporaryDirectory(prefix="batch_pairwise_imported_") as tmp_dir:
        deepening_path = Path(tmp_dir) / "deepening.json"
        deepening_path.write_text(json.dumps(deepening_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        attempts = [
            lambda: run_pairwise(config_path, deepening_path),
            lambda: run_pairwise(config_path=config_path, deepening_path=deepening_path),
            lambda: run_pairwise(config_path=config_path, enriched_path=deepening_path),
            lambda: run_pairwise(config=config_path, enriched=deepening_path),
            lambda: run_pairwise(config_path=config_path, candidates_path=deepening_path),
            lambda: run_pairwise(config_path=config_path, payload=deepening_payload),
        ]

        last_error: Exception | None = None
        for attempt in attempts:
            try:
                return normalize_pairwise_payload(attempt())
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                return build_pairwise_failure_result("pairwise_runner_error: {}".format(exc))

        if last_error is not None:
            return build_pairwise_failure_result("pairwise_runner_signature_mismatch: {}".format(last_error))

    return None


def run_pairwise(
    config_payload: dict[str, Any],
    config_path: Path,
    deepening_payload: dict[str, Any],
    app_a_record: dict[str, Any],
    app_b_record: dict[str, Any],
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    pairwise_cache: dict[tuple[Any, ...], dict[str, Any]],
) -> dict[str, Any]:
    from_module = call_pairwise_runner_module(config_path, deepening_payload)
    if from_module is not None:
        return from_module

    pairwise_features, pairwise_metric, _pairwise_threshold, library_exclusion_mode = extract_pairwise_stage(
        config_payload
    )

    pair_key = tuple(sorted([app_a_record["apk_path"], app_b_record["apk_path"]]))
    cache_key = (
        pair_key,
        tuple(pairwise_features),
        pairwise_metric,
        library_exclusion_mode,
        float(ins_block_sim_threshold),
        int(ged_timeout_sec),
        int(processes_count),
        int(threads_count),
    )
    if cache_key in pairwise_cache:
        return pairwise_cache[cache_key]

    if pairwise_metric == "ged" and pairwise_features == ["code"]:
        result = run_pairwise_code_ged(
            app_a_path=app_a_record["apk_path"],
            app_b_path=app_b_record["apk_path"],
            library_exclusion_mode=library_exclusion_mode,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )
    else:
        result = run_pairwise_feature_metric(
            app_a_record=app_a_record,
            app_b_record=app_b_record,
            pairwise_features=pairwise_features,
            pairwise_metric=pairwise_metric,
            library_exclusion_mode=library_exclusion_mode,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )

    normalized = normalize_pairwise_payload(result)
    pairwise_cache[cache_key] = normalized
    return normalized


def to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_float_for_csv(value: Any) -> str:
    parsed = to_float_or_none(value)
    if parsed is None:
        return ""
    return "{:.6f}".format(parsed)


def select_status(
    screening_score: float,
    screening_threshold: float,
    pairwise_payload: dict[str, Any] | None,
    pairwise_threshold: float,
) -> str:
    if screening_score < screening_threshold:
        return "screening_filtered"
    if pairwise_payload is None:
        return "analysis_failed"

    analysis_status = str(pairwise_payload.get("analysis_status", "analysis_failed"))
    if analysis_status != "success":
        return "analysis_failed"

    scores = pairwise_payload.get("scores", {})
    reduced = to_float_or_none(scores.get("library_reduced_score"))
    full = to_float_or_none(scores.get("full_similarity_score"))
    final_score = reduced if reduced is not None else full
    if final_score is None:
        return "analysis_failed"
    if final_score < pairwise_threshold:
        return "below_pairwise_threshold"
    return "success"


def write_results_csv(output_path: Path, rows: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "config_name",
        "app_a",
        "app_b",
        "screening_score",
        "pairwise_full_score",
        "pairwise_library_reduced",
        "status",
        "screening_cost_ms",
        "total_cost_ms",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "config_name": row["config_name"],
                    "app_a": row["app_a"],
                    "app_b": row["app_b"],
                    "screening_score": format_float_for_csv(row.get("screening_score")),
                    "pairwise_full_score": format_float_for_csv(row.get("pairwise_full_score")),
                    "pairwise_library_reduced": format_float_for_csv(
                        row.get("pairwise_library_reduced")
                    ),
                    "status": row.get("status", ""),
                    "screening_cost_ms": int(row.get("screening_cost_ms", 0)),
                    "total_cost_ms": int(row.get("total_cost_ms", 0)),
                }
            )


def print_summary(rows: list[dict[str, Any]], config_order: list[str]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in config_order}
    for row in rows:
        grouped.setdefault(row["config_name"], []).append(row)

    for config_name in config_order:
        group = grouped.get(config_name, [])
        if not group:
            print("{}: avg_time_ms=n/a analysis_failed=0 avg_score=n/a".format(config_name))
            continue

        avg_time = statistics.mean(float(item.get("total_cost_ms", 0.0)) for item in group)
        analysis_failed_count = sum(1 for item in group if item.get("status") == "analysis_failed")

        score_values: list[float] = []
        for item in group:
            reduced = to_float_or_none(item.get("pairwise_library_reduced"))
            full = to_float_or_none(item.get("pairwise_full_score"))
            if reduced is not None:
                score_values.append(reduced)
            elif full is not None:
                score_values.append(full)

        avg_score_text = "{:.6f}".format(statistics.mean(score_values)) if score_values else "n/a"
        print(
            "{}: avg_time_ms={:.2f} analysis_failed={} avg_score={}".format(
                config_name,
                avg_time,
                analysis_failed_count,
                avg_score_text,
            )
        )


def main() -> int:
    args = parse_args()

    config_paths = collect_config_paths(args.configs)
    pairs = load_pairs(Path(args.pairs).expanduser().resolve())

    app_record_cache: dict[str, dict[str, Any]] = {}
    pairwise_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    config_names: list[str] = []

    for config_path in config_paths:
        config_payload = load_config_payload(config_path)
        config_name = config_path.stem
        config_names.append(config_name)

        screening_layers, screening_metric, screening_threshold = extract_screening_stage(config_payload)
        _pairwise_layers, _pairwise_metric, pairwise_threshold, _pairwise_library_mode = extract_pairwise_stage(
            config_payload
        )

        for pair in pairs:
            pair_started = time.perf_counter()
            app_a_path = pair["app_a_path"]
            app_b_path = pair["app_b_path"]

            pairwise_payload: dict[str, Any] | None = None
            screening_score = 0.0
            screening_cost_ms = 0
            status = "analysis_failed"
            pairwise_full_score: float | None = None
            pairwise_library_reduced: float | None = None

            try:
                app_a_record = build_app_record(app_a_path, app_record_cache)
                app_b_record = build_app_record(app_b_path, app_record_cache)

                screening_started = time.perf_counter()
                screening_score = calculate_screening_score(
                    app_a_record=app_a_record,
                    app_b_record=app_b_record,
                    selected_layers=screening_layers,
                    metric=screening_metric,
                    ins_block_sim_threshold=args.ins_block_sim_threshold,
                    ged_timeout_sec=args.ged_timeout_sec,
                    processes_count=args.processes_count,
                    threads_count=args.threads_count,
                )
                screening_cost_ms = int(round((time.perf_counter() - screening_started) * 1000.0))

                if screening_score >= screening_threshold:
                    candidates_path, candidates_tmp = write_temp_candidates(
                        app_a_record,
                        app_b_record,
                        screening_score,
                    )
                    try:
                        deepening_payload = run_deepening(config_path, candidates_path)
                    finally:
                        candidates_tmp.cleanup()

                    pairwise_payload = run_pairwise(
                        config_payload=config_payload,
                        config_path=config_path,
                        deepening_payload=deepening_payload,
                        app_a_record=app_a_record,
                        app_b_record=app_b_record,
                        ins_block_sim_threshold=args.ins_block_sim_threshold,
                        ged_timeout_sec=args.ged_timeout_sec,
                        processes_count=args.processes_count,
                        threads_count=args.threads_count,
                        pairwise_cache=pairwise_cache,
                    )

            except Exception as exc:
                pairwise_payload = build_pairwise_failure_result("pipeline_error: {}".format(exc))

            status = select_status(
                screening_score=screening_score,
                screening_threshold=screening_threshold,
                pairwise_payload=pairwise_payload,
                pairwise_threshold=pairwise_threshold,
            )

            if pairwise_payload is not None:
                scores = pairwise_payload.get("scores", {})
                pairwise_full_score = to_float_or_none(scores.get("full_similarity_score"))
                pairwise_library_reduced = to_float_or_none(scores.get("library_reduced_score"))

            total_cost_ms = int(round((time.perf_counter() - pair_started) * 1000.0))
            rows.append(
                {
                    "config_name": config_name,
                    "app_a": app_a_path,
                    "app_b": app_b_path,
                    "screening_score": screening_score,
                    "pairwise_full_score": pairwise_full_score,
                    "pairwise_library_reduced": pairwise_library_reduced,
                    "status": status,
                    "screening_cost_ms": screening_cost_ms,
                    "total_cost_ms": total_cost_ms,
                }
            )

    output_path = Path(args.output).expanduser().resolve()
    write_results_csv(output_path, rows)
    print_summary(rows, config_names)
    print("Saved CSV: {}".format(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

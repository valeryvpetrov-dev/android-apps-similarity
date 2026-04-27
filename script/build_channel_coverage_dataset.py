#!/usr/bin/env python3
"""Build an evidence-channel coverage dataset for HINT wave 27.

The builder intentionally stays on the quick M_static path: it extracts ZIP-level
features once per APK, calls ``m_static_views.compare_all`` for selected pairs,
then derives per-channel evidence records from the quick per-layer scores plus
the signing signal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for candidate in (PROJECT_ROOT, SCRIPT_DIR):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

try:
    from script.hint_faithfulness import (
        EVIDENCE_CHANNELS,
        classify_evidence_channel,
        compute_channel_faithfulness,
    )
    from script.library_view_v2 import detect_tpl_in_packages
    from script.m_static_views import compare_all
    from script.screening_runner import extract_layers_from_apk
    from script.signing_view import compare_signatures
    from script.signing_view import extract_apk_signature_hash
except Exception:
    from hint_faithfulness import (  # type: ignore[no-redef]
        EVIDENCE_CHANNELS,
        classify_evidence_channel,
        compute_channel_faithfulness,
    )
    from library_view_v2 import detect_tpl_in_packages  # type: ignore[no-redef]
    from m_static_views import compare_all  # type: ignore[no-redef]
    from screening_runner import extract_layers_from_apk  # type: ignore[no-redef]
    from signing_view import compare_signatures  # type: ignore[no-redef]
    from signing_view import extract_apk_signature_hash  # type: ignore[no-redef]


DEFAULT_MIX = {"clone": 8, "repackage": 8, "similar": 8, "different": 6}
DEFAULT_LAYERS = ["code", "component", "resource", "metadata", "library"]
ARTIFACT_ID = "EXEC-HINT-27-CHANNEL-COVERAGE"


@dataclass
class ApkRecord:
    path: Path
    app_id: str
    sha256: str
    package_name: str
    signature_hash: str | None
    library_set: frozenset[str]
    category: str
    features: dict[str, Any] | None = None


def parse_mix(raw_mix: str | Mapping[str, int] | None) -> dict[str, int]:
    if raw_mix is None:
        return dict(DEFAULT_MIX)
    if isinstance(raw_mix, Mapping):
        parsed = {str(key): int(value) for key, value in raw_mix.items()}
    else:
        parsed: dict[str, int] = {}
        for part in str(raw_mix).split(","):
            stripped = part.strip()
            if not stripped:
                continue
            if ":" not in stripped:
                raise ValueError(f"Invalid --mix item {stripped!r}; expected category:count")
            category, value = stripped.split(":", 1)
            parsed[category.strip()] = int(value.strip())

    unknown = set(parsed) - set(DEFAULT_MIX)
    if unknown:
        raise ValueError(f"Unknown mix categories: {sorted(unknown)}")
    return {category: max(0, int(parsed.get(category, 0))) for category in DEFAULT_MIX}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_from_filename(path: Path) -> str:
    stem = path.stem
    if "_" in stem:
        package_part, version_part = stem.rsplit("_", 1)
        if version_part.isdigit() and package_part:
            return package_part
    return stem


def _category_from_package(package_name: str) -> str:
    parts = [part for part in package_name.split(".") if part]
    if not parts:
        return "unknown"
    if len(parts) >= 2 and parts[0] in {"org", "com", "net", "io", "app"}:
        return ".".join(parts[:2])
    return parts[0]


def _package_name_from_metadata(metadata: Iterable[str], fallback: str) -> str:
    for token in metadata:
        if token.startswith("package_name:"):
            value = token.split(":", 1)[1].strip()
            if value:
                return value
    return fallback


def _infer_decoded_root(corpus_dir: Path) -> Path | None:
    name = corpus_dir.name
    candidates = [
        corpus_dir.parent / name.replace("-apks", "-decoded"),
        corpus_dir.parent / name.replace("apks", "decoded"),
        corpus_dir.with_name("fdroid-corpus-v2-decoded"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _package_set_from_decoded(decoded_app_dir: Path | None) -> frozenset[str]:
    if decoded_app_dir is None or not decoded_app_dir.exists():
        return frozenset()

    packages: set[str] = set()
    for root, _dirs, files in os.walk(decoded_app_dir):
        root_path = Path(root)
        root_name = root_path.name
        if not root_name.startswith("smali"):
            rel_parts = root_path.parts
            if not any(part.startswith("smali") for part in rel_parts):
                continue
        smali_files = [name for name in files if name.endswith(".smali")]
        if not smali_files:
            continue
        parts = root_path.parts
        smali_index = None
        for index, part in enumerate(parts):
            if part.startswith("smali"):
                smali_index = index
                break
        if smali_index is None:
            continue
        package_parts = parts[smali_index + 1 :]
        if package_parts:
            packages.add(".".join(package_parts))
    return frozenset(packages)


def _tpl_library_set(package_set: frozenset[str]) -> frozenset[str]:
    if not package_set:
        return frozenset()
    try:
        detections = detect_tpl_in_packages(package_set)
    except Exception:
        return frozenset()
    libraries = {
        str(tpl_id)
        for tpl_id, info in detections.items()
        if isinstance(info, Mapping) and bool(info.get("detected"))
    }
    return frozenset(libraries)


def _quick_features_from_layers(
    layers: Mapping[str, set[str]],
    signature_hash: str | None,
) -> dict[str, Any]:
    return {
        "code": set(layers.get("code", set())),
        "component": set(layers.get("component", set())),
        "resource": set(layers.get("resource", set())),
        "metadata": set(layers.get("metadata", set())),
        "library": set(layers.get("library", set())),
        "signing": {"hash": signature_hash, "chain": []},
        "resource_v2": {
            "res_strings": set(),
            "res_drawables": set(),
            "res_layouts": set(),
            "assets_bin": set(),
            "icon_phash": None,
            "mode": "v2_unavailable",
        },
        "mode": "quick",
    }


def build_apk_records(corpus_dir: Path) -> tuple[list[ApkRecord], list[str]]:
    warnings: list[str] = []
    apks = sorted(corpus_dir.glob("*.apk"))
    decoded_root = _infer_decoded_root(corpus_dir)
    records: list[ApkRecord] = []

    for apk_path in apks:
        try:
            layers = extract_layers_from_apk(apk_path)
        except Exception as exc:
            warnings.append(f"skip {apk_path.name}: quick feature extraction failed: {exc}")
            continue

        sha256 = _sha256_file(apk_path)
        fallback_package = _package_from_filename(apk_path)
        package_name = _package_name_from_metadata(layers.get("metadata", set()), fallback_package)
        try:
            signature_hash = extract_apk_signature_hash(apk_path)
        except Exception:
            signature_hash = None

        decoded_app_dir = decoded_root / apk_path.stem if decoded_root is not None else None
        package_set = _package_set_from_decoded(decoded_app_dir)
        tpl_libraries = _tpl_library_set(package_set)
        quick_libraries = frozenset(str(item) for item in layers.get("library", set()))
        library_set = tpl_libraries or quick_libraries
        features = _quick_features_from_layers(layers, signature_hash)

        records.append(
            ApkRecord(
                path=apk_path,
                app_id=apk_path.stem,
                sha256=sha256,
                package_name=package_name,
                signature_hash=signature_hash,
                library_set=frozenset(library_set),
                category=_category_from_package(package_name),
                features=features,
            )
        )

    return records, warnings


def _library_overlap(left: frozenset[str], right: frozenset[str]) -> float:
    union = set(left) | set(right)
    if not union:
        return 0.0
    return len(set(left) & set(right)) / float(len(union))


def _set_jaccard(left: Any, right: Any) -> float | None:
    if not isinstance(left, set) or not isinstance(right, set):
        return None
    union = left | right
    if not union:
        return None
    return len(left & right) / float(len(union))


def _quick_static_overlap(left: ApkRecord, right: ApkRecord) -> float:
    if not isinstance(left.features, Mapping) or not isinstance(right.features, Mapping):
        return 0.0
    scores = []
    for layer in ("code", "component", "resource", "library"):
        score = _set_jaccard(left.features.get(layer), right.features.get(layer))
        if score is not None:
            scores.append(score)
    if not scores:
        return 0.0
    return sum(scores) / float(len(scores))


def _pair_identity(left: ApkRecord, right: ApkRecord) -> tuple[str, str]:
    return tuple(sorted((str(left.path), str(right.path))))  # type: ignore[return-value]


def build_pair_pools(records: Sequence[ApkRecord]) -> dict[str, list[tuple[ApkRecord, ApkRecord]]]:
    pools: dict[str, list[tuple[ApkRecord, ApkRecord]]] = {
        "clone": [],
        "repackage": [],
        "similar": [],
        "different": [],
    }

    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            same_package = bool(left.package_name and left.package_name == right.package_name)
            same_sha = bool(left.sha256 and left.sha256 == right.sha256)
            same_signature = (
                left.signature_hash is not None
                and right.signature_hash is not None
                and left.signature_hash == right.signature_hash
            )
            different_sha = bool(left.sha256 and right.sha256 and left.sha256 != right.sha256)
            overlap = _library_overlap(left.library_set, right.library_set)
            near_duplicate = same_package and _quick_static_overlap(left, right) >= 0.8

            if same_sha or (same_package and same_signature) or near_duplicate:
                pools["clone"].append((left, right))
            if same_package and different_sha and (not same_signature or left.path != right.path):
                pools["repackage"].append((left, right))
            if not same_package and overlap >= 0.5:
                pools["similar"].append((left, right))
            if not same_package and overlap < 0.1 and left.category != right.category:
                pools["different"].append((left, right))

    return pools


def _score_for_synthetic_category(category: str) -> float:
    return {
        "clone": 1.0,
        "repackage": 0.82,
        "similar": 0.62,
        "different": 0.08,
    }.get(category, 0.0)


def _canonical_evidence(score: float, signing_score: float) -> list[dict[str, object]]:
    return [
        {"source_stage": "pairwise", "signal_type": "layer_score", "ref": "code", "magnitude": score},
        {"source_stage": "pairwise", "signal_type": "layer_score", "ref": "component", "magnitude": score},
        {"source_stage": "pairwise", "signal_type": "library_match", "ref": "library", "magnitude": score},
        {"source_stage": "pairwise", "signal_type": "resource_overlap", "ref": "resource", "magnitude": score},
        {
            "source_stage": "signing",
            "signal_type": "signature_match",
            "ref": "apk_signature",
            "magnitude": signing_score,
        },
    ]


def _evidence_channels(evidence: Iterable[Mapping[str, object]]) -> list[str]:
    channels = {
        channel
        for item in evidence
        for channel in [classify_evidence_channel(item)]
        if channel is not None
    }
    return sorted(channels)


def channel_coverage_summary(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(pairs)
    per_channel: dict[str, dict[str, float | int]] = {}
    for channel in EVIDENCE_CHANNELS:
        count = 0
        for pair in pairs:
            channels = set(pair.get("evidence_channels") or [])
            if channel in channels:
                count += 1
        per_channel[channel] = {
            "pairs_with_data": count,
            "ratio": round(count / total, 6) if total else 0.0,
        }

    pairs_with_all = sum(
        1
        for pair in pairs
        if set(EVIDENCE_CHANNELS).issubset(set(pair.get("evidence_channels") or []))
    )
    return {
        "pairs_total": total,
        "pairs_with_all_channels": pairs_with_all,
        "all_channels_ratio": round(pairs_with_all / total, 6) if total else 0.0,
        "per_channel": per_channel,
    }


def _feature_summary(features: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for layer in ("code", "component", "resource", "metadata", "library"):
        value = features.get(layer)
        if isinstance(value, set):
            summary[layer] = {
                "count": len(value),
                "sample": sorted(str(item) for item in value)[:8],
            }
    signing = features.get("signing")
    if isinstance(signing, Mapping):
        signature_hash = signing.get("hash")
        summary["signing"] = {
            "present": bool(signature_hash),
            "prefix": str(signature_hash)[:12] if signature_hash else None,
        }
    return summary


def _evidence_from_compare(
    compare_result: Mapping[str, Any],
    left: ApkRecord,
    right: ApkRecord,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    layer_to_signal = {
        "code": ("layer_score", "code"),
        "component": ("layer_score", "component"),
        "library": ("library_match", "library"),
        "resource": ("resource_overlap", "resource"),
    }
    per_layer = compare_result.get("per_layer")
    if isinstance(per_layer, Mapping):
        for layer, (signal_type, ref) in layer_to_signal.items():
            layer_result = per_layer.get(layer)
            if not isinstance(layer_result, Mapping):
                continue
            if layer_result.get("both_empty") or layer_result.get("status") == "both_empty":
                continue
            try:
                magnitude = float(layer_result.get("score", 0.0))
            except (TypeError, ValueError):
                magnitude = 0.0
            evidence.append(
                {
                    "source_stage": "pairwise",
                    "signal_type": signal_type,
                    "ref": ref,
                    "magnitude": magnitude,
                    "status": str(layer_result.get("status") or ""),
                }
            )

    signature = compare_signatures(left.signature_hash, right.signature_hash)
    if signature.get("status") != "both_missing":
        evidence.append(
            {
                "source_stage": "signing",
                "signal_type": "signature_match",
                "ref": "apk_signature",
                "magnitude": float(signature.get("score", 0.0)),
                "status": str(signature.get("status") or ""),
            }
        )
    return evidence


def _compare_pair(left: ApkRecord, right: ApkRecord, ground_truth: str) -> dict[str, Any]:
    features_a = left.features or _quick_features_from_layers(
        extract_layers_from_apk(left.path),
        left.signature_hash,
    )
    features_b = right.features or _quick_features_from_layers(
        extract_layers_from_apk(right.path),
        right.signature_hash,
    )
    compare_result = compare_all(features_a, features_b, layers=DEFAULT_LAYERS)
    evidence = _evidence_from_compare(compare_result, left, right)
    channels = _evidence_channels(evidence)
    pair_id = f"{left.path.stem}__{right.path.stem}"
    return {
        "pair_id": pair_id,
        "ground_truth": ground_truth,
        "app_a": left.app_id,
        "app_b": right.app_id,
        "apk_a": str(left.path),
        "apk_b": str(right.path),
        "package_name_a": left.package_name,
        "package_name_b": right.package_name,
        "sha256_a": left.sha256,
        "sha256_b": right.sha256,
        "signature_hash_a": left.signature_hash,
        "signature_hash_b": right.signature_hash,
        "library_overlap": round(_library_overlap(left.library_set, right.library_set), 6),
        "evidence_channels": channels,
        "pairwise_score": compare_result.get("full_similarity_score"),
        "full_similarity_score": compare_result.get("full_similarity_score"),
        "evidence": evidence,
        "full_metadata": {
            "compare_result": compare_result,
            "evidence": evidence,
            "features_a_summary": _feature_summary(features_a),
            "features_b_summary": _feature_summary(features_b),
            "library_set_a": sorted(left.library_set),
            "library_set_b": sorted(right.library_set),
        },
    }


def _synthetic_pair(category: str, index: int) -> dict[str, Any]:
    score = _score_for_synthetic_category(category)
    signing_score = 1.0 if category == "clone" else 0.0
    evidence = _canonical_evidence(score, signing_score)
    channels = _evidence_channels(evidence)
    return {
        "pair_id": f"synthetic-{category}-{index:02d}",
        "ground_truth": category,
        "app_a": f"synthetic.{category}.a{index}",
        "app_b": f"synthetic.{category}.b{index}",
        "package_name_a": f"synthetic.{category}",
        "package_name_b": f"synthetic.{category}",
        "sha256_a": f"synthetic-a-{category}-{index}",
        "sha256_b": f"synthetic-b-{category}-{index}",
        "signature_hash_a": "synthetic-signature" if category == "clone" else "synthetic-a",
        "signature_hash_b": "synthetic-signature" if category == "clone" else "synthetic-b",
        "library_overlap": score,
        "evidence_channels": channels,
        "pairwise_score": score,
        "full_similarity_score": score,
        "evidence": evidence,
        "full_metadata": {"compare_result": {}, "evidence": evidence, "synthetic": True},
    }


def build_synthetic_dataset(
    *,
    n_pairs: int,
    mix: Mapping[str, int],
    seed: int,
    warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    for category, count in mix.items():
        for index in range(count):
            pairs.append(_synthetic_pair(category, index))
    pairs = pairs[:n_pairs]
    return {
        "artifact_id": ARTIFACT_ID,
        "source": "synthetic_fallback",
        "n_pairs": len(pairs),
        "seed": seed,
        "mix": dict(mix),
        "warnings": list(warnings or []),
        "channel_coverage": channel_coverage_summary(pairs),
        "per_pair": pairs,
    }


def _shuffled_pairs(
    pairs: Sequence[tuple[ApkRecord, ApkRecord]],
    rng: random.Random,
) -> list[tuple[ApkRecord, ApkRecord]]:
    shuffled = list(pairs)
    rng.shuffle(shuffled)
    return shuffled


def _build_real_pairs(
    pools: Mapping[str, Sequence[tuple[ApkRecord, ApkRecord]]],
    mix: Mapping[str, int],
    seed: int,
    require_all_channels: bool = True,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    used_pairs: set[tuple[str, str]] = set()
    pool_sizes = {category: len(pools.get(category, [])) for category in DEFAULT_MIX}

    for category, target_count in mix.items():
        category_rows = 0
        skipped_for_coverage = 0
        for left, right in _shuffled_pairs(pools.get(category, []), rng):
            identity = _pair_identity(left, right)
            if identity in used_pairs:
                continue
            row = _compare_pair(left, right, category)
            has_all_channels = set(EVIDENCE_CHANNELS).issubset(row["evidence_channels"])
            if require_all_channels and not has_all_channels:
                skipped_for_coverage += 1
                continue
            used_pairs.add(identity)
            rows.append(row)
            category_rows += 1
            if category_rows >= target_count:
                break
        if category_rows < target_count:
            warnings.append(
                "category {} produced {}/{} pairs (pool={}, skipped_for_coverage={})".format(
                    category,
                    category_rows,
                    target_count,
                    pool_sizes.get(category, 0),
                    skipped_for_coverage,
                )
            )

    return rows, warnings, pool_sizes


def build_channel_coverage_dataset(
    corpus_dir: Path,
    out_path: Path | None = None,
    n_pairs: int = 30,
    mix: Mapping[str, int] | str | None = None,
    seed: int = 42,
    require_all_channels: bool = True,
) -> dict[str, Any]:
    parsed_mix = parse_mix(mix)
    warnings: list[str] = []
    corpus_dir = corpus_dir.expanduser()
    if not corpus_dir.exists() or not corpus_dir.is_dir():
        warnings.append(f"fallback: corpus_dir does not exist: {corpus_dir}")
        dataset = build_synthetic_dataset(n_pairs=n_pairs, mix=parsed_mix, seed=seed, warnings=warnings)
        if out_path is not None:
            write_json(out_path, dataset)
        return dataset

    records, record_warnings = build_apk_records(corpus_dir)
    warnings.extend(record_warnings)
    if not records:
        warnings.append(f"fallback: no APK records could be built from {corpus_dir}")
        dataset = build_synthetic_dataset(n_pairs=n_pairs, mix=parsed_mix, seed=seed, warnings=warnings)
        if out_path is not None:
            write_json(out_path, dataset)
        return dataset

    pools = build_pair_pools(records)
    rows, pair_warnings, pool_sizes = _build_real_pairs(
        pools,
        parsed_mix,
        seed,
        require_all_channels=require_all_channels,
    )
    warnings.extend(pair_warnings)
    rows = rows[:n_pairs]
    dataset = {
        "artifact_id": ARTIFACT_ID,
        "source": "fdroid_v2",
        "corpus_dir": str(corpus_dir),
        "corpus_apks": len(records),
        "n_pairs": len(rows),
        "seed": seed,
        "mix": parsed_mix,
        "actual_mix": {
            category: sum(1 for row in rows if row.get("ground_truth") == category)
            for category in DEFAULT_MIX
        },
        "pool_sizes": pool_sizes,
        "warnings": warnings,
        "channel_coverage": channel_coverage_summary(rows),
        "per_pair": rows,
    }
    if out_path is not None:
        write_json(out_path, dataset)
    return dataset


def _mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(statistics.mean(values), 6)


def replay_channel_faithfulness(dataset: Mapping[str, Any]) -> dict[str, Any]:
    per_pair_metrics: list[dict[str, Any]] = []
    by_channel: dict[str, dict[str, list[float]]] = {
        channel: {"faithfulness": [], "sufficiency": [], "comprehensiveness": []}
        for channel in EVIDENCE_CHANNELS
    }
    pairs = dataset.get("per_pair") if isinstance(dataset, Mapping) else []
    if not isinstance(pairs, list):
        pairs = []

    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        full_metadata = pair.get("full_metadata")
        evidence = None
        if isinstance(full_metadata, Mapping):
            evidence = full_metadata.get("evidence")
        if evidence is None:
            evidence = pair.get("evidence", [])
        metrics = compute_channel_faithfulness(pair, evidence if isinstance(evidence, list) else [])
        per_pair_metrics.append(
            {
                "pair_id": pair.get("pair_id"),
                "ground_truth": pair.get("ground_truth"),
                "channels": metrics,
            }
        )
        for channel, channel_metrics in metrics.items():
            for metric_name in ("faithfulness", "sufficiency", "comprehensiveness"):
                value = channel_metrics.get(metric_name)
                if value is not None:
                    by_channel[channel][metric_name].append(float(value))

    aggregate: dict[str, Any] = {}
    for channel in EVIDENCE_CHANNELS:
        metric_values = by_channel[channel]
        aggregate[channel] = {
            "n_pairs_with_data": len(metric_values["faithfulness"]),
            "faithfulness_mean": _mean_or_none(metric_values["faithfulness"]),
            "sufficiency_mean": _mean_or_none(metric_values["sufficiency"]),
            "comprehensiveness_mean": _mean_or_none(metric_values["comprehensiveness"]),
        }

    return {
        "artifact_id": ARTIFACT_ID,
        "source_dataset": dataset.get("source") if isinstance(dataset, Mapping) else None,
        "n_pairs": len(pairs),
        "channels": aggregate,
        "per_pair": per_pair_metrics,
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_metrics_path(out_path: Path) -> Path:
    return out_path.with_name("per_channel_metrics_v2.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus_dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--n_pairs", type=int, default=30)
    parser.add_argument("--mix", default="clone:8,repackage:8,similar:8,different:6")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--metrics_out", type=Path, default=None)
    parser.add_argument(
        "--allow_partial_coverage",
        action="store_true",
        help="Keep pairs even when one of the five evidence channels is absent.",
    )
    args = parser.parse_args(argv)

    mix = parse_mix(args.mix)
    dataset = build_channel_coverage_dataset(
        corpus_dir=args.corpus_dir,
        out_path=args.out,
        n_pairs=args.n_pairs,
        mix=mix,
        seed=args.seed,
        require_all_channels=not args.allow_partial_coverage,
    )
    metrics = replay_channel_faithfulness(dataset)
    metrics_path = args.metrics_out or _default_metrics_path(args.out)
    write_json(metrics_path, metrics)

    for warning in dataset.get("warnings", []):
        print(warning, file=sys.stderr)
    print(
        "wrote {} pairs to {}; metrics to {}".format(
            dataset.get("n_pairs", 0),
            args.out,
            metrics_path,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

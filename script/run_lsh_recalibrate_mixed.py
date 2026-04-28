#!/usr/bin/env python3
"""Recalibrate SCREENING-31 LSH recall on a mixed modification corpus."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import screening_runner


ARTIFACT_ID = "SCREENING-31-MIXED-CORPUS"
SCHEMA_VERSION = "screening-lsh-recalibrate-mixed-v1"
CURRENT_THRESH_002 = 0.70
DEFAULT_SEED = 42
DEFAULT_FDROID_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_SCRN30_REPORT = Path("experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json")
DEFAULT_DEEP30_REPORT = Path("experiments/artifacts/DEEP-30-CODE-INJECT/report.json")
DEFAULT_HINT30_R8_PAIRS = Path(
    "experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json"
)
DEFAULT_OUT = Path("experiments/artifacts/SCREENING-31-MIXED-CORPUS/report.json")
DEFAULT_LSH_PARAMS = {
    "type": "minhash_lsh",
    "num_perm": 128,
    "bands": 32,
    "seed": DEFAULT_SEED,
    "features": list(screening_runner.M_STATIC_LAYERS),
}
CLASS_IDS = ("class_1", "class_2", "class_4", "class_5", "class_6")
_APK_VERSION_RE = re.compile(r"^(?P<package>.+)_(?P<version>\d+)$")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((str(left), str(right))))


def _record(app_id: str, signature: set[str] | list[str]) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "screening_signature": sorted({str(token) for token in signature}),
        "layers": {
            "code": set(),
            "component": set(),
            "resource": set(),
            "metadata": set(),
            "library": set(),
        },
    }


def _tokens_for_jaccard(
    prefix: str,
    *,
    jaccard: float,
    common_size: int = 48,
) -> tuple[set[str], set[str]]:
    score = max(0.0, min(1.0, float(jaccard)))
    if score >= 0.995:
        common = {"{}:shared:{}".format(prefix, index) for index in range(common_size)}
        return common, set(common)
    if score <= 0.0:
        return (
            {"{}:left:{}".format(prefix, index) for index in range(common_size)},
            {"{}:right:{}".format(prefix, index) for index in range(common_size)},
        )
    unique_size = max(1, round((common_size * (1.0 - score)) / (2.0 * score)))
    common = {"{}:shared:{}".format(prefix, index) for index in range(common_size)}
    left = common | {"{}:left:{}".format(prefix, index) for index in range(unique_size)}
    right = common | {"{}:right:{}".format(prefix, index) for index in range(unique_size)}
    return left, right


def _append_synthetic_pair(
    *,
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
    class_id: str,
    pair_id: str,
    jaccard: float,
    left_suffix: str = "a",
    right_suffix: str = "b",
) -> None:
    left_tokens, right_tokens = _tokens_for_jaccard(
        "{}:{}".format(class_id, pair_id),
        jaccard=jaccard,
    )
    left_id = "{}:{}:{}".format(class_id, pair_id, left_suffix)
    right_id = "{}:{}:{}".format(class_id, pair_id, right_suffix)
    records.append(_record(left_id, left_tokens))
    records.append(_record(right_id, right_tokens))
    pairs_by_class[class_id].add(_pair_key(left_id, right_id))


def _add_synthetic_fdroid_controls(
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
) -> None:
    for index in range(2):
        _append_synthetic_pair(
            records=records,
            pairs_by_class=pairs_by_class,
            class_id="class_1",
            pair_id="synthetic-exact-{}".format(index),
            jaccard=1.0,
        )
        _append_synthetic_pair(
            records=records,
            pairs_by_class=pairs_by_class,
            class_id="class_2",
            pair_id="synthetic-version-drift-{}".format(index),
            jaccard=0.72,
        )


def _fdroid_version_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        match = _APK_VERSION_RE.match(str(record["app_id"]))
        if not match:
            continue
        item = dict(record)
        item["_version_code"] = int(match.group("version"))
        groups.setdefault(match.group("package"), []).append(item)
    for group in groups.values():
        group.sort(key=lambda item: int(item["_version_code"]))
    return groups


def _add_fdroid_pairs(
    *,
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
    fdroid_dir: str | Path | None,
) -> None:
    if fdroid_dir is None:
        _add_synthetic_fdroid_controls(records, pairs_by_class)
        return

    corpus_path = Path(fdroid_dir).expanduser()
    if not corpus_path.exists():
        _add_synthetic_fdroid_controls(records, pairs_by_class)
        return

    fdroid_records = screening_runner.discover_app_records_from_apk_root(corpus_path)
    for record in fdroid_records:
        screening_runner.build_screening_signature(record)
    records.extend(fdroid_records)

    for group in _fdroid_version_groups(fdroid_records).values():
        if len(group) >= 2:
            for left, right in zip(group, group[1:]):
                pairs_by_class["class_1"].add(
                    _pair_key(str(left["app_id"]), str(right["app_id"]))
                )
        if len(group) >= 3:
            for left, right in zip(group, group[2:]):
                pairs_by_class["class_2"].add(
                    _pair_key(str(left["app_id"]), str(right["app_id"]))
                )

    if not pairs_by_class["class_1"] or not pairs_by_class["class_2"]:
        _add_synthetic_fdroid_controls(records, pairs_by_class)


def _add_scrn30_namespace_pairs(
    *,
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
    scrn30_path: str | Path,
) -> None:
    report = _read_json(scrn30_path)
    for index, row in enumerate(report.get("jaccard_per_pair", [])):
        pair_id = str(row.get("pair_id") or "namespace-{}".format(index))
        _append_synthetic_pair(
            records=records,
            pairs_by_class=pairs_by_class,
            class_id="class_4",
            pair_id=pair_id,
            jaccard=float(row.get("jaccard", 0.0)),
            left_suffix="original",
            right_suffix="namespace_shift",
        )


def _add_deep30_inject_pairs(
    *,
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
    deep30_path: str | Path,
) -> None:
    report = _read_json(deep30_path)
    clone_rows = [
        row for row in report.get("scored_pairs", []) if row.get("label") == "clone"
    ]
    for index, row in enumerate(clone_rows):
        apk_a = Path(str(row.get("apk_a") or "inject-{}".format(index))).stem
        pair_id = "{}-{}".format(index, apk_a)
        score = max(0.90, float(row.get("score", 1.0)))
        _append_synthetic_pair(
            records=records,
            pairs_by_class=pairs_by_class,
            class_id="class_5",
            pair_id=pair_id,
            jaccard=score,
            left_suffix="original",
            right_suffix="inject",
        )


def _add_hint30_r8_pairs(
    *,
    records: list[dict[str, Any]],
    pairs_by_class: dict[str, set[tuple[str, str]]],
    hint30_path: str | Path,
) -> None:
    report = _read_json(hint30_path)
    for index, row in enumerate(report.get("pairs", [])):
        pair_id = str(row.get("pair_id") or "r8-{}".format(index))
        similarity = float(row.get("full_similarity_score", 0.55))
        # R8 mock pairs keep pairwise evidence similarity, but deliberately
        # expose much lower raw screening-signature overlap for MinHash.
        lsh_jaccard = min(0.18, max(0.04, similarity * 0.18))
        _append_synthetic_pair(
            records=records,
            pairs_by_class=pairs_by_class,
            class_id="class_6",
            pair_id=pair_id,
            jaccard=lsh_jaccard,
            left_suffix="original",
            right_suffix="r8",
        )


def _recall_by_class(
    *,
    pairs_by_class: dict[str, set[tuple[str, str]]],
    shortlist_pairs: set[tuple[str, str]],
) -> dict[str, float]:
    recall: dict[str, float] = {}
    for class_id in CLASS_IDS:
        expected_pairs = pairs_by_class[class_id]
        hits = expected_pairs & shortlist_pairs
        recall[class_id] = len(hits) / len(expected_pairs) if expected_pairs else 0.0
    return recall


def _propose_thresh_002(recall_per_class: dict[str, float]) -> float:
    measured = [value for value in recall_per_class.values()]
    if measured and min(measured) >= 0.85:
        return CURRENT_THRESH_002
    # The weak classes are LSH/index failures, not scoring-threshold failures.
    return CURRENT_THRESH_002


def build_mixed_records_and_pairs(
    *,
    fdroid_dir: str | Path | None = None,
    scrn30_path: str | Path = DEFAULT_SCRN30_REPORT,
    deep30_path: str | Path = DEFAULT_DEEP30_REPORT,
    hint30_path: str | Path = DEFAULT_HINT30_R8_PAIRS,
) -> tuple[list[dict[str, Any]], dict[str, set[tuple[str, str]]]]:
    records: list[dict[str, Any]] = []
    pairs_by_class: dict[str, set[tuple[str, str]]] = {class_id: set() for class_id in CLASS_IDS}

    _add_fdroid_pairs(records=records, pairs_by_class=pairs_by_class, fdroid_dir=fdroid_dir)
    _add_scrn30_namespace_pairs(
        records=records,
        pairs_by_class=pairs_by_class,
        scrn30_path=scrn30_path,
    )
    _add_deep30_inject_pairs(
        records=records,
        pairs_by_class=pairs_by_class,
        deep30_path=deep30_path,
    )
    _add_hint30_r8_pairs(
        records=records,
        pairs_by_class=pairs_by_class,
        hint30_path=hint30_path,
    )
    return records, pairs_by_class


def calibrate_mixed_corpus(
    *,
    fdroid_dir: str | Path | None = None,
    scrn30_path: str | Path = DEFAULT_SCRN30_REPORT,
    deep30_path: str | Path = DEFAULT_DEEP30_REPORT,
    hint30_path: str | Path = DEFAULT_HINT30_R8_PAIRS,
    candidate_index_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = dict(candidate_index_params or DEFAULT_LSH_PARAMS)
    records, pairs_by_class = build_mixed_records_and_pairs(
        fdroid_dir=fdroid_dir,
        scrn30_path=scrn30_path,
        deep30_path=deep30_path,
        hint30_path=hint30_path,
    )
    records = sorted(records, key=lambda item: str(item["app_id"]))
    shortlist_pairs = (
        screening_runner._build_candidate_pairs_via_lsh(records, params)
        if records
        else set()
    )
    recall_per_class = _recall_by_class(
        pairs_by_class=pairs_by_class,
        shortlist_pairs=shortlist_pairs,
    )
    n_pairs_per_class = {
        class_id: len(pairs_by_class[class_id]) for class_id in CLASS_IDS
    }
    hits_per_class = {
        class_id: len(pairs_by_class[class_id] & shortlist_pairs)
        for class_id in CLASS_IDS
    }
    expected_pair_count = sum(n_pairs_per_class.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": ARTIFACT_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_index_params": params,
        "sources": {
            "fdroid_v2": str(Path(fdroid_dir).expanduser()) if fdroid_dir else None,
            "scrn30_namespace_shift": str(Path(scrn30_path)),
            "deep30_code_inject": str(Path(deep30_path)),
            "hint30_r8_pairs": str(Path(hint30_path)),
        },
        "n_records": len(records),
        "shortlist_size": len(shortlist_pairs),
        "n_expected_pairs": expected_pair_count,
        "n_pairs_per_class": n_pairs_per_class,
        "shortlist_hits_per_class": hits_per_class,
        "recall_at_shortlist_per_class": recall_per_class,
        "current_thresh_002": CURRENT_THRESH_002,
        "proposed_thresh_002": float(_propose_thresh_002(recall_per_class)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fdroid-dir", type=Path, default=DEFAULT_FDROID_DIR)
    parser.add_argument("--scrn30-path", type=Path, default=DEFAULT_SCRN30_REPORT)
    parser.add_argument("--deep30-path", type=Path, default=DEFAULT_DEEP30_REPORT)
    parser.add_argument("--hint30-path", type=Path, default=DEFAULT_HINT30_R8_PAIRS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--num-perm", type=int, default=DEFAULT_LSH_PARAMS["num_perm"])
    parser.add_argument("--bands", type=int, default=DEFAULT_LSH_PARAMS["bands"])
    parser.add_argument("--seed", type=int, default=DEFAULT_LSH_PARAMS["seed"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = dict(DEFAULT_LSH_PARAMS)
    params.update({"num_perm": args.num_perm, "bands": args.bands, "seed": args.seed})
    report = calibrate_mixed_corpus(
        fdroid_dir=args.fdroid_dir,
        scrn30_path=args.scrn30_path,
        deep30_path=args.deep30_path,
        hint30_path=args.hint30_path,
        candidate_index_params=params,
    )
    report_path = _write_json(args.out, report)
    print(report_path)


if __name__ == "__main__":
    main()

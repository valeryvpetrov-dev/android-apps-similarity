#!/usr/bin/env python3
"""BOR-002 RPlugin: resource-based Jaccard similarity view for M_static.

Computes SHA-256 digests of files under res/ and assets/ in unpacked APKs,
then measures Jaccard similarity on the resulting (relative_path, digest) sets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple


RESOURCE_DIRS = ("res", "assets")
HASH_ALGORITHM = "sha256"
CHUNK_SIZE = 8192


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="resource_view",
        description=(
            "Compare two unpacked APK directories by Jaccard similarity "
            "on SHA-256 digests of resource files (res/, assets/)."
        ),
    )
    parser.add_argument("apk_a", help="Path to the first unpacked APK directory")
    parser.add_argument("apk_b", help="Path to the second unpacked APK directory")
    parser.add_argument(
        "--output",
        help="Optional path to write the resulting JSON payload. Defaults to stdout.",
    )
    return parser.parse_args()


def ensure_input_dir(path_str: str) -> Path:
    apk_path = Path(path_str).expanduser().resolve()
    if not apk_path.exists():
        raise FileNotFoundError("APK directory does not exist: {}".format(apk_path))
    if not apk_path.is_dir():
        raise NotADirectoryError("APK path is not a directory: {}".format(apk_path))
    return apk_path


def _file_sha256(file_path: Path) -> str:
    h = hashlib.new(HASH_ALGORITHM)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extract_resource_features(apk_unpacked_dir: str) -> Dict:
    """Walk res/ and assets/ directories, compute SHA-256 digest per file.

    Returns dict with keys:
        resource_digests: set of (relative_path, digest) tuples
        file_count: number of resource files found
        total_size: cumulative size in bytes
    """
    apk_path = ensure_input_dir(apk_unpacked_dir)
    digests: Set[Tuple[str, str]] = set()
    total_size = 0

    for res_dir_name in RESOURCE_DIRS:
        res_dir = apk_path / res_dir_name
        if not res_dir.is_dir():
            continue
        for file_path in sorted(res_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(apk_path).as_posix()
            digest = _file_sha256(file_path)
            digests.add((rel_path, digest))
            total_size += file_path.stat().st_size

    return {
        "resource_digests": digests,
        "file_count": len(digests),
        "total_size": total_size,
    }


def compare_resources(features_a: Dict, features_b: Dict) -> Dict:
    """Compute Jaccard similarity and per-file diff between two feature sets.

    Returns dict with keys:
        resource_jaccard_score: float in [0.0, 1.0]
        added: list of relative paths present only in B
        removed: list of relative paths present only in A
        modified: list of relative paths present in both but with different digest
        unchanged_count: number of identical (path, digest) pairs
    """
    set_a: Set[Tuple[str, str]] = features_a["resource_digests"]
    set_b: Set[Tuple[str, str]] = features_b["resource_digests"]

    intersection = set_a & set_b
    union = set_a | set_b

    if not union:
        jaccard = 1.0
    else:
        jaccard = len(intersection) / len(union)

    paths_a: Dict[str, str] = {path: digest for path, digest in set_a}
    paths_b: Dict[str, str] = {path: digest for path, digest in set_b}

    added: List[str] = sorted(p for p in paths_b if p not in paths_a)
    removed: List[str] = sorted(p for p in paths_a if p not in paths_b)
    modified: List[str] = sorted(
        p for p in paths_a if p in paths_b and paths_a[p] != paths_b[p]
    )
    unchanged_count = len(intersection)

    return {
        "resource_jaccard_score": jaccard,
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": unchanged_count,
    }


def resource_explanation_hints(comparison: Dict) -> List[Dict]:
    """Generate explanation hints of type ResourceChange for diff entries."""
    hints: List[Dict] = []

    for path in comparison.get("modified", []):
        hints.append({
            "type": "ResourceChange",
            "action": "modified",
            "path": path,
            "detail": "file exists in both APKs but content differs",
        })

    for path in comparison.get("added", []):
        hints.append({
            "type": "ResourceChange",
            "action": "added",
            "path": path,
            "detail": "file present only in second APK",
        })

    for path in comparison.get("removed", []):
        hints.append({
            "type": "ResourceChange",
            "action": "removed",
            "path": path,
            "detail": "file present only in first APK",
        })

    return hints


def write_output(payload: Dict, output_path: str | None) -> None:
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
    if output_path:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload_json + os.linesep, encoding="utf-8")
        return
    print(payload_json)


def main() -> int:
    args = parse_args()
    try:
        features_a = extract_resource_features(args.apk_a)
        features_b = extract_resource_features(args.apk_b)
        comparison = compare_resources(features_a, features_b)
        hints = resource_explanation_hints(comparison)

        payload = {
            "apk_a": str(Path(args.apk_a).expanduser().resolve()),
            "apk_b": str(Path(args.apk_b).expanduser().resolve()),
            "features_a": {
                "file_count": features_a["file_count"],
                "total_size": features_a["total_size"],
            },
            "features_b": {
                "file_count": features_b["file_count"],
                "total_size": features_b["total_size"],
            },
            "comparison": comparison,
            "explanation_hints": hints,
        }
        write_output(payload, args.output)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

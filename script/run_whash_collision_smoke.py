#!/usr/bin/env python3
"""REPR-27 wHash collision smoke for F-Droid v2 APK icons."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from script import resource_view_v2
except ImportError:  # pragma: no cover - standalone script fallback
    import resource_view_v2  # type: ignore[no-redef]

try:
    from PIL import Image  # type: ignore
    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on local env
    Image = None  # type: ignore
    _PILLOW_AVAILABLE = False


RUN_ID = "REPR-27-WHASH-FDROID"
SOURCE_PATH = "script/run_whash_collision_smoke.py"
DEFAULT_CORPUS_DIR = (
    Path.home()
    / "Library"
    / "Caches"
    / "phd-shared"
    / "datasets"
    / "fdroid-corpus-v2-apks"
)
DEFAULT_MINI_CORPUS_DIR = _PROJECT_ROOT / "apk"
DEFAULT_OUT = _PROJECT_ROOT / "experiments" / "artifacts" / RUN_ID
NEAR_DUPLICATE_THRESHOLD = 5
HASH_BITS = 64
HASH_HEX_LEN = HASH_BITS // 4


def discover_apks(corpus_dir: Path) -> List[Path]:
    """Return APK files under ``corpus_dir`` in deterministic order."""
    root = Path(corpus_dir).expanduser()
    if not root.is_dir():
        return []
    return sorted(path.resolve() for path in root.rglob("*.apk") if path.is_file())


def _resolve_corpus_dir(
    corpus_dir: Path,
    fallback_corpus_dir: Optional[Path],
) -> Tuple[Path, bool, List[Path]]:
    requested = Path(corpus_dir).expanduser()
    apk_paths = discover_apks(requested)
    if apk_paths:
        return requested, False, apk_paths

    if fallback_corpus_dir is None:
        raise ValueError("corpus_dir not found or contains no APK files: {}".format(requested))

    fallback = Path(fallback_corpus_dir).expanduser()
    fallback_apks = discover_apks(fallback)
    if not fallback_apks:
        raise ValueError(
            "corpus_dir unavailable and fallback mini-corpus contains no APK files: "
            "{} -> {}".format(requested, fallback)
        )
    return fallback, True, fallback_apks


def _display_path(path: Path) -> str:
    return str(Path(path).expanduser().absolute())


def _decoded_root_for_corpus(apk_paths: Sequence[Path]) -> Optional[Path]:
    if not apk_paths:
        return None
    corpus_root = apk_paths[0].parent
    if corpus_root.name.endswith("-apks"):
        candidate = corpus_root.with_name(corpus_root.name[:-5] + "-decoded")
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _decoded_dir_for_apk(apk_path: Path, decoded_root: Optional[Path]) -> Optional[Path]:
    if decoded_root is None:
        return None
    candidate = decoded_root / apk_path.stem
    return candidate if candidate.is_dir() else None


def _package_name_from_apk_name(apk_path: Path) -> str:
    stem = apk_path.stem
    if "_" not in stem:
        return stem
    return stem.rsplit("_", 1)[0]


def _read_manifest_package(decoded_dir: Optional[Path]) -> Optional[str]:
    if decoded_dir is None:
        return None
    manifest = decoded_dir / "AndroidManifest.xml"
    if not manifest.is_file():
        return None
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = 'package="'
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = text.find('"', start)
    if end < 0:
        return None
    package_name = text[start:end].strip()
    return package_name or None


def _safe_zip_members(zf: zipfile.ZipFile) -> Iterable[zipfile.ZipInfo]:
    for info in zf.infolist():
        if info.is_dir():
            continue
        raw_name = info.filename
        if not raw_name.startswith(("res/", "assets/")):
            continue
        rel = PurePosixPath(raw_name)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        yield info


def _extract_apk_resources(apk_path: Path, unpacked_dir: Path) -> None:
    with zipfile.ZipFile(apk_path) as zf:
        for info in _safe_zip_members(zf):
            rel = PurePosixPath(info.filename)
            target = unpacked_dir.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))


def _parse_icon_hash(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    prefix = "{}:".format(resource_view_v2.ICON_TOKEN_PREFIX)
    if not token.startswith(prefix):
        return None
    tail = token[len(prefix):]
    if ":" in tail:
        method, _, hex_part = tail.partition(":")
        method = method.strip().lower()
    else:
        method = resource_view_v2.ICON_HASH_METHOD_FALLBACK
        hex_part = tail
    hex_part = hex_part.strip().lower()
    if len(hex_part) != HASH_HEX_LEN:
        return None
    try:
        value = int(hex_part, 16)
    except ValueError:
        return None
    return {
        "method": method,
        "hex": hex_part,
        "value": value,
    }


def _image_resampling_filter() -> Any:
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS


def _binary_bits_to_hex(bits: Sequence[bool]) -> str:
    value = 0
    for bit in bits:
        value = (value << 1) | int(bool(bit))
    return "{:0{}x}".format(value, HASH_HEX_LEN)


def _compute_native_whash(icon_path: Path) -> Optional[str]:
    """Small local equivalent for ImageHash whash(hash_size=8, mode='haar').

    For the default ImageHash settings used by resource_view_v2, ``hash_size``
    and ``image_scale`` are both 8. The final low-frequency plane is therefore
    the resized 8x8 grayscale image with a median threshold.
    """
    if not _PILLOW_AVAILABLE:
        return None
    try:
        with Image.open(icon_path) as img:
            gray = img.convert("L").resize((8, 8), _image_resampling_filter())
            pixels = list(gray.getdata())
    except Exception:  # noqa: BLE001 - decoder failures mean no icon signal
        return None
    if len(pixels) != HASH_BITS:
        return None
    median = statistics.median(pixels)
    return _binary_bits_to_hex([pixel > median for pixel in pixels])


def _find_icon_file(unpacked_dir: Path) -> Optional[Path]:
    finder = getattr(resource_view_v2, "_find_icon_file", None)
    if finder is None:
        return None
    return finder(unpacked_dir)


def _extract_icon_token_from_dir(unpacked_dir: Path) -> Tuple[Optional[str], str, Optional[str]]:
    wants_whash = resource_view_v2.ICON_HASH_METHOD == "whash"
    imagehash_available = bool(getattr(resource_view_v2, "_IMAGEHASH_AVAILABLE", False))
    if wants_whash and not imagehash_available:
        icon_path = _find_icon_file(unpacked_dir)
        if icon_path is not None:
            whash_hex = _compute_native_whash(icon_path)
            if whash_hex is not None:
                token = "{}:whash:{}".format(
                    resource_view_v2.ICON_TOKEN_PREFIX,
                    whash_hex,
                )
                return token, "native_whash", None

    features = resource_view_v2.extract_resource_view_v2(str(unpacked_dir))
    resource_token = features.get("icon_phash")
    parsed = _parse_icon_hash(resource_token)
    if parsed is not None and (not wants_whash or parsed["method"] == "whash"):
        return resource_token, "resource_view_v2", resource_token

    if wants_whash:
        icon_path = _find_icon_file(unpacked_dir)
        if icon_path is not None:
            whash_hex = _compute_native_whash(icon_path)
            if whash_hex is not None:
                token = "{}:whash:{}".format(
                    resource_view_v2.ICON_TOKEN_PREFIX,
                    whash_hex,
                )
                return token, "native_whash", resource_token

    return resource_token, "resource_view_v2", resource_token


def _record_for_apk(apk_path: Path, decoded_root: Optional[Path]) -> Dict[str, Any]:
    decoded_dir = _decoded_dir_for_apk(apk_path, decoded_root)
    package_name = _read_manifest_package(decoded_dir) or _package_name_from_apk_name(apk_path)
    base_record: Dict[str, Any] = {
        "apk_name": apk_path.name,
        "apk_path": str(apk_path),
        "package_name": package_name,
        "decoded_dir": str(decoded_dir) if decoded_dir is not None else None,
        "extraction_source": "decoded_dir" if decoded_dir is not None else "apk_zip",
    }
    try:
        if decoded_dir is not None:
            token, hash_source, resource_token = _extract_icon_token_from_dir(decoded_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="whash-apk-") as tmp:
                unpacked_dir = Path(tmp)
                _extract_apk_resources(apk_path, unpacked_dir)
                token, hash_source, resource_token = _extract_icon_token_from_dir(unpacked_dir)
    except Exception as exc:  # noqa: BLE001 - one bad APK must not kill corpus run
        base_record.update(
            {
                "status": "error",
                "error": "{}: {}".format(type(exc).__name__, exc),
                "icon_phash": None,
                "resource_view_v2_icon_phash": None,
                "hash_source": "error",
            }
        )
        return base_record

    parsed = _parse_icon_hash(token)
    if parsed is None:
        base_record.update(
            {
                "status": "missing_icon_hash",
                "icon_phash": token,
                "resource_view_v2_icon_phash": resource_token,
                "hash_source": hash_source,
                "hash_method": None,
                "hash_hex": None,
            }
        )
        return base_record

    base_record.update(
        {
            "status": "ok",
            "icon_phash": token,
            "resource_view_v2_icon_phash": resource_token,
            "hash_source": hash_source,
            "hash_method": parsed["method"],
            "hash_hex": parsed["hex"],
            "_hash_value": parsed["value"],
        }
    )
    return base_record


def _hamming(value_a: int, value_b: int) -> int:
    return bin(value_a ^ value_b).count("1")


def _distribution(records: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    valid_records = [record for record in records if record.get("status") == "ok"]
    distances: List[int] = []
    distance_counts: Counter[int] = Counter()
    n_cross_package_pairs = 0
    n_collisions = 0
    n_near_duplicates = 0
    n_near_duplicates_cross_package = 0
    n_method_mismatch_pairs = 0

    for left_index, left in enumerate(valid_records):
        for right in valid_records[left_index + 1:]:
            distance = _hamming(int(left["_hash_value"]), int(right["_hash_value"]))
            distances.append(distance)
            distance_counts[distance] += 1
            same_package = left["package_name"] == right["package_name"]
            if left.get("hash_method") != right.get("hash_method"):
                n_method_mismatch_pairs += 1
            if not same_package:
                n_cross_package_pairs += 1
                if distance == 0:
                    n_collisions += 1
            if distance <= NEAR_DUPLICATE_THRESHOLD:
                n_near_duplicates += 1
                if not same_package:
                    n_near_duplicates_cross_package += 1

    n_pairs = len(distances)
    metrics: Dict[str, Any] = {
        "n_hashes": len(valid_records),
        "n_pairs": n_pairs,
        "n_cross_package_pairs": n_cross_package_pairs,
        "n_method_mismatch_pairs": n_method_mismatch_pairs,
        "mean_hamming": statistics.mean(distances) if distances else None,
        "median_hamming": statistics.median(distances) if distances else None,
        "min_hamming": min(distances) if distances else None,
        "max_hamming": max(distances) if distances else None,
        "n_collisions": n_collisions,
        "collision_rate": (
            n_collisions / n_cross_package_pairs if n_cross_package_pairs else 0.0
        ),
        "n_near_duplicates": n_near_duplicates,
        "n_near_duplicates_cross_package": n_near_duplicates_cross_package,
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        "near_duplicate_rate": n_near_duplicates / n_pairs if n_pairs else 0.0,
    }
    histogram = {
        "run_id": RUN_ID,
        "n_pairs": n_pairs,
        "distance_counts": {str(distance): distance_counts.get(distance, 0) for distance in range(HASH_BITS + 1)},
        "bins": [
            {"distance": distance, "count": distance_counts.get(distance, 0)}
            for distance in range(HASH_BITS + 1)
        ],
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
    }
    return metrics, histogram


def _output_paths(out: Path) -> Tuple[Path, Path]:
    out_path = Path(out).expanduser()
    if out_path.suffix.lower() == ".json":
        return out_path, out_path.parent / "histogram.json"
    return out_path / "report.json", out_path / "histogram.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_collision_smoke(
    corpus_dir: Path,
    out: Path,
    fallback_corpus_dir: Optional[Path] = DEFAULT_MINI_CORPUS_DIR,
) -> Dict[str, Any]:
    requested = Path(corpus_dir).expanduser()
    effective, used_fallback, apk_paths = _resolve_corpus_dir(requested, fallback_corpus_dir)
    decoded_root = _decoded_root_for_corpus(apk_paths)
    records = [_record_for_apk(apk_path, decoded_root) for apk_path in apk_paths]
    metrics, histogram = _distribution(records)
    hash_methods = Counter(
        record["hash_method"]
        for record in records
        if record.get("status") == "ok" and record.get("hash_method")
    )
    hash_sources = Counter(
        record["hash_source"]
        for record in records
        if record.get("hash_source")
    )
    report_path, histogram_path = _output_paths(Path(out))
    report: Dict[str, Any] = {
        "run_id": RUN_ID,
        "status": "done" if metrics["n_hashes"] >= 2 else "partial",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "script": SOURCE_PATH,
            "requested_corpus_dir": _display_path(requested),
            "effective_corpus_dir": _display_path(effective),
            "used_fallback": used_fallback,
            "fallback_corpus_dir": (
                _display_path(Path(fallback_corpus_dir))
                if fallback_corpus_dir is not None
                else None
            ),
            "decoded_root": str(decoded_root) if decoded_root is not None else None,
            "resource_view_v2_hash_method": resource_view_v2.ICON_HASH_METHOD,
            "resource_view_v2_imagehash_available": bool(
                getattr(resource_view_v2, "_IMAGEHASH_AVAILABLE", False)
            ),
            "native_whash_available": _PILLOW_AVAILABLE,
        },
        "n_apks": len(apk_paths),
        "n_with_icon_hash": metrics["n_hashes"],
        "n_without_icon_hash": len(apk_paths) - metrics["n_hashes"],
        "hash_methods": dict(sorted(hash_methods.items())),
        "hash_sources": dict(sorted(hash_sources.items())),
        "report_path": str(report_path),
        "histogram_path": str(histogram_path),
        "records": [
            {key: value for key, value in record.items() if key != "_hash_value"}
            for record in records
        ],
    }
    report.update(metrics)
    _write_json(report_path, report)
    _write_json(histogram_path, histogram)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="REPR-27: Hamming-distance distribution for icon wHash on APK corpus."
    )
    parser.add_argument(
        "--corpus_dir",
        default=str(DEFAULT_CORPUS_DIR),
        help="APK corpus directory (default: F-Droid v2 shared cache).",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output directory or report.json path.",
    )
    args = parser.parse_args(argv)
    report = run_collision_smoke(Path(args.corpus_dir), Path(args.out))
    summary = {
        key: report[key]
        for key in (
            "status",
            "n_apks",
            "n_with_icon_hash",
            "n_pairs",
            "mean_hamming",
            "median_hamming",
            "min_hamming",
            "max_hamming",
            "n_collisions",
            "collision_rate",
            "n_near_duplicates",
            "report_path",
            "histogram_path",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

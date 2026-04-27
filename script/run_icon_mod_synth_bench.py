#!/usr/bin/env python3
"""REPR-30 synthetic icon-modification benchmark for launcher icon wHash."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import random
import statistics
import sys
import tempfile
import zipfile
from dataclasses import dataclass
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
    from PIL import Image, ImageEnhance  # type: ignore
except ImportError as exc:  # pragma: no cover - required by task
    raise SystemExit("Pillow is required for REPR-30 icon mod synth bench") from exc

try:
    import imagehash as _imagehash  # type: ignore
    _IMAGEHASH_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _imagehash = None  # type: ignore
    _IMAGEHASH_AVAILABLE = False


RUN_ID = "REPR-30-ICON-MOD-SYNTH"
SOURCE_PATH = "script/run_icon_mod_synth_bench.py"
DEFAULT_CORPUS_DIR = (
    Path.home()
    / "Library"
    / "Caches"
    / "phd-shared"
    / "datasets"
    / "fdroid-corpus-v2-apks"
)
DEFAULT_OUT = _PROJECT_ROOT / "experiments" / "artifacts" / RUN_ID
MOD_TYPES = ("brightness", "scale", "translate", "recompress")
HASH_BITS = 64
HASH_HEX_LEN = HASH_BITS // 4
NEAR_DISTANCE_THRESHOLD = 5


@dataclass(frozen=True)
class IconSample:
    apk_name: str
    apk_path: str
    package_name: str
    icon_rel_path: str
    extraction_source: str
    resource_view_v2_icon_phash: Optional[str]
    image: Image.Image


def _resampling_filter() -> Any:
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS


def _display_path(path: Path) -> str:
    return str(Path(path).expanduser().absolute())


def discover_apks(corpus_dir: Path) -> List[Path]:
    """Return APK files under ``corpus_dir`` in deterministic order."""
    root = Path(corpus_dir).expanduser()
    if not root.is_dir():
        return []
    return sorted(path.resolve() for path in root.rglob("*.apk") if path.is_file())


def _decoded_root_for_corpus(corpus_dir: Path) -> Optional[Path]:
    root = Path(corpus_dir).expanduser()
    if root.name.endswith("-apks"):
        candidate = root.with_name(root.name[:-5] + "-decoded")
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


def _find_icon_file(unpacked_dir: Path) -> Optional[Path]:
    finder = getattr(resource_view_v2, "_find_icon_file", None)
    if finder is None:
        return None
    return finder(unpacked_dir)


def _load_icon_image(icon_path: Path) -> Image.Image:
    with Image.open(icon_path) as img:
        return img.convert("RGBA").copy()


def _icon_rel_path(icon_path: Path, unpacked_dir: Path) -> str:
    try:
        return icon_path.relative_to(unpacked_dir).as_posix()
    except ValueError:
        return str(icon_path)


def _sample_from_unpacked_dir(
    apk_path: Path,
    unpacked_dir: Path,
    extraction_source: str,
    decoded_dir: Optional[Path],
) -> Optional[IconSample]:
    try:
        features = resource_view_v2.extract_resource_view_v2(str(unpacked_dir))
    except Exception:  # noqa: BLE001 - one bad APK must not kill corpus run
        return None
    token = features.get("icon_phash")
    if token is None:
        return None
    icon_path = _find_icon_file(unpacked_dir)
    if icon_path is None:
        return None
    try:
        image = _load_icon_image(icon_path)
    except Exception:  # noqa: BLE001 - decoder failures mean no icon sample
        return None
    package_name = _read_manifest_package(decoded_dir) or _package_name_from_apk_name(apk_path)
    return IconSample(
        apk_name=apk_path.name,
        apk_path=str(apk_path),
        package_name=package_name,
        icon_rel_path=_icon_rel_path(icon_path, unpacked_dir),
        extraction_source=extraction_source,
        resource_view_v2_icon_phash=token,
        image=image,
    )


def collect_icon_samples(
    corpus_dir: Path,
    target_count: int = 30,
    fallback_count: int = 10,
) -> Tuple[List[IconSample], Dict[str, Any]]:
    """Select APKs with launcher icons found by ``resource_view_v2``."""
    requested = Path(corpus_dir).expanduser()
    apk_paths = discover_apks(requested)
    decoded_root = _decoded_root_for_corpus(requested)
    target = max(target_count, fallback_count)
    samples: List[IconSample] = []
    n_errors = 0

    for apk_path in apk_paths:
        if len(samples) >= target:
            break
        decoded_dir = _decoded_dir_for_apk(apk_path, decoded_root)
        try:
            if decoded_dir is not None:
                sample = _sample_from_unpacked_dir(
                    apk_path,
                    decoded_dir,
                    "decoded_dir",
                    decoded_dir,
                )
            else:
                with tempfile.TemporaryDirectory(prefix="icon-mod-apk-") as tmp:
                    unpacked_dir = Path(tmp)
                    _extract_apk_resources(apk_path, unpacked_dir)
                    sample = _sample_from_unpacked_dir(
                        apk_path,
                        unpacked_dir,
                        "apk_zip",
                        None,
                    )
        except Exception:  # noqa: BLE001 - skip malformed APKs
            sample = None
            n_errors += 1
        if sample is not None:
            samples.append(sample)

    if len(samples) >= target_count:
        selected = samples[:target_count]
        selection_mode = "target_30"
    elif len(samples) >= fallback_count:
        selected = samples[:fallback_count]
        selection_mode = "fallback_10"
    else:
        selected = samples
        selection_mode = "partial_under_10"

    metadata = {
        "requested_corpus_dir": _display_path(requested),
        "decoded_root": str(decoded_root) if decoded_root is not None else None,
        "n_apks_discovered": len(apk_paths),
        "n_icon_candidates_collected": len(samples),
        "n_selected_icons": len(selected),
        "target_count": target_count,
        "fallback_count": fallback_count,
        "selection_mode": selection_mode,
        "n_errors": n_errors,
    }
    return selected, metadata


def _flatten_alpha(image: Image.Image, background: Tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, background + (255,))
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def make_icon_modifications(image: Image.Image) -> Dict[str, Image.Image]:
    """Create the four raw-iconicity modifications for one icon."""
    base_rgba = image.convert("RGBA")
    width, height = base_rgba.size
    resample = _resampling_filter()

    brightness = ImageEnhance.Brightness(base_rgba).enhance(1.3)

    scaled_size = (max(1, int(round(width * 1.2))), max(1, int(round(height * 1.2))))
    scale = base_rgba.resize(scaled_size, resample).resize((width, height), resample)

    translated_canvas = Image.new("RGBA", (width + 10, height + 10), (255, 255, 255, 0))
    translated_canvas.alpha_composite(base_rgba, dest=(5, 5))
    translate = translated_canvas.resize((width, height), resample)

    jpeg_buffer = io.BytesIO()
    _flatten_alpha(base_rgba).save(jpeg_buffer, format="JPEG", quality=70)
    jpeg_buffer.seek(0)
    with Image.open(jpeg_buffer) as recompressed:
        png_buffer = io.BytesIO()
        recompressed.save(png_buffer, format="PNG")
        png_buffer.seek(0)
        with Image.open(png_buffer) as png_img:
            recompress = png_img.convert("RGBA").copy()

    return {
        "brightness": brightness,
        "scale": scale,
        "translate": translate,
        "recompress": recompress,
    }


def _native_whash_hex(image: Image.Image) -> str:
    gray = _flatten_alpha(image).convert("L").resize((8, 8), _resampling_filter())
    pixels = list(gray.getdata())
    median = statistics.median(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel > median)
    return "{:0{}x}".format(value, HASH_HEX_LEN)


def compute_image_whash(image: Image.Image) -> int:
    """Compute a 64-bit icon wHash integer for a PIL image."""
    if _IMAGEHASH_AVAILABLE:
        hash_obj = _imagehash.whash(
            _flatten_alpha(image).convert("L"),
            hash_size=8,
            mode="haar",
        )
        return int(str(hash_obj), 16)
    return int(_native_whash_hex(image), 16)


def hamming_distance(hash_a: int, hash_b: int) -> int:
    return bin(hash_a ^ hash_b).count("1")


def _metric_summary(distances: Sequence[int]) -> Dict[str, Any]:
    return {
        "n_pairs": len(distances),
        "mean_hamming": statistics.mean(distances) if distances else None,
        "std_hamming": statistics.pstdev(distances) if len(distances) > 1 else 0.0,
        "max_hamming": max(distances) if distances else None,
        "n_pairs_distance_le_5": sum(
            distance <= NEAR_DISTANCE_THRESHOLD for distance in distances
        ),
    }


def _baseline_pairs(
    n_images: int,
    n_pairs: int,
    random_seed: int,
    group_labels: Optional[Sequence[str]] = None,
) -> List[Tuple[int, int]]:
    candidates = []
    for left in range(n_images):
        for right in range(left + 1, n_images):
            if group_labels is not None and group_labels[left] == group_labels[right]:
                continue
            candidates.append((left, right))
    if not candidates and group_labels is not None:
        candidates = [(left, right) for left in range(n_images) for right in range(left + 1, n_images)]
    rng = random.Random(random_seed)
    if len(candidates) <= n_pairs:
        rng.shuffle(candidates)
        return candidates
    return rng.sample(candidates, n_pairs)


def summarize_icon_mod_distances(
    images: Sequence[Image.Image],
    baseline_pairs: int = 30,
    random_seed: int = 30,
    baseline_group_labels: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return per-modification and unrelated-pair Hamming summaries."""
    hashes = [compute_image_whash(image) for image in images]
    summary: Dict[str, Dict[str, Any]] = {}
    mod_distances: Dict[str, List[int]] = {mod_type: [] for mod_type in MOD_TYPES}

    for image, original_hash in zip(images, hashes):
        modified = make_icon_modifications(image)
        for mod_type in MOD_TYPES:
            mod_hash = compute_image_whash(modified[mod_type])
            mod_distances[mod_type].append(hamming_distance(original_hash, mod_hash))

    for mod_type in MOD_TYPES:
        summary[mod_type] = _metric_summary(mod_distances[mod_type])

    unrelated_distances = [
        hamming_distance(hashes[left], hashes[right])
        for left, right in _baseline_pairs(
            len(hashes),
            baseline_pairs,
            random_seed,
            group_labels=baseline_group_labels,
        )
    ]
    summary["unrelated_pairs_baseline"] = _metric_summary(unrelated_distances)
    return summary


def _pair_records(
    samples: Sequence[IconSample],
    baseline_pairs: int,
    random_seed: int,
) -> Dict[str, Any]:
    original_hashes = [compute_image_whash(sample.image) for sample in samples]
    package_names = [sample.package_name for sample in samples]
    mod_pairs: Dict[str, List[Dict[str, Any]]] = {mod_type: [] for mod_type in MOD_TYPES}

    for sample, original_hash in zip(samples, original_hashes):
        modified = make_icon_modifications(sample.image)
        original_hex = "{:0{}x}".format(original_hash, HASH_HEX_LEN)
        for mod_type in MOD_TYPES:
            mod_hash = compute_image_whash(modified[mod_type])
            mod_pairs[mod_type].append(
                {
                    "apk_name": sample.apk_name,
                    "package_name": sample.package_name,
                    "icon_rel_path": sample.icon_rel_path,
                    "original_whash": original_hex,
                    "modified_whash": "{:0{}x}".format(mod_hash, HASH_HEX_LEN),
                    "hamming": hamming_distance(original_hash, mod_hash),
                }
            )

    unrelated = []
    for left, right in _baseline_pairs(
        len(samples),
        baseline_pairs,
        random_seed,
        group_labels=package_names,
    ):
        left_sample = samples[left]
        right_sample = samples[right]
        unrelated.append(
            {
                "left_apk_name": left_sample.apk_name,
                "right_apk_name": right_sample.apk_name,
                "left_package_name": left_sample.package_name,
                "right_package_name": right_sample.package_name,
                "left_whash": "{:0{}x}".format(original_hashes[left], HASH_HEX_LEN),
                "right_whash": "{:0{}x}".format(original_hashes[right], HASH_HEX_LEN),
                "hamming": hamming_distance(original_hashes[left], original_hashes[right]),
            }
        )

    return {
        "mods": mod_pairs,
        "unrelated_pairs_baseline": unrelated,
    }


def _output_report_path(out: Path) -> Path:
    out_path = Path(out).expanduser()
    if out_path.suffix.lower() == ".json":
        return out_path
    return out_path / "report.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_icon_mod_synth_bench(
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    out: Path = DEFAULT_OUT,
    target_count: int = 30,
    fallback_count: int = 10,
    baseline_pairs: int = 30,
    random_seed: int = 30,
) -> Dict[str, Any]:
    samples, selection = collect_icon_samples(
        corpus_dir,
        target_count=target_count,
        fallback_count=fallback_count,
    )
    images = [sample.image for sample in samples]
    summary = summarize_icon_mod_distances(
        images,
        baseline_pairs=baseline_pairs,
        random_seed=random_seed,
        baseline_group_labels=[sample.package_name for sample in samples],
    )
    pair_records = _pair_records(samples, baseline_pairs=baseline_pairs, random_seed=random_seed)
    report_path = _output_report_path(out)
    status = "done" if selection["n_selected_icons"] >= target_count else "fallback"
    if selection["n_selected_icons"] < fallback_count:
        status = "partial"

    report: Dict[str, Any] = {
        "run_id": RUN_ID,
        "status": status,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "script": SOURCE_PATH,
            "pillow_available": True,
            "imagehash_available": _IMAGEHASH_AVAILABLE,
            "hash_method": "imagehash.whash" if _IMAGEHASH_AVAILABLE else "native_8x8_median_whash",
            "resource_view_v2_hash_method": resource_view_v2.ICON_HASH_METHOD,
        },
        "selection": selection,
        "baseline_pairs_requested": baseline_pairs,
        "random_seed": random_seed,
        "report_path": str(report_path),
        "samples": [
            {
                "apk_name": sample.apk_name,
                "apk_path": sample.apk_path,
                "package_name": sample.package_name,
                "icon_rel_path": sample.icon_rel_path,
                "extraction_source": sample.extraction_source,
                "resource_view_v2_icon_phash": sample.resource_view_v2_icon_phash,
            }
            for sample in samples
        ],
        "pair_records": pair_records,
    }
    report.update(summary)
    _write_json(report_path, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="REPR-30: synthetic launcher icon mod benchmark for wHash."
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
    parser.add_argument("--target_count", type=int, default=30)
    parser.add_argument("--fallback_count", type=int, default=10)
    parser.add_argument("--baseline_pairs", type=int, default=30)
    parser.add_argument("--random_seed", type=int, default=30)
    args = parser.parse_args(argv)

    report = run_icon_mod_synth_bench(
        corpus_dir=Path(args.corpus_dir),
        out=Path(args.out),
        target_count=args.target_count,
        fallback_count=args.fallback_count,
        baseline_pairs=args.baseline_pairs,
        random_seed=args.random_seed,
    )
    summary = {
        "status": report["status"],
        "selection_mode": report["selection"]["selection_mode"],
        "n_selected_icons": report["selection"]["n_selected_icons"],
        "brightness": report["brightness"],
        "scale": report["scale"],
        "translate": report["translate"],
        "recompress": report["recompress"],
        "unrelated_pairs_baseline": report["unrelated_pairs_baseline"],
        "report_path": report["report_path"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""REPR-31 JPEG quality recompress benchmark for launcher icon wHash."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from script import run_icon_mod_synth_bench as repr30
except ImportError:  # pragma: no cover - standalone script fallback
    import run_icon_mod_synth_bench as repr30  # type: ignore[no-redef]

try:
    from PIL import Image  # type: ignore
except ImportError as exc:  # pragma: no cover - required by task
    raise SystemExit("Pillow is required for REPR-31 resource recompress bench") from exc


RUN_ID = "REPR-31-RECOMPRESS-MODS"
SOURCE_PATH = "script/run_resource_recompress_bench.py"
DEFAULT_CORPUS_DIR = repr30.DEFAULT_CORPUS_DIR
DEFAULT_OUT = _PROJECT_ROOT / "experiments" / "artifacts" / RUN_ID
REPR30_REPORT_PATH = (
    _PROJECT_ROOT
    / "experiments"
    / "artifacts"
    / "REPR-30-ICON-MOD-SYNTH"
    / "report.json"
)
JPEG_QUALITIES = (30, 50, 70, 90)


def _quality_key(quality: int) -> str:
    return "q{}".format(quality)


def recompress_icon_as_png(image: Image.Image, quality: int) -> Image.Image:
    """Round-trip a PIL image through JPEG at ``quality`` and decode it as PNG."""
    jpeg_buffer = io.BytesIO()
    repr30._flatten_alpha(image).save(jpeg_buffer, format="JPEG", quality=quality)
    jpeg_buffer.seek(0)

    with Image.open(jpeg_buffer) as jpeg_image:
        png_buffer = io.BytesIO()
        jpeg_image.save(png_buffer, format="PNG")
        png_buffer.seek(0)
        with Image.open(png_buffer) as png_image:
            return png_image.convert("RGBA").copy()


def recompress_hamming_distance(image: Image.Image, quality: int) -> int:
    original_hash = repr30.compute_image_whash(image)
    recompressed_hash = repr30.compute_image_whash(recompress_icon_as_png(image, quality))
    return repr30.hamming_distance(original_hash, recompressed_hash)


def summarize_recompress_distances(
    images: Sequence[Image.Image],
    baseline_pairs: int = 30,
    random_seed: int = 31,
    baseline_group_labels: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return q30/q50/q70/q90 and unrelated-pair Hamming summaries."""
    hashes = [repr30.compute_image_whash(image) for image in images]
    summary: Dict[str, Dict[str, Any]] = {}

    for quality in JPEG_QUALITIES:
        distances = [
            repr30.hamming_distance(
                original_hash,
                repr30.compute_image_whash(recompress_icon_as_png(image, quality)),
            )
            for image, original_hash in zip(images, hashes)
        ]
        summary[_quality_key(quality)] = repr30._metric_summary(distances)

    unrelated_distances = [
        repr30.hamming_distance(hashes[left], hashes[right])
        for left, right in repr30._baseline_pairs(
            len(hashes),
            baseline_pairs,
            random_seed,
            group_labels=baseline_group_labels,
        )
    ]
    summary["unrelated_pairs_baseline"] = repr30._metric_summary(unrelated_distances)
    return summary


def _pair_records(
    samples: Sequence[repr30.IconSample],
    baseline_pairs: int,
    random_seed: int,
) -> Dict[str, Any]:
    original_hashes = [repr30.compute_image_whash(sample.image) for sample in samples]
    package_names = [sample.package_name for sample in samples]
    recompress_pairs: Dict[str, List[Dict[str, Any]]] = {
        _quality_key(quality): [] for quality in JPEG_QUALITIES
    }

    for sample, original_hash in zip(samples, original_hashes):
        original_hex = "{:0{}x}".format(original_hash, repr30.HASH_HEX_LEN)
        for quality in JPEG_QUALITIES:
            recompressed_hash = repr30.compute_image_whash(
                recompress_icon_as_png(sample.image, quality)
            )
            recompress_pairs[_quality_key(quality)].append(
                {
                    "apk_name": sample.apk_name,
                    "package_name": sample.package_name,
                    "icon_rel_path": sample.icon_rel_path,
                    "jpeg_quality": quality,
                    "original_whash": original_hex,
                    "modified_whash": "{:0{}x}".format(
                        recompressed_hash,
                        repr30.HASH_HEX_LEN,
                    ),
                    "hamming": repr30.hamming_distance(original_hash, recompressed_hash),
                }
            )

    unrelated = []
    for left, right in repr30._baseline_pairs(
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
                "left_whash": "{:0{}x}".format(original_hashes[left], repr30.HASH_HEX_LEN),
                "right_whash": "{:0{}x}".format(original_hashes[right], repr30.HASH_HEX_LEN),
                "hamming": repr30.hamming_distance(original_hashes[left], original_hashes[right]),
            }
        )

    return {
        "recompress": recompress_pairs,
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


def _brightness_q30_diagnostic(summary: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    repr30_brightness: Optional[Dict[str, Any]] = None
    if REPR30_REPORT_PATH.is_file():
        try:
            previous = json.loads(REPR30_REPORT_PATH.read_text(encoding="utf-8"))
            brightness = previous.get("brightness")
            if isinstance(brightness, dict):
                repr30_brightness = {
                    "source_report": str(REPR30_REPORT_PATH),
                    "mean_hamming": brightness.get("mean_hamming"),
                    "n_pairs_distance_le_5": brightness.get("n_pairs_distance_le_5"),
                    "n_pairs": brightness.get("n_pairs"),
                    "max_hamming": brightness.get("max_hamming"),
                }
        except (OSError, json.JSONDecodeError):
            repr30_brightness = None

    q30 = summary["q30"]
    diagnostic: Dict[str, Any] = {
        "repr31_q30": {
            "mean_hamming": q30["mean_hamming"],
            "n_pairs_distance_le_5": q30["n_pairs_distance_le_5"],
            "n_pairs": q30["n_pairs"],
            "max_hamming": q30["max_hamming"],
        },
        "repr30_brightness": repr30_brightness,
    }
    if repr30_brightness is not None and repr30_brightness["mean_hamming"] is not None:
        diagnostic["mean_delta_brightness_minus_q30"] = (
            repr30_brightness["mean_hamming"] - q30["mean_hamming"]
        )
    return diagnostic


def run_resource_recompress_bench(
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    out: Path = DEFAULT_OUT,
    target_count: int = 30,
    fallback_count: int = 10,
    baseline_pairs: int = 30,
    random_seed: int = 31,
) -> Dict[str, Any]:
    samples, selection = repr30.collect_icon_samples(
        corpus_dir,
        target_count=target_count,
        fallback_count=fallback_count,
    )
    images = [sample.image for sample in samples]
    package_names = [sample.package_name for sample in samples]
    summary = summarize_recompress_distances(
        images,
        baseline_pairs=baseline_pairs,
        random_seed=random_seed,
        baseline_group_labels=package_names,
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
            "extended_from": repr30.SOURCE_PATH,
            "pillow_available": True,
            "imagehash_available": repr30._IMAGEHASH_AVAILABLE,
            "hash_method": "imagehash.whash"
            if repr30._IMAGEHASH_AVAILABLE
            else "native_8x8_median_whash",
            "resource_view_v2_hash_method": repr30.resource_view_v2.ICON_HASH_METHOD,
        },
        "selection": selection,
        "jpeg_qualities": list(JPEG_QUALITIES),
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
        "diagnostics": {
            "brightness_vs_aggressive_jpeg_q30": _brightness_q30_diagnostic(summary),
        },
        "pair_records": pair_records,
    }
    report.update(summary)
    _write_json(report_path, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="REPR-31: JPEG quality recompress launcher icon benchmark for wHash."
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
    parser.add_argument("--random_seed", type=int, default=31)
    args = parser.parse_args(argv)

    report = run_resource_recompress_bench(
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
        "q30": report["q30"],
        "q50": report["q50"],
        "q70": report["q70"],
        "q90": report["q90"],
        "unrelated_pairs_baseline": report["unrelated_pairs_baseline"],
        "report_path": report["report_path"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

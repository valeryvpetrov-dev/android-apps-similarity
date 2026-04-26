#!/usr/bin/env python3
"""Build REPR-24 IDF snapshot v2 from an APK corpus."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from screening_runner import extract_layers_from_apk
except ImportError:  # pragma: no cover - package import fallback
    from script.screening_runner import extract_layers_from_apk

try:
    from library_view_v2 import detect_tpl_in_packages, extract_library_features_v2
except ImportError:  # pragma: no cover - package import fallback
    try:
        from script.library_view_v2 import detect_tpl_in_packages, extract_library_features_v2
    except ImportError:  # pragma: no cover - optional library detection
        detect_tpl_in_packages = None  # type: ignore[assignment]
        extract_library_features_v2 = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "experiments" / "datasets" / "idf-snapshot-v2.json"
DEFAULT_LAYERS = ("library", "component", "resource")


def _discover_apks(corpus_dir: Path) -> list[Path]:
    return sorted(path for path in corpus_dir.rglob("*.apk") if path.is_file())


def _parse_layers(raw_layers: str) -> list[str]:
    layers = [layer.strip() for layer in raw_layers.split(",") if layer.strip()]
    if not layers:
        raise ValueError("--layers must contain at least one layer")
    return layers


def _normalize_tokens(raw_tokens: object) -> set[str]:
    if raw_tokens is None:
        return set()
    if isinstance(raw_tokens, (str, bytes)):
        return {str(raw_tokens)}
    try:
        iterator = iter(raw_tokens)  # type: ignore[arg-type]
    except TypeError:
        return set()
    tokens: set[str] = set()
    for token in iterator:
        if isinstance(token, str) and token:
            tokens.add(token)
    return tokens


def _decoded_root_for_corpus(corpus_dir: Path) -> Path | None:
    configured = os.environ.get("SIMILARITY_DECODED_CORPUS_DIR", "").strip()
    if configured:
        decoded_root = Path(configured).expanduser().resolve()
        return decoded_root if decoded_root.is_dir() else None
    if corpus_dir.name.endswith("-apks"):
        sibling = corpus_dir.with_name(corpus_dir.name[:-5] + "-decoded")
        if sibling.is_dir():
            return sibling
    return None


def _smali_path_to_package(decoded_dir: Path, smali_path: Path) -> str | None:
    try:
        rel_parts = smali_path.relative_to(decoded_dir).parts
    except ValueError:
        return None
    if len(rel_parts) < 3:
        return None
    smali_root = rel_parts[0]
    if smali_root != "smali" and not smali_root.startswith("smali_classes"):
        return None
    package_parts = rel_parts[1:-1]
    if not package_parts:
        return None
    return ".".join(part for part in package_parts if part)


def _extract_packages_from_decoded_dir(decoded_dir: Path) -> frozenset[str]:
    packages: set[str] = set()
    for smali_root in sorted(decoded_dir.iterdir()):
        if not smali_root.is_dir():
            continue
        if smali_root.name != "smali" and not smali_root.name.startswith("smali_classes"):
            continue
        for smali_path in smali_root.rglob("*.smali"):
            package_name = _smali_path_to_package(decoded_dir, smali_path)
            if package_name:
                packages.add(package_name)
    return frozenset(packages)


def _detect_libraries_from_decoded_dir(decoded_dir: Path) -> set[str]:
    if detect_tpl_in_packages is None:
        return set()
    packages = _extract_packages_from_decoded_dir(decoded_dir)
    if not packages:
        return set()
    detections = detect_tpl_in_packages(packages)
    return {
        tpl_id
        for tpl_id, info in detections.items()
        if isinstance(info, dict) and info.get("detected")
    }


def _detect_libraries_from_apk(apk_path: Path) -> set[str]:
    if extract_library_features_v2 is None:
        return set()
    try:
        features = extract_library_features_v2(str(apk_path))
    except Exception:
        return set()
    libraries = features.get("libraries", {}) if isinstance(features, dict) else {}
    if not isinstance(libraries, dict):
        return set()
    return {token for token in libraries if isinstance(token, str) and token}


def _library_tokens(
    apk_path: Path,
    corpus_dir: Path,
    quick_layers: dict[str, set[str]],
) -> set[str]:
    decoded_root = _decoded_root_for_corpus(corpus_dir)
    if decoded_root is not None:
        decoded_dir = decoded_root / apk_path.stem
        if decoded_dir.is_dir():
            return _detect_libraries_from_decoded_dir(decoded_dir)
        return set()

    detected_from_apk = _detect_libraries_from_apk(apk_path)
    if detected_from_apk:
        return detected_from_apk
    return _normalize_tokens(quick_layers.get("library"))


def _deterministic_built_at(apk_paths: Sequence[Path]) -> str:
    if not apk_paths:
        return "1970-01-01T00:00:00Z"
    newest_mtime = max(path.stat().st_mtime for path in apk_paths)
    dt = datetime.fromtimestamp(newest_mtime, timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _layer_payload(counter: Counter[str]) -> dict:
    document_frequency = {
        token: int(counter[token])
        for token in sorted(counter)
    }
    return {
        "n_tokens": len(document_frequency),
        "document_frequency": document_frequency,
    }


def build_idf_snapshot(
    corpus_dir: str | Path,
    layers: Iterable[str],
    out_path: str | Path,
) -> dict:
    """Scan APKs and write a deterministic IDF document-frequency snapshot."""
    resolved_corpus_dir = Path(corpus_dir).expanduser().resolve()
    if not resolved_corpus_dir.is_dir():
        raise FileNotFoundError(f"corpus_dir does not exist: {resolved_corpus_dir}")

    layer_names = [str(layer).strip() for layer in layers if str(layer).strip()]
    if not layer_names:
        raise ValueError("layers must contain at least one layer")

    apk_paths = _discover_apks(resolved_corpus_dir)
    if not apk_paths:
        raise ValueError(f"no APK files found under: {resolved_corpus_dir}")

    counters: dict[str, Counter[str]] = {
        layer_name: Counter()
        for layer_name in layer_names
    }

    for apk_path in apk_paths:
        extracted_layers = extract_layers_from_apk(apk_path)
        for layer_name in layer_names:
            if layer_name == "library":
                tokens = _library_tokens(apk_path, resolved_corpus_dir, extracted_layers)
            else:
                tokens = _normalize_tokens(extracted_layers.get(layer_name))
            counters[layer_name].update(tokens)

    payload: dict = {
        "snapshot_version": "v2",
        "n_documents": len(apk_paths),
        "source": resolved_corpus_dir.name,
        "built_at": _deterministic_built_at(apk_paths),
    }

    warnings: list[str] = []
    for layer_name in layer_names:
        layer = _layer_payload(counters[layer_name])
        if layer["n_tokens"] == 0:
            warnings.append(
                f"{layer_name}: n_tokens=0; layer omitted from snapshot"
            )
            continue
        payload[layer_name] = layer

    if warnings:
        payload["warnings"] = warnings

    resolved_out_path = Path(out_path).expanduser()
    resolved_out_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build IDF snapshot v2 from an APK corpus."
    )
    parser.add_argument("--corpus_dir", required=True, help="Directory with APK corpus")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output JSON path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--layers",
        default=",".join(DEFAULT_LAYERS),
        help="Comma-separated layers (default: library,component,resource)",
    )
    args = parser.parse_args(argv)

    try:
        build_idf_snapshot(args.corpus_dir, _parse_layers(args.layers), args.out)
    except Exception as exc:
        print(f"build_idf_snapshot_v2: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

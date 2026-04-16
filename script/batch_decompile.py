#!/usr/bin/env python3
"""batch_decompile.py — DEEP-003 mass APK decompilation via apktool.

Decompiles APK files using apktool, skipping those already decoded.
Supports optional filtering by a shortlist JSON (pairs format).

Usage:
    python script/batch_decompile.py \\
        --apk-dir path/to/apks \\
        --output-dir path/to/decoded \\
        [--pairs path/to/shortlist.json] \\
        [--apktool /opt/homebrew/bin/apktool] \\
        [--force]

Exit codes:
    0 — all target APKs decoded (or skipped)
    1 — one or more APKs failed to decode
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


APKTOOL_DEFAULT = "apktool"

# Keys used in shortlist / enriched-candidate JSON to identify APK paths
_APK_PATH_KEYS = (
    "apk_path",
    "apk",
    "path",
    "app_path",
    "artifact_path",
)
_A_APK_KEYS = (
    "app_a_apk_path",
    "apk_a_path",
    "apk_1",
    "query_apk_path",
    "query_app_apk_path",
    "app_a_path",
)
_B_APK_KEYS = (
    "app_b_apk_path",
    "apk_b_path",
    "apk_2",
    "candidate_apk_path",
    "candidate_app_apk_path",
    "app_b_path",
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="batch_decompile.py",
        description=(
            "Mass APK decompilation via apktool. "
            "Skips APKs whose decoded directory already exists."
        ),
    )
    parser.add_argument(
        "--apk-dir",
        required=True,
        help="Directory containing APK files (searched recursively).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where decoded APK directories will be written.",
    )
    parser.add_argument(
        "--pairs",
        default=None,
        help=(
            "Optional path to shortlist/pairs JSON. "
            "When provided, only APKs referenced in the JSON are decompiled."
        ),
    )
    parser.add_argument(
        "--apktool",
        default=APKTOOL_DEFAULT,
        help=f"Path to apktool executable (default: {APKTOOL_DEFAULT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-decode even if output directory already exists (passes -f to apktool).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Shortlist / pairs JSON parsing
# ---------------------------------------------------------------------------

def _extract_apk_stems_from_app(app: Any) -> set[str]:
    """Return APK stem(s) referenced by an app entry (dict or str)."""
    stems: set[str] = set()
    if isinstance(app, str) and app.lower().endswith(".apk"):
        stems.add(Path(app).stem)
    elif isinstance(app, dict):
        for key in _APK_PATH_KEYS:
            value = app.get(key)
            if isinstance(value, str) and value.lower().endswith(".apk"):
                stems.add(Path(value).stem)
    return stems


def _extract_apk_stems_from_candidate(candidate: dict[str, Any]) -> set[str]:
    stems: set[str] = set()

    # Direct side keys
    for key in (*_A_APK_KEYS, *_B_APK_KEYS):
        value = candidate.get(key)
        if isinstance(value, str) and value.lower().endswith(".apk"):
            stems.add(Path(value).stem)

    # Nested app_a / app_b
    for side_key in ("app_a", "app_b", "query_app", "candidate_app"):
        app = candidate.get(side_key)
        stems.update(_extract_apk_stems_from_app(app))

    # apps: { app_a: ..., app_b: ... }
    apps = candidate.get("apps")
    if isinstance(apps, dict):
        for side_key in ("app_a", "app_b", "query_app", "candidate_app"):
            app = apps.get(side_key)
            stems.update(_extract_apk_stems_from_app(app))

    return stems


def load_target_stems(pairs_path: Path) -> set[str] | None:
    """Parse pairs JSON and return a set of APK stems to decompile.

    Returns None if pairs_path is None (meaning: decompile everything).
    Returns empty set if JSON is valid but contains no APK references.
    """
    payload = json.loads(pairs_path.read_text(encoding="utf-8"))
    items: list[Any] = []

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in (
            "enriched_candidates",
            "candidate_list",
            "candidates",
            "short_list",
            "shortlist",
            "items",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if not items:
            # Could be a single pair object
            if "app_a" in payload or "app_b" in payload:
                items = [payload]

    stems: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            stems.update(_extract_apk_stems_from_candidate(item))
    return stems


# ---------------------------------------------------------------------------
# Decompilation core
# ---------------------------------------------------------------------------

def collect_apk_paths(apk_dir: Path) -> list[Path]:
    """Return sorted list of APK files found recursively in apk_dir."""
    return sorted(apk_dir.rglob("*.apk"))


def decoded_dir_for(apk_path: Path, output_dir: Path) -> Path:
    """Return the expected decoded directory path for a given APK."""
    return output_dir / apk_path.stem


def decompile_apk(
    apk_path: Path,
    decoded_dir: Path,
    apktool: str,
    force: bool = False,
) -> tuple[bool, str]:
    """Invoke apktool to decompile a single APK.

    Returns (success: bool, message: str).
    """
    cmd = [apktool, "d", "-f", "-o", str(decoded_dir), str(apk_path)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min per APK
        )
        if result.returncode == 0:
            return True, "ok"
        stderr_snippet = (result.stderr or "").strip()[:300]
        return False, f"apktool exit {result.returncode}: {stderr_snippet}"
    except FileNotFoundError:
        return False, f"apktool not found at: {apktool!r}"
    except subprocess.TimeoutExpired:
        return False, "apktool timed out after 300 s"
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected error: {exc}"


def run_batch(
    apk_dir: Path,
    output_dir: Path,
    apktool: str,
    target_stems: set[str] | None,
    force: bool = False,
) -> dict[str, Any]:
    """Run batch decompilation.

    Returns summary dict with keys: decoded, skipped, failed, errors.
    """
    all_apks = collect_apk_paths(apk_dir)
    if target_stems is not None:
        apks = [p for p in all_apks if p.stem in target_stems]
    else:
        apks = all_apks

    total = len(apks)
    decoded_count = 0
    skipped_count = 0
    failed_count = 0
    errors: list[dict[str, str]] = []

    output_dir.mkdir(parents=True, exist_ok=True)

    for index, apk_path in enumerate(apks, start=1):
        dest = decoded_dir_for(apk_path, output_dir)
        prefix = f"[{index}/{total}]"

        # Skip if already decoded and --force not set
        if dest.exists() and not force:
            print(f"{prefix} SKIP  {apk_path.name} -> {dest.name} (already decoded)")
            skipped_count += 1
            continue

        print(f"{prefix} DECODE {apk_path.name} -> {dest.name} ...", end=" ", flush=True)
        success, msg = decompile_apk(apk_path, dest, apktool, force=force)
        if success:
            print("OK")
            decoded_count += 1
        else:
            print(f"FAIL ({msg})")
            failed_count += 1
            errors.append({"apk": str(apk_path), "error": msg})

    return {
        "total": total,
        "decoded": decoded_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    apk_dir = Path(args.apk_dir)
    output_dir = Path(args.output_dir)

    if not apk_dir.is_dir():
        print(f"ERROR: --apk-dir not found: {apk_dir}", file=sys.stderr)
        sys.exit(1)

    target_stems: set[str] | None = None
    if args.pairs:
        pairs_path = Path(args.pairs)
        if not pairs_path.is_file():
            print(f"ERROR: --pairs file not found: {pairs_path}", file=sys.stderr)
            sys.exit(1)
        target_stems = load_target_stems(pairs_path)
        print(f"Pairs filter active: {len(target_stems)} unique APK stems from {pairs_path.name}")

    summary = run_batch(
        apk_dir=apk_dir,
        output_dir=output_dir,
        apktool=args.apktool,
        target_stems=target_stems,
        force=args.force,
    )

    print()
    print("=== Summary ===")
    print(f"  Total   : {summary['total']}")
    print(f"  Decoded : {summary['decoded']}")
    print(f"  Skipped : {summary['skipped']}")
    print(f"  Failed  : {summary['failed']}")

    if summary["errors"]:
        print()
        print("Failed APKs:")
        for entry in summary["errors"]:
            print(f"  {entry['apk']}: {entry['error']}")

    if summary["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

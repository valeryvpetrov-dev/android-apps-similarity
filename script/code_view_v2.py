#!/usr/bin/env python3
"""SOTA-001: code_view_v2 — opcode n-gram + TLSH fuzzy hash similarity.

Replaces v1 (DEX filename Jaccard) with:
  1. Dalvik opcode extraction via androguard
  2. Opcode n-gram bag (window=5) across all methods in APK
  3. TLSH fuzzy hash over concatenated n-gram representation
  4. Similarity: 1 - normalized_tlsh_distance(hash_a, hash_b) → [0, 1]

Key advantage: obfuscation-resistant (rename-invariant) — opcodes survive
method/class renames, unlike DEX filename or symbol-name based features.

Dependencies:
  androguard >= 4.0  (pip install androguard)
  py-tlsh            (pip install py-tlsh)
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency guards
# ---------------------------------------------------------------------------

try:
    from androguard.misc import AnalyzeAPK
    _ANDROGUARD_AVAILABLE = True
except ImportError:
    _ANDROGUARD_AVAILABLE = False
    logger.warning(
        "androguard is not installed. "
        "Install with: pip install androguard\n"
        "code_view_v2 will return fallback scores."
    )

try:
    import tlsh as _tlsh_module
    _TLSH_AVAILABLE = True
except ImportError:
    _TLSH_AVAILABLE = False
    logger.warning(
        "py-tlsh is not installed. "
        "Install with: pip install py-tlsh\n"
        "code_view_v2 will return fallback scores."
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NGRAM_WINDOW: int = 5
# TLSH distance range is 0..900+; 300 maps roughly to ~50% structural overlap.
TLSH_NORM_DIVISOR: int = 300
# Minimum bytes for TLSH (library requires >= 50 bytes)
TLSH_MIN_BYTES: int = 50


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def _collect_library_packages(apk_path: Path) -> frozenset:
    """Return flat set of packages belonging to detected third-party libraries.

    EXEC-075: Used for library-subtraction screening (app_only mode).
    If detection fails, returns empty frozenset — caller falls back to full opcode extraction.
    """
    try:
        from library_view_v2 import extract_apk_packages, detect_tpl_in_packages
    except ImportError:
        logger.warning("library_view_v2 not importable; library-subtraction disabled")
        return frozenset()

    try:
        apk_packages = extract_apk_packages(str(apk_path))
        tpl_hits = detect_tpl_in_packages(apk_packages)
    except Exception as exc:
        logger.warning("library detection failed for %s: %s", apk_path, exc)
        return frozenset()

    library_packages: set = set()
    for tpl_id, hit in tpl_hits.items():
        if hit.get("detected"):
            library_packages.update(hit.get("matched_packages", set()))
    return frozenset(library_packages)


def _extract_opcodes_from_apk(
    apk_path: Path,
    app_only: bool = False,
) -> list[str]:
    """Return flat list of Dalvik opcode names across all methods in APK.

    Uses androguard AnalyzeAPK which parses all DEX files inside the APK.
    Each instruction's mnemonic (opcode name) is collected in method order.

    Args:
        apk_path: Path to the .apk file.
        app_only: If True (EXEC-075), skip methods whose declaring class belongs
                  to a detected third-party library (via library_view_v2). This
                  isolates app-specific code and reduces TLSH FPR caused by
                  shared libraries (Jetpack Compose, Material3, etc.).
    """
    if not _ANDROGUARD_AVAILABLE:
        raise RuntimeError("androguard is not available")

    library_packages: frozenset = frozenset()
    if app_only:
        library_packages = _collect_library_packages(apk_path)

    # Lazy import to avoid hard dependency when app_only=False.
    try:
        from library_view_v2 import _smali_class_to_package
    except ImportError:
        _smali_class_to_package = None  # type: ignore[assignment]

    _, _, dx = AnalyzeAPK(str(apk_path))
    opcodes: list[str] = []
    skipped_library_methods = 0

    for method in dx.get_methods():
        # Skip external methods (Android framework / library calls) — no bytecode
        if method.is_external():
            continue

        # EXEC-075: library-subtraction — skip methods of detected TPL classes.
        if app_only and library_packages and _smali_class_to_package is not None:
            class_name = method.get_class_name()
            pkg = _smali_class_to_package(class_name) if class_name else None
            if pkg is not None:
                # exact match or prefix match ("androidx.compose.ui.node.X" vs "androidx.compose.ui")
                if pkg in library_packages or any(
                    pkg.startswith(lp + ".") for lp in library_packages
                ):
                    skipped_library_methods += 1
                    continue

        encoded_method = method.get_method()
        try:
            code = encoded_method.get_code()
        except AttributeError:
            continue
        if code is None:
            continue
        bc = code.get_bc()
        if bc is None:
            continue
        for instr in bc.get_instructions():
            opcodes.append(instr.get_name())

    if app_only and skipped_library_methods > 0:
        logger.info(
            "%s: library-subtraction skipped %d methods of detected TPLs",
            apk_path.name,
            skipped_library_methods,
        )

    return opcodes


def _build_ngrams(opcodes: list[str], window: int = NGRAM_WINDOW) -> list[tuple[str, ...]]:
    """Build n-gram tuples from flat opcode list."""
    if len(opcodes) < window:
        return []
    return list(zip(*[opcodes[i:] for i in range(window)]))


def _ngrams_to_bytes(ngrams: list[tuple[str, ...]]) -> bytes:
    """Encode n-gram list to bytes for TLSH hashing."""
    parts = [" ".join(gram) for gram in ngrams]
    return " | ".join(parts).encode("utf-8", errors="replace")


def extract_opcode_ngram_tlsh(
    apk_path: Path,
    window: int = NGRAM_WINDOW,
    app_only: bool = False,
) -> Optional[str]:
    """Extract TLSH hash from Dalvik opcode n-grams of an APK.

    Args:
        apk_path: Path to the .apk file.
        window:   N-gram window size (default 5).
        app_only: EXEC-075. If True, exclude methods of detected third-party
                  libraries from the opcode stream before hashing. Reduces
                  screening FPR caused by shared-library TLSH overlap.

    Returns:
        TLSH hash string, or None on error / insufficient data.
    """
    if not _ANDROGUARD_AVAILABLE or not _TLSH_AVAILABLE:
        logger.error(
            "Cannot extract TLSH hash: missing dependency "
            "(androguard=%s, tlsh=%s)",
            _ANDROGUARD_AVAILABLE,
            _TLSH_AVAILABLE,
        )
        return None

    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    try:
        opcodes = _extract_opcodes_from_apk(apk_path, app_only=app_only)
    except Exception as exc:
        logger.error("Failed to extract opcodes from %s: %s", apk_path, exc)
        return None

    if not opcodes:
        logger.warning("No opcodes found in %s", apk_path)
        return None

    ngrams = _build_ngrams(opcodes, window)
    if not ngrams:
        logger.warning(
            "Not enough opcodes (%d) for window=%d in %s",
            len(opcodes),
            window,
            apk_path,
        )
        return None

    data = _ngrams_to_bytes(ngrams)

    if len(data) < TLSH_MIN_BYTES:
        logger.warning(
            "Encoded n-gram data too short (%d bytes, need >= %d) for TLSH in %s",
            len(data),
            TLSH_MIN_BYTES,
            apk_path,
        )
        return None

    try:
        h = _tlsh_module.hash(data)
        # tlsh.hash() returns "TNULL" or empty string on failure
        if not h or h == "TNULL":
            logger.warning("TLSH returned null hash for %s", apk_path)
            return None
        return h
    except Exception as exc:
        logger.error("TLSH hashing failed for %s: %s", apk_path, exc)
        return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_code_v2(
    hash_a: Optional[str],
    hash_b: Optional[str],
) -> dict:
    """Compare two TLSH hashes and return similarity score in [0, 1].

    Args:
        hash_a: TLSH hash string from extract_opcode_ngram_tlsh(), or None.
        hash_b: TLSH hash string from extract_opcode_ngram_tlsh(), or None.

    Returns:
        Dict with keys:
          "score":  float in [0, 1]  (1 = identical, 0 = completely different)
          "status": str — one of:
            "tlsh_ok"             both hashes valid, comparison successful
            "tlsh_fallback_empty" one or both hashes are None / empty
            "tlsh_error"          TLSH library comparison raised an exception
    """
    if not hash_a or not hash_b:
        return {"score": 0.0, "status": "tlsh_fallback_empty"}

    if not _TLSH_AVAILABLE:
        return {"score": 0.0, "status": "tlsh_error"}

    try:
        diff = _tlsh_module.diff(hash_a, hash_b)
        # diff == 0 → identical; higher → more different
        score = max(0.0, 1.0 - diff / TLSH_NORM_DIVISOR)
        return {"score": round(score, 6), "status": "tlsh_ok"}
    except Exception as exc:
        logger.error("TLSH diff failed: %s", exc)
        return {"score": 0.0, "status": "tlsh_error"}


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="code_view_v2",
        description=(
            "SOTA-001: compare two APKs using opcode n-gram TLSH fuzzy hash."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    parser.add_argument(
        "--window", type=int, default=NGRAM_WINDOW, help="N-gram window size"
    )
    args = parser.parse_args()

    import json

    hash_a = extract_opcode_ngram_tlsh(Path(args.apk_a), window=args.window)
    hash_b = extract_opcode_ngram_tlsh(Path(args.apk_b), window=args.window)
    result = compare_code_v2(hash_a, hash_b)
    result["hash_a"] = hash_a
    result["hash_b"] = hash_b
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

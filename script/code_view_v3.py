#!/usr/bin/env python3
"""
R_code v3: Method-level opcode multiset similarity.
Approach inspired by MOSDroid (Computers & Security, 2025).

Key idea: represent APK as a set of per-method opcode sequences,
compare APKs via Jaccard similarity on these sets.
Invariant to DEX packaging (single-dex vs multi-dex).
"""
from __future__ import annotations

import logging
import sys
from collections import Counter
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
        "code_view_v3 will return fallback scores."
    )


# ── Feature extraction ──────────────────────────────────────────────────────

def _extract_method_opcode_sets(apk_path: Path) -> list[tuple[str, ...]]:
    """
    Extract list of per-method opcode sequences from APK.
    Returns list of tuples, one per internal method.
    Uses androguard if available; falls back gracefully.
    """
    if not _ANDROGUARD_AVAILABLE:
        logger.error("androguard is not available; cannot extract method opcodes")
        return []

    try:
        _, _, dx = AnalyzeAPK(str(apk_path))
    except Exception as exc:
        logger.error("AnalyzeAPK failed for %s: %s", apk_path, exc)
        return []

    method_sequences = []
    for method in dx.get_methods():
        if method.is_external():
            continue
        encoded = method.get_method()
        try:
            code = encoded.get_code()
        except AttributeError:
            continue
        if code is None:
            continue
        bc = code.get_bc()
        if bc is None:
            continue
        opcodes = tuple(instr.get_name() for instr in bc.get_instructions())
        if opcodes:  # пропускаем пустые методы
            method_sequences.append(opcodes)

    return method_sequences


def extract_method_opcode_fingerprint(apk_path: Path) -> Optional[frozenset]:
    """
    Extract frozenset of unique per-method opcode tuples.
    frozenset = set representation (ignores duplicate methods).
    Returns None if extraction failed or empty.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    sequences = _extract_method_opcode_sets(apk_path)
    if not sequences:
        logger.warning("No method opcode sequences found in %s", apk_path)
        return None
    return frozenset(sequences)


def extract_method_opcode_multiset(apk_path: Path) -> Optional[dict]:
    """
    Extract dict[opcode_tuple -> count] (multiset representation).
    Useful for weighted comparison.
    Returns None if extraction failed or empty.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    sequences = _extract_method_opcode_sets(apk_path)
    if not sequences:
        return None
    return dict(Counter(sequences))


# ── Similarity ───────────────────────────────────────────────────────────────

def compare_code_v3(
    set_a: Optional[frozenset],
    set_b: Optional[frozenset],
) -> dict:
    """
    Compare two method opcode fingerprints via Jaccard similarity.

    Returns:
        {"score": float [0..1], "status": str}
    """
    if set_a is None and set_b is None:
        return {"score": 1.0, "status": "both_empty"}
    if set_a is None or set_b is None:
        return {"score": 0.0, "status": "one_empty"}
    if len(set_a) == 0 and len(set_b) == 0:
        return {"score": 1.0, "status": "both_empty"}
    if len(set_a) == 0 or len(set_b) == 0:
        return {"score": 0.0, "status": "one_empty"}

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)

    if union == 0:
        return {"score": 0.0, "status": "union_zero"}

    score = intersection / union
    return {"score": round(score, 6), "status": "jaccard_ok"}


def compare_code_v3_weighted(
    multiset_a: Optional[dict],
    multiset_b: Optional[dict],
) -> dict:
    """
    Weighted Jaccard similarity on multisets (dict[opcode_tuple -> count]).

    Weighted Jaccard = sum(min(a_i, b_i)) / sum(max(a_i, b_i))

    Returns:
        {"score": float [0..1], "status": str}
    """
    if multiset_a is None and multiset_b is None:
        return {"score": 1.0, "status": "both_empty"}
    if multiset_a is None or multiset_b is None:
        return {"score": 0.0, "status": "one_empty"}
    if not multiset_a and not multiset_b:
        return {"score": 1.0, "status": "both_empty"}
    if not multiset_a or not multiset_b:
        return {"score": 0.0, "status": "one_empty"}

    all_keys = set(multiset_a.keys()) | set(multiset_b.keys())
    numerator = 0
    denominator = 0
    for key in all_keys:
        a_count = multiset_a.get(key, 0)
        b_count = multiset_b.get(key, 0)
        numerator += min(a_count, b_count)
        denominator += max(a_count, b_count)

    if denominator == 0:
        return {"score": 0.0, "status": "union_zero"}

    score = numerator / denominator
    return {"score": round(score, 6), "status": "weighted_jaccard_ok"}


# ── CLI helper ───────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="code_view_v3",
        description=(
            "R_code v3: compare two APKs using method-level opcode multiset Jaccard "
            "(inspired by MOSDroid, Computers & Security 2025)."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    parser.add_argument(
        "--weighted", action="store_true",
        help="Use weighted Jaccard on multisets instead of set Jaccard"
    )
    args = parser.parse_args()

    if args.weighted:
        multiset_a = extract_method_opcode_multiset(Path(args.apk_a))
        multiset_b = extract_method_opcode_multiset(Path(args.apk_b))
        result = compare_code_v3_weighted(multiset_a, multiset_b)
        result["method_count_a"] = sum(multiset_a.values()) if multiset_a else 0
        result["method_count_b"] = sum(multiset_b.values()) if multiset_b else 0
    else:
        fp_a = extract_method_opcode_fingerprint(Path(args.apk_a))
        fp_b = extract_method_opcode_fingerprint(Path(args.apk_b))
        result = compare_code_v3(fp_a, fp_b)
        result["unique_methods_a"] = len(fp_a) if fp_a else 0
        result["unique_methods_b"] = len(fp_b) if fp_b else 0

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

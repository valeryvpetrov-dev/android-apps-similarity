#!/usr/bin/env python3
"""EXEC-082.1: code_view_v4_shingled — shingled opcode fuzzy fingerprint.

@canonical REPR-26-CODE-VIEW-SUNSET-PLAN: preferred canonical code
representation. Production comparison uses this shingled method-level fuzzy
representation first, with code_view_v4 as the canonical fallback.

Motivation (REV-1 / research/R-04):
  ``code_view_v4`` hashes the full opcode sequence of every method as one
  byte string. A single-byte mutation inside the method therefore flips
  every n-gram that overlaps it and can change the whole TLSH / simhash
  digest. This breaks the "small edit → small distance" property that
  fuzzy fingerprints are supposed to provide.

Representation (DroidMOSS-style shingling):
  1. Re-use the DEX parser from ``code_view_v4`` to collect the opcode
     tuple of every internal method.
  2. For each method build the set of **shingles** — fixed-length sliding
     windows over the opcode sequence. The set cardinality is bounded by
     the method size; a local edit only disturbs ``shingle_size`` shingles
     and the rest stays identical.
  3. Hash the shingle set into a fuzzy fingerprint:
       * TLSH over the sorted concatenation of shingles when py-tlsh is
         installed and the byte stream is long enough;
       * simhash over the shingle set otherwise (each shingle contributes
         once, so the digest depends on the set, not the order);
       * BLAKE2b fallback for methods too short to produce any shingle.

Comparison:
  Fuzzy method-id aligned comparison — identical contract to
  ``compare_code_v4``. Common method ids are scored by TLSH diff for TLSH
  fingerprints and by normalized Hamming distance for simhash fingerprints.

No androguard dependency. Stdlib only (``hashlib``, ``struct``), with
optional ``tlsh``.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import sys
from pathlib import Path
from typing import Optional

try:
    from script.code_view_v4 import (  # type: ignore
        FP_PREFIX_SHORT,
        FP_PREFIX_SIMHASH,
        FP_PREFIX_TLSH,
        SIMHASH_BITS,
        TLSH_DIFF_MAX,
        TLSH_MIN_BYTES,
        _TLSH_AVAILABLE,
        _collect_methods_from_apk,
    )
except ImportError:
    from code_view_v4 import (  # type: ignore[no-redef]
        FP_PREFIX_SHORT,
        FP_PREFIX_SIMHASH,
        FP_PREFIX_TLSH,
        SIMHASH_BITS,
        TLSH_DIFF_MAX,
        TLSH_MIN_BYTES,
        _TLSH_AVAILABLE,
        _collect_methods_from_apk,
    )

if _TLSH_AVAILABLE:
    import tlsh as _tlsh_module  # type: ignore
else:
    _tlsh_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE: str = "v4_shingled"

# Default shingle width. 4 opcodes ≈ one basic statement in Dalvik
# (e.g. const/invoke/move-result/goto). Must match the figure reported in
# EXEC-082.1 artefacts.
DEFAULT_SHINGLE_SIZE: int = 4


# ---------------------------------------------------------------------------
# Shingling
# ---------------------------------------------------------------------------

def shingle_opcodes(
    opcodes: list[int],
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
) -> set[bytes]:
    """Return the set of unique fixed-length shingles of ``opcodes``.

    Each shingle is a window of ``shingle_size`` consecutive opcodes packed
    into ``bytes`` via ``struct.pack`` for deterministic hashing.

    If ``len(opcodes) < shingle_size`` the function returns the empty set —
    it does not raise. ``shingle_size`` must be a positive int.
    """
    if shingle_size <= 0:
        raise ValueError("shingle_size must be a positive integer")
    if len(opcodes) < shingle_size:
        return set()
    pack_fmt = "B" * shingle_size
    shingles: set[bytes] = set()
    for i in range(len(opcodes) - shingle_size + 1):
        window = opcodes[i:i + shingle_size]
        shingles.add(struct.pack(pack_fmt, *window))
    return shingles


# ---------------------------------------------------------------------------
# Fingerprint backends
# ---------------------------------------------------------------------------

def _blake2b_fingerprint_over_opcodes(opcodes: list[int]) -> str:
    """Stable digest for methods too short to produce any shingle."""
    digest = hashlib.blake2b(bytes(opcodes), digest_size=8).hexdigest()
    return FP_PREFIX_SHORT + digest


def _serialize_shingle_set(shingles: set[bytes]) -> bytes:
    """Deterministic byte serialization of a shingle set.

    Sorting makes TLSH input independent of Python's set hash randomization,
    so the fingerprint is reproducible across processes.
    """
    return b"".join(sorted(shingles))


def _tlsh_fingerprint_over_shingles(shingles: set[bytes]) -> Optional[str]:
    """TLSH hash of the sorted shingle concatenation, or None when too short."""
    if not _TLSH_AVAILABLE or _tlsh_module is None:
        return None
    data = _serialize_shingle_set(shingles)
    if len(data) < TLSH_MIN_BYTES:
        return None
    try:
        h = _tlsh_module.hash(data)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("TLSH hashing failed in shingled view: %s", exc)
        return None
    if not h or h == "TNULL":
        return None
    return FP_PREFIX_TLSH + h


def _simhash_fingerprint_over_shingles(
    shingles: set[bytes],
    bits: int = SIMHASH_BITS,
) -> str:
    """Compute a 64-bit simhash over a **set** of shingles.

    Each shingle contributes exactly once: the vector entries are +1 when
    the shingle's hash has the corresponding bit set and -1 otherwise. The
    final fingerprint keeps bit positions with a positive running sum. The
    digest therefore depends on the set membership, not on shingle order.
    """
    if bits <= 0 or bits > 64:
        raise ValueError("simhash bits must be in (0, 64]")
    acc = [0] * bits
    byte_len = (bits + 7) // 8
    mask = (1 << bits) - 1
    # Iterate over a sorted list so two identical shingle sets always
    # touch ``acc`` in the same order — belt-and-braces determinism.
    for shingle in sorted(shingles):
        h = hashlib.blake2b(shingle, digest_size=byte_len).digest()
        val = int.from_bytes(h, "big") & mask
        for b in range(bits):
            if val & (1 << b):
                acc[b] += 1
            else:
                acc[b] -= 1
    fp = 0
    for b in range(bits):
        if acc[b] >= 0:
            fp |= (1 << b)
    hex_width = (bits + 3) // 4
    return FP_PREFIX_SIMHASH + f"{fp:0{hex_width}x}"


def shingled_method_fingerprint(
    opcodes: list[int],
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
) -> str:
    """Fuzzy fingerprint of a method computed from its shingle set.

    Backend selection mirrors ``code_view_v4._method_fingerprint``:
      * methods with no shingles (body shorter than ``shingle_size``)
        → BLAKE2b digest over the raw opcode bytes, prefix ``B:``;
      * otherwise TLSH over the serialized shingle set when available and
        the serialized stream is long enough, prefix ``T:``;
      * otherwise 64-bit simhash over the shingle set, prefix ``S:``.
    """
    shingles = shingle_opcodes(opcodes, shingle_size=shingle_size)
    if not shingles:
        return _blake2b_fingerprint_over_opcodes(opcodes)
    tlsh_fp = _tlsh_fingerprint_over_shingles(shingles)
    if tlsh_fp is not None:
        return tlsh_fp
    return _simhash_fingerprint_over_shingles(shingles)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_code_view_v4_shingled(
    apk_path: str | Path,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
) -> Optional[dict]:
    """Extract method-level shingled fuzzy fingerprints from an APK.

    Args:
        apk_path: Path to the .apk file.
        shingle_size: Sliding-window width in opcodes (default 4).

    Returns:
        Dict with keys::

            {
                "method_fingerprints": dict[str, str],
                "total_methods": int,
                "mode": "v4_shingled",
            }

        or ``None`` when ``apk_path`` does not exist or is not a file.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    methods = _collect_methods_from_apk(apk_path)
    method_fingerprints: dict[str, str] = {}
    for method_id, opcodes in methods:
        if method_id in method_fingerprints:
            # Keep first occurrence, matching code_view_v4 semantics.
            continue
        method_fingerprints[method_id] = shingled_method_fingerprint(
            list(opcodes), shingle_size=shingle_size
        )

    return {
        "method_fingerprints": method_fingerprints,
        "total_methods": len(method_fingerprints),
        "mode": MODE,
    }


def compare_code_v4_shingled(
    features_a: Optional[dict],
    features_b: Optional[dict],
    *,
    tlsh_diff_max: int = TLSH_DIFF_MAX,
) -> dict:
    """Fuzzy fingerprint distance per method id — same contract as v4.

    Returns::

        {
            "score": float in [0, 1],
            "matched_methods": int,       # common method ids
            "union_methods": int,         # |ids_a ∪ ids_b|
            "denominator_methods": int,   # max(|ids_a|, |ids_b|)
            "status": str,
        }
    """
    if features_a is None and features_b is None:
        return {
            "score": 0.0,
            "matched_methods": 0,
            "union_methods": 0,
            "denominator_methods": 0,
            "status": "both_empty",
            "both_empty": True,
        }
    if features_a is None or features_b is None:
        return {
            "score": 0.0,
            "matched_methods": 0,
            "union_methods": 0,
            "denominator_methods": 0,
            "status": "one_empty",
        }

    fp_a = dict(features_a.get("method_fingerprints") or {})
    fp_b = dict(features_b.get("method_fingerprints") or {})
    ids_a = set(fp_a)
    ids_b = set(fp_b)

    if not ids_a and not ids_b:
        return {
            "score": 0.0,
            "matched_methods": 0,
            "union_methods": 0,
            "denominator_methods": 0,
            "status": "both_empty",
            "both_empty": True,
        }
    if not ids_a or not ids_b:
        return {
            "score": 0.0,
            "matched_methods": 0,
            "union_methods": len(ids_a | ids_b),
            "denominator_methods": max(len(ids_a), len(ids_b)),
            "status": "one_empty",
        }

    common_ids = ids_a & ids_b
    denominator = max(len(ids_a), len(ids_b))
    similarity_sum = sum(
        _fingerprint_similarity(
            fp_a[method_id],
            fp_b[method_id],
            tlsh_diff_max=tlsh_diff_max,
        )
        for method_id in common_ids
    )
    score = similarity_sum / denominator if denominator else 0.0
    return {
        "score": round(score, 6),
        "matched_methods": len(common_ids),
        "union_methods": len(ids_a | ids_b),
        "denominator_methods": denominator,
        "status": "fuzzy_ok",
    }


def _fingerprint_similarity(
    fp_a: str,
    fp_b: str,
    *,
    tlsh_diff_max: int = TLSH_DIFF_MAX,
) -> float:
    """Return normalized similarity for two fingerprints from the same method id."""
    if fp_a == fp_b:
        return 1.0
    if fp_a.startswith(FP_PREFIX_TLSH) and fp_b.startswith(FP_PREFIX_TLSH):
        if not _TLSH_AVAILABLE or _tlsh_module is None:
            return 0.0
        if tlsh_diff_max <= 0:
            raise ValueError("tlsh_diff_max must be a positive integer")
        try:
            diff = _tlsh_module.diff(
                fp_a[len(FP_PREFIX_TLSH):],
                fp_b[len(FP_PREFIX_TLSH):],
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("TLSH comparison failed in shingled view: %s", exc)
            return 0.0
        return 1.0 - min(diff, tlsh_diff_max) / tlsh_diff_max
    if fp_a.startswith(FP_PREFIX_SIMHASH) and fp_b.startswith(FP_PREFIX_SIMHASH):
        return _simhash_similarity(
            fp_a[len(FP_PREFIX_SIMHASH):],
            fp_b[len(FP_PREFIX_SIMHASH):],
        )
    if fp_a.startswith(FP_PREFIX_SHORT) and fp_b.startswith(FP_PREFIX_SHORT):
        return 0.0
    return 0.0


def _simhash_similarity(hex_a: str, hex_b: str) -> float:
    """Return normalized 64-bit Hamming similarity for simhash hex strings."""
    try:
        xor_value = int(hex_a, 16) ^ int(hex_b, 16)
    except ValueError:
        return 0.0
    try:
        distance = xor_value.bit_count()
    except AttributeError:
        distance = bin(xor_value).count("1")
    return 1.0 - min(distance, SIMHASH_BITS) / SIMHASH_BITS


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="code_view_v4_shingled",
        description=(
            "EXEC-082.1: compare two APKs using shingled method-level fuzzy "
            "fingerprints of opcode sequences (TLSH with simhash fallback)."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    parser.add_argument(
        "--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE,
        help=f"Shingle width in opcodes (default {DEFAULT_SHINGLE_SIZE})",
    )
    args = parser.parse_args()

    features_a = extract_code_view_v4_shingled(
        Path(args.apk_a), shingle_size=args.shingle_size,
    )
    features_b = extract_code_view_v4_shingled(
        Path(args.apk_b), shingle_size=args.shingle_size,
    )
    result = compare_code_v4_shingled(features_a, features_b)
    result["total_methods_a"] = features_a["total_methods"] if features_a else 0
    result["total_methods_b"] = features_b["total_methods"] if features_b else 0
    result["tlsh_available"] = _TLSH_AVAILABLE
    result["shingle_size"] = args.shingle_size
    print(json.dumps(result, indent=2))


def compute_code_v4_shingled(
    apk_a: str | Path,
    apk_b: str | Path,
    *,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    tlsh_diff_max: int = TLSH_DIFF_MAX,
    features_a: Optional[dict] = None,
    features_b: Optional[dict] = None,
) -> dict:
    """Extract and compare two APKs with explicit calibration parameters."""
    if features_a is None:
        features_a = extract_code_view_v4_shingled(
            Path(apk_a), shingle_size=shingle_size,
        )
    if features_b is None:
        features_b = extract_code_view_v4_shingled(
            Path(apk_b), shingle_size=shingle_size,
        )
    result = compare_code_v4_shingled(
        features_a,
        features_b,
        tlsh_diff_max=tlsh_diff_max,
    )
    result["total_methods_a"] = features_a["total_methods"] if features_a else 0
    result["total_methods_b"] = features_b["total_methods"] if features_b else 0
    result["tlsh_available"] = _TLSH_AVAILABLE
    result["shingle_size"] = shingle_size
    result["tlsh_diff_max"] = tlsh_diff_max
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

#!/usr/bin/env python3
"""EXEC-081: Juxtapp-style structural baseline for APK similarity.

Motivation:
  Juxtapp (Hanna et al., DIMVA'12) represents each application as a fixed-size
  bit vector built by hashing opcode n-grams into vector positions. Similarity
  between two applications is the Jaccard coefficient of their bit vectors
  (ratio of bits set in both to the union of bits set in at least one). This
  module is the second structural baseline for benchmarking our system, next
  to the DroidMOSS-style DEX fuzzy hash baseline (EXEC-079).

Representation:
  1. Parse every ``classes*.dex`` inside an APK using the pure-Python DEX
     parser from ``code_view_v4`` (no androguard) and concatenate the opcode
     sequences of every internal method into a single application-level
     opcode stream.
  2. Slide a fixed-width window of ``ngram_size`` opcodes over that stream to
     build the multiset of n-grams.
  3. For each n-gram, derive a position in a fixed-length bit vector via a
     BLAKE2b digest modulo ``vector_bits`` and set that bit. Collisions are
     tolerated by design — the same n-gram always maps to the same position,
     so identical applications yield identical vectors while unrelated ones
     only overlap through spurious bit collisions.

Comparison:
  Jaccard on the bit vectors::

      score = popcount(A & B) / popcount(A | B)

  Identical APKs score 1.0; fully disjoint opcode sets score 0.0.

Dependencies:
  Python stdlib only (``hashlib``, ``zipfile``, ``struct``). The DEX parsing
  is delegated to ``code_view_v4._collect_methods_from_apk`` to avoid
  duplicating ~200 lines of bytecode-layout code; no androguard is used.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from script.code_view_v4 import _collect_methods_from_apk
except ImportError:  # pragma: no cover — fallback when run from script/
    from code_view_v4 import _collect_methods_from_apk  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE: str = "juxtapp-v1"

#: Width of the sliding window over the opcode stream.
DEFAULT_NGRAM_SIZE: int = 4

#: Length of the application-level bit vector. 8192 bits gives a good trade-off
#: between collision rate (~1/8192 per n-gram) and memory footprint.
DEFAULT_VECTOR_BITS: int = 8192


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ngram_to_position(ngram_bytes: bytes, vector_bits: int) -> int:
    """Hash an n-gram into a position in ``[0, vector_bits)``.

    Uses BLAKE2b with an 8-byte digest — enough entropy to cover vectors far
    larger than the default 8192 bits without bias from modulo reduction.
    """
    h = hashlib.blake2b(ngram_bytes, digest_size=8).digest()
    return int.from_bytes(h, "big") % vector_bits


def _app_opcode_stream(apk_path: Path) -> tuple[int, ...]:
    """Concatenate every method's opcode tuple into a single application-level
    stream. Method order follows ``_collect_methods_from_apk``'s iteration
    over ``classes*.dex`` (sorted by name), which is deterministic for a
    given APK."""
    methods = _collect_methods_from_apk(apk_path)
    if not methods:
        return ()
    buf: list[int] = []
    for _method_id, opcodes in methods:
        buf.extend(opcodes)
    return tuple(buf)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_juxtapp_vector(
    apk_path: str,
    ngram_size: int = DEFAULT_NGRAM_SIZE,
    vector_bits: int = DEFAULT_VECTOR_BITS,
) -> dict:
    """Build a Juxtapp-style bit vector for ``apk_path``.

    Args:
        apk_path: Path to the .apk file (string or :class:`pathlib.Path`).
        ngram_size: Sliding-window width over the opcode stream. Defaults to
            :data:`DEFAULT_NGRAM_SIZE` (4), the setting used in the original
            paper.
        vector_bits: Length of the bit vector. Defaults to
            :data:`DEFAULT_VECTOR_BITS` (8192).

    Returns:
        Dict with keys::

            {
                "vector": list[int],     # 0/1 bit array of length vector_bits
                "total_ngrams": int,     # count of n-grams fed into the vector
                "mode": "juxtapp-v1",
            }

        ``total_ngrams`` counts every window, including repeats; the vector
        only records set/unset bits so ``sum(vector) <= total_ngrams``.
    """
    if ngram_size <= 0:
        raise ValueError(f"ngram_size must be positive, got {ngram_size}")
    if vector_bits <= 0:
        raise ValueError(f"vector_bits must be positive, got {vector_bits}")

    path = Path(apk_path)
    vector = [0] * vector_bits

    if not path.exists() or not path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", path)
        return {
            "vector": vector,
            "total_ngrams": 0,
            "mode": MODE,
        }

    stream = _app_opcode_stream(path)
    total_ngrams = 0
    if len(stream) >= ngram_size:
        stream_bytes = bytes(stream)
        for i in range(len(stream) - ngram_size + 1):
            gram = stream_bytes[i:i + ngram_size]
            pos = _ngram_to_position(gram, vector_bits)
            vector[pos] = 1
            total_ngrams += 1

    return {
        "vector": vector,
        "total_ngrams": total_ngrams,
        "mode": MODE,
    }


def compare_juxtapp(
    features_a: Optional[dict],
    features_b: Optional[dict],
) -> dict:
    """Compare two Juxtapp feature dicts via Jaccard on their bit vectors.

    Returns:
        Dict with keys::

            {
                "score": float in [0, 1],
                "matched_bits": int,   # popcount(A & B)
                "union_bits": int,     # popcount(A | B)
                "status": str,
            }

        ``status`` values:
            ``ok``                       — both sides carry bits, score is the
                                           Jaccard ratio.
            ``empty``                    — both vectors are all-zero (no
                                           n-grams were extracted from either
                                           APK); score is 0.0.
            ``mismatched_vector_bits``   — the two vectors have different
                                           lengths and cannot be compared;
                                           score is 0.0.
    """
    if features_a is None or features_b is None:
        return {
            "score": 0.0,
            "matched_bits": 0,
            "union_bits": 0,
            "status": "empty",
        }

    vec_a = features_a.get("vector") or []
    vec_b = features_b.get("vector") or []

    if len(vec_a) != len(vec_b):
        return {
            "score": 0.0,
            "matched_bits": 0,
            "union_bits": 0,
            "status": "mismatched_vector_bits",
        }

    matched = 0
    union = 0
    for a, b in zip(vec_a, vec_b):
        if a and b:
            matched += 1
            union += 1
        elif a or b:
            union += 1

    if union == 0:
        return {
            "score": 0.0,
            "matched_bits": 0,
            "union_bits": 0,
            "status": "empty",
        }

    score = matched / union
    return {
        "score": round(score, 6),
        "matched_bits": matched,
        "union_bits": union,
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="baseline_juxtapp",
        description=(
            "EXEC-081: Juxtapp-style structural baseline — Jaccard on "
            "opcode-n-gram bit vectors extracted from two APKs."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    parser.add_argument(
        "--ngram-size", type=int, default=DEFAULT_NGRAM_SIZE,
        help=f"Opcode n-gram width (default: {DEFAULT_NGRAM_SIZE})",
    )
    parser.add_argument(
        "--vector-bits", type=int, default=DEFAULT_VECTOR_BITS,
        help=f"Bit-vector length (default: {DEFAULT_VECTOR_BITS})",
    )
    args = parser.parse_args()

    features_a = extract_juxtapp_vector(
        args.apk_a, ngram_size=args.ngram_size, vector_bits=args.vector_bits
    )
    features_b = extract_juxtapp_vector(
        args.apk_b, ngram_size=args.ngram_size, vector_bits=args.vector_bits
    )
    result = compare_juxtapp(features_a, features_b)
    result["total_ngrams_a"] = features_a["total_ngrams"]
    result["total_ngrams_b"] = features_b["total_ngrams"]
    result["ngram_size"] = args.ngram_size
    result["vector_bits"] = args.vector_bits
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

#!/usr/bin/env python3
"""EXEC-082a: code_view_v4 — method-level fuzzy fingerprint of opcode sequences.

Motivation (REV-1 / research/R-04):
  v2 hashes the whole APK opcode stream with TLSH; v3 compares per-method
  opcode multisets with set Jaccard. Neither is a per-method *fuzzy* hash,
  so they cannot tolerate small per-method mutations while keeping a method
  addressable. v4 fills that gap and is the base for EXEC-082.1 shingling.

Representation:
  1. Parse each DEX inside the APK with a minimal pure-Python parser
     (no androguard dependency) and extract the opcode sequence of every
     internal method.
  2. For every method compute a stable id
     `<class_descriptor>-><method_name><proto_descriptor>` and a fuzzy
     fingerprint of its opcode sequence. Fingerprint is TLSH when py-tlsh
     is installed, otherwise a 64-bit simhash over opcode 3-grams hashed
     via BLAKE2b (pure-Python fallback).

Comparison:
  Fuzzy method-id aligned comparison. Common method ids are scored by TLSH
  diff for TLSH fingerprints and by normalized Hamming distance for simhash
  fingerprints. Missing method ids contribute zero similarity.

No androguard dependency is used — the module relies only on the Python
stdlib (`zipfile`, `struct`, `hashlib`) plus the optional `tlsh` package.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import sys
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency
# ---------------------------------------------------------------------------

try:
    import tlsh as _tlsh_module  # type: ignore
    _TLSH_AVAILABLE = True
except ImportError:
    _TLSH_AVAILABLE = False
    _tlsh_module = None  # type: ignore[assignment]
    logger.info(
        "py-tlsh is not installed; code_view_v4 falls back to "
        "pure-Python simhash over opcode 3-grams."
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE: str = "v4"

# N-gram window for simhash fallback.
SIMHASH_NGRAM: int = 3

# Bit width of the simhash fingerprint.
SIMHASH_BITS: int = 64

# TLSH requires at least ~50 bytes of input; shorter method bodies fall back
# to a deterministic BLAKE2b digest so every method still gets a fingerprint.
TLSH_MIN_BYTES: int = 50

# Fingerprint prefixes — used to distinguish the backends when reading a
# mixed-mode output and to short-circuit comparison.
FP_PREFIX_TLSH: str = "T:"
FP_PREFIX_SIMHASH: str = "S:"
FP_PREFIX_SHORT: str = "B:"   # BLAKE2b digest for too-short bodies

# TLSH diff values above ~300 are treated as unrelated for normalization.
TLSH_DIFF_MAX: int = 300

# ---------------------------------------------------------------------------
# Minimal DEX parser (no androguard)
# ---------------------------------------------------------------------------

# Size (in 16-bit code units) of every Dalvik instruction, indexed by opcode.
# Source: https://source.android.com/docs/core/runtime/dalvik-bytecode
def _build_insn_size_table() -> list[int]:
    sizes = [1] * 256
    explicit = {
        0x00: 1, 0x01: 1, 0x02: 2, 0x03: 3,
        0x04: 1, 0x05: 2, 0x06: 3,
        0x07: 1, 0x08: 2, 0x09: 3,
        0x0a: 1, 0x0b: 1, 0x0c: 1, 0x0d: 1, 0x0e: 1,
        0x0f: 1, 0x10: 1, 0x11: 1,
        0x12: 1, 0x13: 2, 0x14: 3, 0x15: 2,
        0x16: 2, 0x17: 3, 0x18: 5, 0x19: 2,
        0x1a: 2, 0x1b: 3,
        0x1c: 2, 0x1d: 1, 0x1e: 1, 0x1f: 2,
        0x20: 2, 0x21: 1, 0x22: 2, 0x23: 2,
        0x24: 3, 0x25: 3,
        0x26: 3,
        0x27: 1,
        0x28: 1, 0x29: 2, 0x2a: 3,
        0x2b: 3, 0x2c: 3,
        0x2d: 2, 0x2e: 2, 0x2f: 2, 0x30: 2, 0x31: 2,
        0x32: 2, 0x33: 2, 0x34: 2, 0x35: 2, 0x36: 2, 0x37: 2,
        0x38: 2, 0x39: 2, 0x3a: 2, 0x3b: 2, 0x3c: 2, 0x3d: 2,
    }
    for op, sz in explicit.items():
        sizes[op] = sz
    # aget / aput family
    for op in range(0x44, 0x52):
        sizes[op] = 2
    # iget / iput family
    for op in range(0x52, 0x60):
        sizes[op] = 2
    # sget / sput family
    for op in range(0x60, 0x6e):
        sizes[op] = 2
    # invoke-*
    for op in range(0x6e, 0x73):
        sizes[op] = 3
    # invoke-*/range
    for op in range(0x74, 0x79):
        sizes[op] = 3
    # unary / binop / binop-2addr / binop-lit
    for op in range(0x7b, 0x90):
        sizes[op] = 1
    for op in range(0x90, 0xb0):
        sizes[op] = 2
    for op in range(0xb0, 0xd0):
        sizes[op] = 1
    for op in range(0xd0, 0xd8):
        sizes[op] = 2
    for op in range(0xd8, 0xe3):
        sizes[op] = 2
    sizes[0xfa] = 4  # invoke-polymorphic
    sizes[0xfb] = 4  # invoke-polymorphic/range
    sizes[0xfc] = 3  # invoke-custom
    sizes[0xfd] = 3  # invoke-custom/range
    sizes[0xfe] = 2  # const-method-handle
    sizes[0xff] = 2  # const-method-type
    return sizes


_INSN_SIZES: list[int] = _build_insn_size_table()


def _read_uleb128(data: bytes, off: int) -> tuple[int, int]:
    """Decode a ULEB128 integer from ``data`` starting at ``off``.

    Returns ``(value, new_offset)``.
    """
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7f) << shift
        if (b & 0x80) == 0:
            return result, off
        shift += 7


def _read_mutf8_string(data: bytes, off: int) -> str:
    """Read a DEX string_data_item (ULEB128 length + null-terminated bytes)."""
    _length, off = _read_uleb128(data, off)
    end = data.index(b"\x00", off)
    raw = data[off:end]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _instruction_size(code_units: list[int], pos: int) -> int:
    """Return the size (in 16-bit code units) of the instruction at ``pos``."""
    unit = code_units[pos]
    opcode = unit & 0xff
    # Pseudo-payloads encoded as special forms of opcode 0x00.
    if opcode == 0x00 and unit != 0:
        ident = unit >> 8
        if ident == 0x01:  # packed-switch-payload
            size = code_units[pos + 1]
            return 4 + size * 2
        if ident == 0x02:  # sparse-switch-payload
            size = code_units[pos + 1]
            return 2 + size * 4
        if ident == 0x03:  # fill-array-data-payload
            element_width = code_units[pos + 1]
            size = code_units[pos + 2] | (code_units[pos + 3] << 16)
            units_for_data = (size * element_width + 1) // 2
            return 4 + units_for_data
    size = _INSN_SIZES[opcode]
    # Defensive: never advance by zero — that would loop forever.
    return size if size > 0 else 1


def _iter_method_opcodes(dex: bytes) -> list[tuple[str, tuple[int, ...]]]:
    """Return ``[(method_id, opcodes_tuple), ...]`` for every method that has code.

    ``method_id`` is a stable UTF-8 string:
    ``<class_descriptor>-><method_name><proto_descriptor>``.
    ``opcodes_tuple`` is the sequence of Dalvik opcode bytes (ints, 0..255).
    """
    if len(dex) < 0x70 or dex[:4] != b"dex\n":
        return []

    (string_ids_size, string_ids_off,
     type_ids_size, type_ids_off,
     proto_ids_size, proto_ids_off,
     _field_ids_size, _field_ids_off,
     method_ids_size, method_ids_off,
     class_defs_size, class_defs_off,
     _data_size, _data_off) = struct.unpack_from("<14I", dex, 56)

    # Strings
    strings: list[str] = [""] * string_ids_size
    for i in range(string_ids_size):
        (sid_off,) = struct.unpack_from("<I", dex, string_ids_off + i * 4)
        strings[i] = _read_mutf8_string(dex, sid_off)

    # Types (descriptors)
    types: list[str] = [""] * type_ids_size
    for i in range(type_ids_size):
        (str_idx,) = struct.unpack_from("<I", dex, type_ids_off + i * 4)
        types[i] = strings[str_idx]

    # Prototypes: reconstruct `(<params>)<return>` descriptor
    proto_descs: list[str] = [""] * proto_ids_size
    for i in range(proto_ids_size):
        (_shorty_idx, return_type_idx, parameters_off) = struct.unpack_from(
            "<III", dex, proto_ids_off + i * 12
        )
        params: list[str] = []
        if parameters_off != 0:
            (list_size,) = struct.unpack_from("<I", dex, parameters_off)
            for j in range(list_size):
                (tidx,) = struct.unpack_from(
                    "<H", dex, parameters_off + 4 + j * 2
                )
                params.append(types[tidx])
        proto_descs[i] = "(" + "".join(params) + ")" + types[return_type_idx]

    # Methods: (class_idx, proto_idx, name_idx)
    method_refs: list[tuple[str, str, str]] = [("", "", "")] * method_ids_size
    for i in range(method_ids_size):
        (class_idx, proto_idx, name_idx) = struct.unpack_from(
            "<HHI", dex, method_ids_off + i * 8
        )
        method_refs[i] = (
            types[class_idx],
            strings[name_idx],
            proto_descs[proto_idx],
        )

    # Iterate class_defs -> class_data -> encoded_method
    results: list[tuple[str, tuple[int, ...]]] = []
    for i in range(class_defs_size):
        cdef_off = class_defs_off + i * 32
        (_class_idx, _access, _superclass_idx, _interfaces_off,
         _source_file_idx, _annotations_off, class_data_off,
         _static_values_off) = struct.unpack_from("<8I", dex, cdef_off)
        if class_data_off == 0:
            continue
        cpos = class_data_off
        static_fields_size, cpos = _read_uleb128(dex, cpos)
        instance_fields_size, cpos = _read_uleb128(dex, cpos)
        direct_methods_size, cpos = _read_uleb128(dex, cpos)
        virtual_methods_size, cpos = _read_uleb128(dex, cpos)
        # Skip field entries (field_idx_diff + access_flags per field)
        for _ in range(static_fields_size + instance_fields_size):
            _, cpos = _read_uleb128(dex, cpos)
            _, cpos = _read_uleb128(dex, cpos)
        for method_group_size in (direct_methods_size, virtual_methods_size):
            prev_midx = 0
            for _ in range(method_group_size):
                midx_diff, cpos = _read_uleb128(dex, cpos)
                _access_flags, cpos = _read_uleb128(dex, cpos)
                code_off, cpos = _read_uleb128(dex, cpos)
                prev_midx += midx_diff
                if code_off == 0:
                    continue  # abstract / native method, no body
                opcodes = _decode_opcodes(dex, code_off)
                if not opcodes:
                    continue
                class_desc, name, proto = method_refs[prev_midx]
                method_id = f"{class_desc}->{name}{proto}"
                results.append((method_id, opcodes))
    return results


def _decode_opcodes(dex: bytes, code_off: int) -> tuple[int, ...]:
    """Decode the ``insns[]`` array of a code_item into opcode bytes."""
    # code_item header: ushort regs, ins, outs, tries; uint debug; uint insns_size
    (_regs, _ins, _outs, _tries) = struct.unpack_from("<HHHH", dex, code_off)
    (_debug_off,) = struct.unpack_from("<I", dex, code_off + 8)
    (insns_size,) = struct.unpack_from("<I", dex, code_off + 12)
    insns_start = code_off + 16
    if insns_size == 0:
        return ()
    # Unpack all 16-bit code units in one call.
    code_units = list(
        struct.unpack_from(f"<{insns_size}H", dex, insns_start)
    )
    opcodes: list[int] = []
    pos = 0
    length = len(code_units)
    while pos < length:
        unit = code_units[pos]
        opcode = unit & 0xff
        opcodes.append(opcode)
        pos += _instruction_size(code_units, pos)
    return tuple(opcodes)


def _collect_methods_from_apk(apk_path: Path) -> list[tuple[str, tuple[int, ...]]]:
    """Enumerate ``classes*.dex`` inside ``apk_path`` and collect method opcodes."""
    all_methods: list[tuple[str, tuple[int, ...]]] = []
    try:
        with zipfile.ZipFile(apk_path) as zf:
            dex_names = sorted(
                n for n in zf.namelist()
                if n.startswith("classes") and n.endswith(".dex")
            )
            for name in dex_names:
                try:
                    dex_bytes = zf.read(name)
                except KeyError:
                    continue
                try:
                    all_methods.extend(_iter_method_opcodes(dex_bytes))
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "Failed to parse %s inside %s: %s", name, apk_path, exc
                    )
    except zipfile.BadZipFile:
        logger.warning("Not a valid APK/ZIP: %s", apk_path)
        return []
    return all_methods


# ---------------------------------------------------------------------------
# Fingerprint backends
# ---------------------------------------------------------------------------

def _opcode_bytes(opcodes: tuple[int, ...]) -> bytes:
    """Serialize opcode tuple to bytes for hashing."""
    return bytes(opcodes)


def _blake2b_fingerprint(opcodes: tuple[int, ...]) -> str:
    """Short deterministic digest for opcode bodies that cannot feed TLSH."""
    digest = hashlib.blake2b(_opcode_bytes(opcodes), digest_size=8).hexdigest()
    return FP_PREFIX_SHORT + digest


def _simhash_fingerprint(
    opcodes: tuple[int, ...],
    ngram: int = SIMHASH_NGRAM,
    bits: int = SIMHASH_BITS,
) -> str:
    """Compute a pure-Python simhash over opcode n-grams.

    Each n-gram is hashed with BLAKE2b truncated to ``bits`` bits. Bits of the
    resulting simhash vector are +1 when the corresponding hash bit is set and
    -1 otherwise; the final fingerprint keeps the bit positions with a positive
    running sum.
    """
    if bits <= 0 or bits > 64:
        raise ValueError("simhash bits must be in (0, 64]")
    # For very short bodies we still emit a stable fingerprint — just hash the
    # whole sequence. This keeps the method addressable and the score mass
    # stable between TLSH and simhash modes.
    if len(opcodes) < ngram:
        return _blake2b_fingerprint(opcodes)
    acc = [0] * bits
    byte_len = (bits + 7) // 8
    mask = (1 << bits) - 1
    for i in range(len(opcodes) - ngram + 1):
        gram = bytes(opcodes[i:i + ngram])
        h = hashlib.blake2b(gram, digest_size=byte_len).digest()
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


def _tlsh_fingerprint(opcodes: tuple[int, ...]) -> Optional[str]:
    """TLSH hash of the opcode byte stream, or None when too short."""
    if not _TLSH_AVAILABLE:
        return None
    data = _opcode_bytes(opcodes)
    if len(data) < TLSH_MIN_BYTES:
        return None
    try:
        h = _tlsh_module.hash(data)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("TLSH hashing failed: %s", exc)
        return None
    if not h or h == "TNULL":
        return None
    return FP_PREFIX_TLSH + h


def _method_fingerprint(opcodes: tuple[int, ...]) -> str:
    """Compute a method-level fingerprint, preferring TLSH when available."""
    fp = _tlsh_fingerprint(opcodes) if _TLSH_AVAILABLE else None
    if fp is not None:
        return fp
    return _simhash_fingerprint(opcodes)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_code_view_v4(apk_path: Path) -> Optional[dict]:
    """Extract method-level fuzzy fingerprints from an APK.

    Args:
        apk_path: Path to the .apk file.

    Returns:
        Dict with keys::

            {
                "method_fingerprints": dict[str, str],
                "total_methods": int,
                "mode": "v4",
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
        # If two encoded_method entries collide on method_id (e.g. via
        # multi-dex duplicates), keep the first non-empty body to stay
        # deterministic under Python's stable iteration order.
        if method_id in method_fingerprints:
            continue
        method_fingerprints[method_id] = _method_fingerprint(opcodes)

    return {
        "method_fingerprints": method_fingerprints,
        "total_methods": len(method_fingerprints),
        "mode": MODE,
    }


def compare_code_v4(
    features_a: Optional[dict],
    features_b: Optional[dict],
) -> dict:
    """Compare two v4 feature dicts by fuzzy fingerprint distance per method id.

    Returns a dict with::

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
            "score": 1.0,
            "matched_methods": 0,
            "union_methods": 0,
            "denominator_methods": 0,
            "status": "both_empty",
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
            "score": 1.0,
            "matched_methods": 0,
            "union_methods": 0,
            "denominator_methods": 0,
            "status": "both_empty",
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
        _fingerprint_similarity(fp_a[method_id], fp_b[method_id])
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


def _fingerprint_similarity(fp_a: str, fp_b: str) -> float:
    """Return normalized similarity for two fingerprints from the same method id."""
    if fp_a == fp_b:
        return 1.0
    if fp_a.startswith(FP_PREFIX_TLSH) and fp_b.startswith(FP_PREFIX_TLSH):
        if not _TLSH_AVAILABLE or _tlsh_module is None:
            return 0.0
        try:
            diff = _tlsh_module.diff(
                fp_a[len(FP_PREFIX_TLSH):],
                fp_b[len(FP_PREFIX_TLSH):],
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("TLSH comparison failed: %s", exc)
            return 0.0
        return 1.0 - min(diff, TLSH_DIFF_MAX) / TLSH_DIFF_MAX
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
        prog="code_view_v4",
        description=(
            "EXEC-082a: compare two APKs using method-level fuzzy fingerprints "
            "of opcode sequences (TLSH with simhash fallback)."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    args = parser.parse_args()

    features_a = extract_code_view_v4(Path(args.apk_a))
    features_b = extract_code_view_v4(Path(args.apk_b))
    result = compare_code_v4(features_a, features_b)
    result["total_methods_a"] = features_a["total_methods"] if features_a else 0
    result["total_methods_b"] = features_b["total_methods"] if features_b else 0
    result["tlsh_available"] = _TLSH_AVAILABLE
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

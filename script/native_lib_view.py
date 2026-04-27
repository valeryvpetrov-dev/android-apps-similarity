#!/usr/bin/env python3
"""Lightweight native library fingerprint for APK ``lib/<abi>/*.so`` files.

SYS-30-NATIVE-LIB-FINGERPRINT intentionally avoids CFG/basic-block analysis.
It extracts cheap ELF signals: SONAME/NEEDED, dynamic imports/exports and
printable strings from ``.rodata``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import struct
import zipfile
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

ABI_KEYS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
MODE = "native-lib-view-v1"

DT_NULL = 0
DT_NEEDED = 1
DT_SONAME = 14
SHT_DYNAMIC = 6
SHT_DYNSYM = 11
SHN_UNDEF = 0
STB_GLOBAL = 1
STB_WEAK = 2

COMPARE_WEIGHTS = {
    "jaccard_imports": 0.35,
    "jaccard_exports": 0.35,
    "jaccard_strings": 0.20,
    "jaccard_needed": 0.10,
}


@dataclass(frozen=True)
class _Section:
    name: str
    sh_type: int
    offset: int
    size: int
    link: int
    entsize: int


def _empty_features() -> dict[str, Any]:
    features: dict[str, Any] = {
        "mode": MODE,
        "native_libs_present": False,
    }
    for abi in ABI_KEYS:
        features[abi] = []
    return features


def extract_native_lib_features(apk_path: Path | str) -> dict[str, Any]:
    """Extract native library fingerprints grouped by Android ABI."""
    path = Path(apk_path)
    features = _empty_features()

    with zipfile.ZipFile(path, "r") as zf:
        for info in sorted(zf.infolist(), key=lambda item: item.filename):
            parsed = _parse_native_lib_zip_name(info.filename)
            if parsed is None:
                continue
            abi, lib_name = parsed
            payload = zf.read(info)
            item = {
                "path": info.filename,
                "name": lib_name,
                "size": info.file_size,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "fingerprint": fingerprint_elf(payload),
            }
            if abi in ABI_KEYS:
                features[abi].append(item)
            else:
                features.setdefault("other_abis", {}).setdefault(abi, []).append(item)
            features["native_libs_present"] = True

    return features


def compare_native_libs(
    features_a: dict[str, Any] | None,
    features_b: dict[str, Any] | None,
) -> dict[str, float | str | dict[str, float]]:
    """Compare two native-lib feature dicts with per-signal Jaccard scores."""
    sets_a = _collect_compare_sets(features_a)
    sets_b = _collect_compare_sets(features_b)
    result: dict[str, float | str | dict[str, float]] = {
        "jaccard_imports": _jaccard(sets_a["imports"], sets_b["imports"]),
        "jaccard_exports": _jaccard(sets_a["exports"], sets_b["exports"]),
        "jaccard_strings": _jaccard(sets_a["strings"], sets_b["strings"]),
        "jaccard_needed": _jaccard(sets_a["needed"], sets_b["needed"]),
    }
    result["score"] = sum(
        COMPARE_WEIGHTS[key] * float(result[key]) for key in COMPARE_WEIGHTS
    )
    result["status"] = "ok" if any(sets_a.values()) or any(sets_b.values()) else "empty"
    result["weights"] = dict(COMPARE_WEIGHTS)
    return result


def fingerprint_elf(payload: bytes) -> dict[str, Any]:
    """Return a stable ELF fingerprint dict.

    The function prefers pyelftools when present but falls back to the local
    section-header parser.  Fallback is the normal path in restricted
    environments used by the SYS wave.
    """
    base = _empty_fingerprint()
    if not payload.startswith(b"\x7fELF"):
        base["parse_status"] = "not_elf"
        return base

    parsed = _fingerprint_with_pyelftools(payload)
    if parsed is not None:
        parsed["parse_status"] = "pyelftools"
        return parsed

    try:
        parsed = _fingerprint_with_struct(payload)
        parsed["parse_status"] = "struct"
        return parsed
    except Exception as exc:  # pragma: no cover - defensive for malformed APKs
        base["parse_status"] = "error"
        base["parse_error"] = type(exc).__name__
        return base


def _empty_fingerprint() -> dict[str, Any]:
    return {
        "soname": None,
        "needed_libs": [],
        "imported_symbols_set": [],
        "exported_symbols_set": [],
        "rodata_strings_top20": [],
    }


def _parse_native_lib_zip_name(name: str) -> tuple[str, str] | None:
    parts = name.split("/")
    if len(parts) != 3:
        return None
    if parts[0] != "lib" or not parts[2].endswith(".so"):
        return None
    return parts[1], parts[2]


def _fingerprint_with_pyelftools(payload: bytes) -> dict[str, Any] | None:
    try:
        from elftools.elf.elffile import ELFFile  # type: ignore
    except ImportError:
        return None

    try:
        elf = ELFFile(BytesIO(payload))
        fingerprint = _empty_fingerprint()

        dynamic = elf.get_section_by_name(".dynamic")
        if dynamic is not None:
            needed: list[str] = []
            for tag in dynamic.iter_tags():
                if tag.entry.d_tag == "DT_SONAME":
                    fingerprint["soname"] = tag.soname
                elif tag.entry.d_tag == "DT_NEEDED":
                    needed.append(tag.needed)
            fingerprint["needed_libs"] = sorted(dict.fromkeys(needed))

        dynsym = elf.get_section_by_name(".dynsym")
        if dynsym is not None:
            imports: set[str] = set()
            exports: set[str] = set()
            for symbol in dynsym.iter_symbols():
                name = symbol.name
                if not name:
                    continue
                bind = symbol.entry.st_info.bind
                if bind not in ("STB_GLOBAL", "STB_WEAK"):
                    continue
                if symbol.entry.st_shndx == "SHN_UNDEF":
                    imports.add(name)
                else:
                    exports.add(name)
            fingerprint["imported_symbols_set"] = sorted(imports)
            fingerprint["exported_symbols_set"] = sorted(exports)

        rodata = elf.get_section_by_name(".rodata")
        if rodata is not None:
            fingerprint["rodata_strings_top20"] = _top_strings(rodata.data())
        return fingerprint
    except Exception:
        return None


def _fingerprint_with_struct(payload: bytes) -> dict[str, Any]:
    fingerprint = _empty_fingerprint()
    elf_class = payload[4]
    elf_data = payload[5]
    if elf_class not in (1, 2):
        raise ValueError("unsupported ELF class")
    if elf_data not in (1, 2):
        raise ValueError("unsupported ELF endian")
    endian = "<" if elf_data == 1 else ">"

    sections = _parse_sections(payload, elf_class, endian)
    dynstr = _section_data(payload, _first_section(sections, name=".dynstr"))
    dynamic = _first_section(sections, name=".dynamic", sh_type=SHT_DYNAMIC)
    if dynamic is not None and dynstr:
        soname, needed = _parse_dynamic(payload, dynamic, dynstr, elf_class, endian)
        fingerprint["soname"] = soname
        fingerprint["needed_libs"] = sorted(dict.fromkeys(needed))

    dynsym = _first_section(sections, name=".dynsym", sh_type=SHT_DYNSYM)
    if dynsym is not None:
        linked_dynstr = dynstr
        if 0 <= dynsym.link < len(sections):
            linked_dynstr = _section_data(payload, sections[dynsym.link])
        imports, exports = _parse_dynsym(
            payload, dynsym, linked_dynstr, elf_class, endian
        )
        fingerprint["imported_symbols_set"] = sorted(imports)
        fingerprint["exported_symbols_set"] = sorted(exports)

    rodata = _first_section(sections, name=".rodata")
    if rodata is not None:
        fingerprint["rodata_strings_top20"] = _top_strings(_section_data(payload, rodata))
    return fingerprint


def _parse_sections(payload: bytes, elf_class: int, endian: str) -> list[_Section]:
    if elf_class == 2:
        header_size = struct.calcsize(endian + "16sHHIQQQIHHHHHH")
        if len(payload) < header_size:
            raise ValueError("truncated ELF64 header")
        fields = struct.unpack_from(endian + "16sHHIQQQIHHHHHH", payload, 0)
        e_shoff = fields[6]
        e_shentsize = fields[11]
        e_shnum = fields[12]
        e_shstrndx = fields[13]
        sh_fmt = endian + "IIQQQQIIQQ"
    else:
        header_size = struct.calcsize(endian + "16sHHIIIIIHHHHHH")
        if len(payload) < header_size:
            raise ValueError("truncated ELF32 header")
        fields = struct.unpack_from(endian + "16sHHIIIIIHHHHHH", payload, 0)
        e_shoff = fields[6]
        e_shentsize = fields[11]
        e_shnum = fields[12]
        e_shstrndx = fields[13]
        sh_fmt = endian + "IIIIIIIIII"

    actual_shentsize = struct.calcsize(sh_fmt)
    if e_shoff <= 0 or e_shnum <= 0:
        return []
    if e_shentsize < actual_shentsize:
        raise ValueError("invalid section header size")

    raw_sections: list[tuple[int, int, int, int, int, int]] = []
    for i in range(e_shnum):
        offset = e_shoff + i * e_shentsize
        if offset + actual_shentsize > len(payload):
            raise ValueError("truncated section header table")
        fields = struct.unpack_from(sh_fmt, payload, offset)
        name_off = int(fields[0])
        sh_type = int(fields[1])
        sh_offset = int(fields[4])
        sh_size = int(fields[5])
        sh_link = int(fields[6])
        sh_entsize = int(fields[9])
        raw_sections.append((name_off, sh_type, sh_offset, sh_size, sh_link, sh_entsize))

    shstr = b""
    if 0 <= e_shstrndx < len(raw_sections):
        _, _, sh_offset, sh_size, _, _ = raw_sections[e_shstrndx]
        shstr = _slice(payload, sh_offset, sh_size)

    sections: list[_Section] = []
    for name_off, sh_type, sh_offset, sh_size, sh_link, sh_entsize in raw_sections:
        sections.append(
            _Section(
                name=_read_c_string(shstr, name_off),
                sh_type=sh_type,
                offset=sh_offset,
                size=sh_size,
                link=sh_link,
                entsize=sh_entsize,
            )
        )
    return sections


def _parse_dynamic(
    payload: bytes,
    section: _Section,
    dynstr: bytes,
    elf_class: int,
    endian: str,
) -> tuple[str | None, list[str]]:
    soname: str | None = None
    needed: list[str] = []
    if elf_class == 2:
        fmt = endian + "qQ"
        default_entsize = 16
    else:
        fmt = endian + "iI"
        default_entsize = 8
    entsize = section.entsize or default_entsize
    record_size = struct.calcsize(fmt)
    for offset in range(section.offset, section.offset + section.size, entsize):
        if offset + record_size > len(payload):
            break
        tag, value = struct.unpack_from(fmt, payload, offset)
        if tag == DT_NULL:
            break
        if tag == DT_SONAME:
            soname = _read_c_string(dynstr, int(value))
        elif tag == DT_NEEDED:
            needed_name = _read_c_string(dynstr, int(value))
            if needed_name:
                needed.append(needed_name)
    return soname, needed


def _parse_dynsym(
    payload: bytes,
    section: _Section,
    dynstr: bytes,
    elf_class: int,
    endian: str,
) -> tuple[set[str], set[str]]:
    imports: set[str] = set()
    exports: set[str] = set()
    if elf_class == 2:
        fmt = endian + "IBBHQQ"
        default_entsize = 24
    else:
        fmt = endian + "IIIBBH"
        default_entsize = 16
    entsize = section.entsize or default_entsize
    record_size = struct.calcsize(fmt)

    for offset in range(section.offset, section.offset + section.size, entsize):
        if offset + record_size > len(payload):
            break
        fields = struct.unpack_from(fmt, payload, offset)
        if elf_class == 2:
            st_name, st_info, _st_other, st_shndx = fields[:4]
        else:
            st_name = fields[0]
            st_info = fields[3]
            st_shndx = fields[5]
        name = _read_c_string(dynstr, int(st_name))
        if not name:
            continue
        bind = st_info >> 4
        if bind not in (STB_GLOBAL, STB_WEAK):
            continue
        if st_shndx == SHN_UNDEF:
            imports.add(name)
        else:
            exports.add(name)
    return imports, exports


def _first_section(
    sections: list[_Section],
    *,
    name: str | None = None,
    sh_type: int | None = None,
) -> _Section | None:
    for section in sections:
        if name is not None and section.name == name:
            return section
    if sh_type is not None:
        for section in sections:
            if section.sh_type == sh_type:
                return section
    return None


def _section_data(payload: bytes, section: _Section | None) -> bytes:
    if section is None:
        return b""
    return _slice(payload, section.offset, section.size)


def _slice(payload: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset > len(payload):
        return b""
    return payload[offset : min(len(payload), offset + size)]


def _read_c_string(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("utf-8", errors="replace")


_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")


def _top_strings(data: bytes, limit: int = 20) -> list[str]:
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for index, match in enumerate(_PRINTABLE_RE.finditer(data)):
        text = match.group(0).decode("utf-8", errors="replace")
        if text not in first_seen:
            first_seen[text] = index
        counts[text] += 1
    ranked = sorted(counts, key=lambda text: (-counts[text], first_seen[text], text))
    return ranked[:limit]


def _collect_compare_sets(features: dict[str, Any] | None) -> dict[str, set[str]]:
    sets = {
        "imports": set(),
        "exports": set(),
        "strings": set(),
        "needed": set(),
    }
    if not features:
        return sets
    for item in _iter_lib_items(features):
        fingerprint = item.get("fingerprint", {})
        sets["imports"].update(fingerprint.get("imported_symbols_set") or [])
        sets["exports"].update(fingerprint.get("exported_symbols_set") or [])
        sets["strings"].update(fingerprint.get("rodata_strings_top20") or [])
        sets["needed"].update(fingerprint.get("needed_libs") or [])
    return sets


def _iter_lib_items(features: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for abi in ABI_KEYS:
        for item in features.get(abi, []) or []:
            if isinstance(item, dict):
                yield item
    other_abis = features.get("other_abis", {})
    if isinstance(other_abis, dict):
        for items in other_abis.values():
            for item in items or []:
                if isinstance(item, dict):
                    yield item


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def summarize_native_lib_coverage(apk_paths: Iterable[Path | str]) -> dict[str, Any]:
    """Build a small corpus coverage summary for reporting artifacts."""
    paths = [Path(p) for p in apk_paths]
    apk_count = len(paths)
    with_native = 0
    libs_per_apk: list[int] = []
    bytes_per_apk: list[int] = []
    abi_distribution: Counter[str] = Counter()
    sample: dict[str, Any] | None = None

    for path in paths:
        features = extract_native_lib_features(path)
        items = list(_iter_lib_items(features))
        if features["native_libs_present"]:
            with_native += 1
        libs_per_apk.append(len(items))
        bytes_per_apk.append(sum(int(item.get("size", 0)) for item in items))
        for abi in ABI_KEYS:
            if features.get(abi):
                abi_distribution[abi] += 1
        if sample is None and items:
            sample_item = items[0]
            sample = {
                "apk": str(path),
                "path": sample_item.get("path"),
                "name": sample_item.get("name"),
                "size": sample_item.get("size"),
                "fingerprint": sample_item.get("fingerprint"),
            }

    return {
        "task_id": "SYS-30-NATIVE-LIB-FINGERPRINT",
        "mode": MODE,
        "apk_count": apk_count,
        "native_libs_present_count": with_native,
        "native_libs_present_ratio": (with_native / apk_count) if apk_count else 0.0,
        "abi_distribution_apk_count": dict(sorted(abi_distribution.items())),
        "libs_per_apk": _distribution(libs_per_apk),
        "native_bytes_per_apk": _distribution(bytes_per_apk),
        "sample_fingerprint": sample,
    }


def _distribution(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "median": 0.0, "p90": 0.0, "max": 0, "mean": 0.0}
    sorted_values = sorted(values)
    p90_index = min(len(sorted_values) - 1, int(0.9 * (len(sorted_values) - 1)))
    return {
        "min": sorted_values[0],
        "median": float(statistics.median(sorted_values)),
        "p90": float(sorted_values[p90_index]),
        "max": sorted_values[-1],
        "mean": float(statistics.mean(sorted_values)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="*", type=Path, help="APK files to fingerprint")
    parser.add_argument("--corpus", type=Path, help="Directory with APK files")
    parser.add_argument("--output", type=Path, help="Write coverage JSON report")
    args = parser.parse_args(argv)

    paths = list(args.apk)
    if args.corpus:
        paths.extend(sorted(args.corpus.rglob("*.apk")))
    report = summarize_native_lib_coverage(paths)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

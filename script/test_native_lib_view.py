#!/usr/bin/env python3
"""Tests for SYS-30-NATIVE-LIB-FINGERPRINT.

The fixtures build tiny APK zip files with minimal ELF shared objects.  They
exercise the public contract without relying on external APK corpora.
"""
from __future__ import annotations

import struct
import sys
import unittest
import zipfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from script.native_lib_view import compare_native_libs, extract_native_lib_features
except ImportError:
    compare_native_libs = None  # type: ignore[assignment]
    extract_native_lib_features = None  # type: ignore[assignment]


ABI_KEYS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _pack_dynsym(name_off: int, bind: int, typ: int, shndx: int) -> bytes:
    return struct.pack("<IBBHQQ", name_off, (bind << 4) | typ, 0, shndx, 0, 0)


def _make_minimal_elf64(
    *,
    soname: str,
    needed: tuple[str, ...],
    imported: tuple[str, ...],
    exported: tuple[str, ...],
    rodata_strings: tuple[str, ...],
) -> bytes:
    """Create a small little-endian ELF64 with .dynsym/.dynstr/.dynamic."""
    dynstr_parts = [b""]
    offsets: dict[str, int] = {"": 0}
    for text in (soname, *needed, *imported, *exported):
        if text not in offsets:
            offsets[text] = sum(len(part) + 1 for part in dynstr_parts)
            dynstr_parts.append(text.encode("utf-8"))
    dynstr = b"\x00".join(dynstr_parts) + b"\x00"

    dynsym = [_pack_dynsym(0, 0, 0, 0)]
    for symbol in imported:
        dynsym.append(_pack_dynsym(offsets[symbol], 1, 2, 0))
    for symbol in exported:
        dynsym.append(_pack_dynsym(offsets[symbol], 1, 2, 1))
    dynsym_data = b"".join(dynsym)

    dynamic = [struct.pack("<QQ", 14, offsets[soname])]
    dynamic.extend(struct.pack("<QQ", 1, offsets[lib]) for lib in needed)
    dynamic.append(struct.pack("<QQ", 0, 0))
    dynamic_data = b"".join(dynamic)
    rodata = b"\x00".join(s.encode("utf-8") for s in rodata_strings) + b"\x00"
    shstrtab = b"\x00.dynstr\x00.dynsym\x00.dynamic\x00.rodata\x00.shstrtab\x00"

    section_payloads = [
        b"",
        dynstr,
        dynsym_data,
        dynamic_data,
        rodata,
        shstrtab,
    ]
    section_names = [0, 1, 9, 17, 26, 34]
    section_types = [0, 3, 11, 6, 1, 3]
    section_flags = [0, 0, 0, 0, 2, 0]
    section_alignments = [0, 1, 8, 8, 1, 1]
    section_entsizes = [0, 0, 24, 16, 0, 0]
    section_links = [0, 0, 1, 1, 0, 0]
    section_infos = [0, 0, 1, 0, 0, 0]

    content = bytearray(b"\x00" * 64)
    offsets_by_section = [0]
    sizes_by_section = [0]
    for payload, alignment in zip(section_payloads[1:], section_alignments[1:]):
        padding = _align(len(content), alignment) - len(content)
        content.extend(b"\x00" * padding)
        offsets_by_section.append(len(content))
        sizes_by_section.append(len(payload))
        content.extend(payload)

    shoff = _align(len(content), 8)
    content.extend(b"\x00" * (shoff - len(content)))
    for i in range(len(section_payloads)):
        content.extend(
            struct.pack(
                "<IIQQQQIIQQ",
                section_names[i],
                section_types[i],
                section_flags[i],
                0,
                offsets_by_section[i],
                sizes_by_section[i],
                section_links[i],
                section_infos[i],
                section_alignments[i],
                section_entsizes[i],
            )
        )

    elf_header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\x00" * 9,
        3,
        62,
        1,
        0,
        0,
        shoff,
        0,
        64,
        0,
        0,
        64,
        len(section_payloads),
        5,
    )
    content[:64] = elf_header
    return bytes(content)


def _write_apk(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return path


def _require_api() -> None:
    assert callable(extract_native_lib_features), "script.native_lib_view.extract_native_lib_features is required"
    assert callable(compare_native_libs), "script.native_lib_view.compare_native_libs is required"


class TestExtractNativeLibFeatures(unittest.TestCase):
    def test_returns_abi_layers_with_so_fingerprints(self) -> None:
        _require_api()
        with self.subTest("synthetic APK with native libs"):
            tmp = Path(self._testMethodName + ".apk")
            self.addCleanup(lambda: tmp.unlink(missing_ok=True))
            elf = _make_minimal_elf64(
                soname="libsample.so",
                needed=("libc.so",),
                imported=("malloc",),
                exported=("JNI_OnLoad",),
                rodata_strings=("native-ready",),
            )
            _write_apk(
                tmp,
                {
                    "lib/arm64-v8a/libsample.so": elf,
                    "lib/x86_64/libsample.so": elf,
                },
            )
            features = extract_native_lib_features(tmp)  # type: ignore[misc]

        self.assertTrue(features["native_libs_present"])
        for abi in ABI_KEYS:
            self.assertIn(abi, features)
            self.assertIsInstance(features[abi], list)
        self.assertEqual([item["name"] for item in features["arm64-v8a"]], ["libsample.so"])
        self.assertEqual(features["armeabi-v7a"], [])
        self.assertIn("fingerprint", features["arm64-v8a"][0])

    def test_elf_fingerprint_contains_required_fields(self) -> None:
        _require_api()
        tmp = Path(self._testMethodName + ".apk")
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        elf = _make_minimal_elf64(
            soname="libsample.so",
            needed=("libc.so", "libm.so"),
            imported=("malloc", "free"),
            exported=("JNI_OnLoad", "Java_com_example_Native_ping"),
            rodata_strings=("native-ready", "sqlite", "android/log"),
        )
        _write_apk(tmp, {"lib/x86_64/libsample.so": elf})

        features = extract_native_lib_features(tmp)  # type: ignore[misc]
        fingerprint = features["x86_64"][0]["fingerprint"]

        for key in (
            "soname",
            "needed_libs",
            "imported_symbols_set",
            "exported_symbols_set",
            "rodata_strings_top20",
        ):
            self.assertIn(key, fingerprint)
        self.assertEqual(fingerprint["soname"], "libsample.so")
        self.assertEqual(fingerprint["needed_libs"], ["libc.so", "libm.so"])
        self.assertEqual(fingerprint["imported_symbols_set"], ["free", "malloc"])
        self.assertEqual(
            fingerprint["exported_symbols_set"],
            ["JNI_OnLoad", "Java_com_example_Native_ping"],
        )
        self.assertIn("native-ready", fingerprint["rodata_strings_top20"])

    def test_missing_lib_directory_returns_absent_feature_dict(self) -> None:
        _require_api()
        tmp = Path(self._testMethodName + ".apk")
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        _write_apk(tmp, {"AndroidManifest.xml": b"<manifest />"})

        features = extract_native_lib_features(tmp)  # type: ignore[misc]

        self.assertEqual(features["native_libs_present"], False)
        for abi in ABI_KEYS:
            self.assertEqual(features[abi], [])


class TestCompareNativeLibs(unittest.TestCase):
    def test_compare_native_libs_returns_jaccards_and_weighted_score(self) -> None:
        _require_api()
        features_a = {
            "native_libs_present": True,
            "arm64-v8a": [
                {
                    "name": "liba.so",
                    "fingerprint": {
                        "needed_libs": ["libc.so", "libm.so"],
                        "imported_symbols_set": ["malloc", "free"],
                        "exported_symbols_set": ["JNI_OnLoad"],
                        "rodata_strings_top20": ["alpha", "shared"],
                    },
                }
            ],
            "armeabi-v7a": [],
            "x86_64": [],
            "x86": [],
        }
        features_b = {
            "native_libs_present": True,
            "arm64-v8a": [
                {
                    "name": "libb.so",
                    "fingerprint": {
                        "needed_libs": ["libc.so"],
                        "imported_symbols_set": ["malloc", "puts"],
                        "exported_symbols_set": ["JNI_OnLoad", "Java_X"],
                        "rodata_strings_top20": ["beta", "shared"],
                    },
                }
            ],
            "armeabi-v7a": [],
            "x86_64": [],
            "x86": [],
        }

        result = compare_native_libs(features_a, features_b)  # type: ignore[misc]

        for key in (
            "jaccard_imports",
            "jaccard_exports",
            "jaccard_strings",
            "jaccard_needed",
            "score",
        ):
            self.assertIn(key, result)
        self.assertAlmostEqual(result["jaccard_imports"], 1 / 3, places=6)
        self.assertAlmostEqual(result["jaccard_exports"], 1 / 2, places=6)
        self.assertAlmostEqual(result["jaccard_strings"], 1 / 3, places=6)
        self.assertAlmostEqual(result["jaccard_needed"], 1 / 2, places=6)
        self.assertGreater(result["score"], 0.0)
        self.assertLess(result["score"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

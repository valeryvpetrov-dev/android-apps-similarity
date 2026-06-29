#!/usr/bin/env python3
"""APK packaging evidence for pairwise explanations."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

try:
    from script.signing_view import extract_apk_signature_hash
except Exception:  # pragma: no cover - direct script import fallback
    try:
        from signing_view import extract_apk_signature_hash  # type: ignore[no-redef]
    except Exception:  # pragma: no cover
        extract_apk_signature_hash = None  # type: ignore[assignment]


PACKAGING_EVIDENCE_POLICY_ID = "R_apk_packaging_evidence_policy_v1"
PACKAGING_EVIDENCE_REF = "apk_packaging_profile"
PACKAGING_EVIDENCE_ROLE = "evidence_only"
PACKAGING_SCORE_EFFECT = "none"

_PACKAGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])package(?:Name)?\s*(?:=|:)\s*[\"']?([A-Za-z0-9_$.]+)"
)
_SIGNATURE_EXTENSIONS = (".RSA", ".DSA", ".EC")
_DELTA_KIND_ORDER = (
    "manifest_package_name_delta",
    "signing_certificate_delta",
    "apk_entry_layout_delta",
    "manifest_or_metadata_packaging_delta",
)


def _decode_manifest_candidates(manifest_bytes: bytes) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for encoding in ("utf-8", "utf-16le", "utf-16be", "latin-1"):
        try:
            text = manifest_bytes.decode(encoding, errors="ignore")
        except LookupError:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if text and text not in seen:
            candidates.append(text)
            seen.add(text)

    printable = "".join(
        chr(byte) if 32 <= byte <= 126 else " " for byte in manifest_bytes
    )
    printable = re.sub(r"\s+", " ", printable).strip()
    if printable and printable not in seen:
        candidates.append(printable)
    return candidates


def extract_manifest_package_name(manifest_bytes: bytes | None) -> str | None:
    if not manifest_bytes:
        return None
    for text in _decode_manifest_candidates(manifest_bytes):
        match = _PACKAGE_PATTERN.search(text)
        if match:
            package_name = match.group(1).strip()
            if package_name:
                return package_name
    return None


def _entry_layout_tokens(
    *,
    entries: list[str],
    dex_names: list[str],
    signature_file_names: list[str],
    native_lib_names: list[str],
    asset_names: list[str],
    manifest_present: bool,
    resources_arsc_present: bool,
) -> list[str]:
    top_level = sorted({entry.split("/", 1)[0] for entry in entries if entry})
    tokens = {
        "entry_count:{}".format(len(entries)),
        "dex_count:{}".format(len(dex_names)),
        "signature_file_count:{}".format(len(signature_file_names)),
        "native_lib_count:{}".format(len(native_lib_names)),
        "asset_file_count:{}".format(len(asset_names)),
        "manifest_present:{}".format(1 if manifest_present else 0),
        "resources_arsc_present:{}".format(1 if resources_arsc_present else 0),
    }
    tokens.update("dex_name:{}".format(name) for name in dex_names)
    tokens.update("signature_file:{}".format(name) for name in signature_file_names)
    tokens.update("native_lib:{}".format(name) for name in native_lib_names[:20])
    tokens.update("asset_file:{}".format(name) for name in asset_names[:20])
    tokens.update("top_level:{}".format(name) for name in top_level)
    return sorted(tokens)


def extract_apk_packaging_profile(apk_path: str | Path | None) -> dict[str, Any]:
    path = Path(apk_path) if apk_path else None
    if path is None or not path.is_file():
        return {
            "status": "missing_apk",
            "manifest_package_name": None,
            "manifest_present": False,
            "resources_arsc_present": False,
            "entry_count": 0,
            "dex_names": [],
            "signature_file_names": [],
            "native_lib_names": [],
            "asset_names": [],
            "entry_layout_tokens": [],
        }

    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = sorted(
                entry for entry in archive.namelist() if entry and not entry.endswith("/")
            )
            entry_set = set(entries)
            manifest_present = "AndroidManifest.xml" in entry_set
            manifest_package_name = None
            if manifest_present:
                manifest_package_name = extract_manifest_package_name(
                    archive.read("AndroidManifest.xml")
                )
            dex_names = sorted(
                entry
                for entry in entries
                if entry.startswith("classes") and entry.endswith(".dex") and "/" not in entry
            )
            signature_file_names = sorted(
                entry
                for entry in entries
                if entry.startswith("META-INF/")
                and entry.upper().endswith(_SIGNATURE_EXTENSIONS)
            )
            native_lib_names = sorted(
                entry
                for entry in entries
                if entry.startswith("lib/") and entry.endswith(".so")
            )
            asset_names = sorted(entry for entry in entries if entry.startswith("assets/"))
            resources_arsc_present = "resources.arsc" in entry_set
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        return {
            "status": "analysis_failed",
            "error": str(error),
            "manifest_package_name": None,
            "manifest_present": False,
            "resources_arsc_present": False,
            "entry_count": 0,
            "dex_names": [],
            "signature_file_names": [],
            "native_lib_names": [],
            "asset_names": [],
            "entry_layout_tokens": [],
        }

    return {
        "status": "success",
        "manifest_package_name": manifest_package_name,
        "manifest_present": manifest_present,
        "resources_arsc_present": resources_arsc_present,
        "entry_count": len(entries),
        "dex_names": dex_names,
        "signature_file_names": signature_file_names,
        "native_lib_names": native_lib_names,
        "asset_names": asset_names,
        "entry_layout_tokens": _entry_layout_tokens(
            entries=entries,
            dex_names=dex_names,
            signature_file_names=signature_file_names,
            native_lib_names=native_lib_names,
            asset_names=asset_names,
            manifest_present=manifest_present,
            resources_arsc_present=resources_arsc_present,
        ),
    }


def _signature_hash(apk_path: str | Path | None) -> str | None:
    if extract_apk_signature_hash is None or apk_path is None:
        return None
    try:
        return extract_apk_signature_hash(Path(apk_path))
    except Exception:
        return None


def _sample_delta(left: list[str], right: list[str], limit: int = 12) -> list[str]:
    left_set = set(left)
    right_set = set(right)
    removed = ["-{}".format(item) for item in sorted(left_set - right_set)]
    added = ["+{}".format(item) for item in sorted(right_set - left_set)]
    return (removed + added)[:limit]


def build_packaging_evidence_fields(
    apk_a: str | Path | None,
    apk_b: str | Path | None,
) -> dict[str, Any]:
    left = extract_apk_packaging_profile(apk_a)
    right = extract_apk_packaging_profile(apk_b)

    left_package = left.get("manifest_package_name")
    right_package = right.get("manifest_package_name")
    manifest_package_delta = bool(
        left_package and right_package and left_package != right_package
    )
    manifest_or_metadata_delta = bool(
        manifest_package_delta
        or left.get("manifest_present") != right.get("manifest_present")
        or left.get("resources_arsc_present") != right.get("resources_arsc_present")
    )

    left_signature = _signature_hash(apk_a)
    right_signature = _signature_hash(apk_b)
    signing_delta = bool(
        left_signature and right_signature and left_signature != right_signature
    )

    left_layout_tokens = list(left.get("entry_layout_tokens") or [])
    right_layout_tokens = list(right.get("entry_layout_tokens") or [])
    entry_layout_delta = left_layout_tokens != right_layout_tokens

    delta_flags = {
        "manifest_package_name_delta": manifest_package_delta,
        "signing_certificate_delta": signing_delta,
        "apk_entry_layout_delta": entry_layout_delta,
        "manifest_or_metadata_packaging_delta": manifest_or_metadata_delta,
    }
    delta_kinds = [kind for kind in _DELTA_KIND_ORDER if delta_flags[kind]]
    evidence_score = min(1.0, len(delta_kinds) / len(_DELTA_KIND_ORDER))

    return {
        "packaging_evidence_policy_id": PACKAGING_EVIDENCE_POLICY_ID,
        "packaging_evidence_ref": PACKAGING_EVIDENCE_REF,
        "packaging_evidence_role": PACKAGING_EVIDENCE_ROLE,
        "packaging_score_effect": PACKAGING_SCORE_EFFECT,
        "packaging_score_included": False,
        "packaging_evidence_applied": bool(delta_kinds),
        "packaging_evidence_score": evidence_score,
        "packaging_delta_kinds": delta_kinds,
        "manifest_package_name_delta": manifest_package_delta,
        "signing_certificate_delta": signing_delta,
        "apk_entry_layout_delta": entry_layout_delta,
        "manifest_or_metadata_packaging_delta": manifest_or_metadata_delta,
        "left_manifest_package_name": left_package,
        "right_manifest_package_name": right_package,
        "left_signing_certificate_sha256": left_signature,
        "right_signing_certificate_sha256": right_signature,
        "left_apk_entry_count": int(left.get("entry_count") or 0),
        "right_apk_entry_count": int(right.get("entry_count") or 0),
        "left_dex_names": list(left.get("dex_names") or []),
        "right_dex_names": list(right.get("dex_names") or []),
        "left_signature_file_names": list(left.get("signature_file_names") or []),
        "right_signature_file_names": list(right.get("signature_file_names") or []),
        "left_native_lib_names": list(left.get("native_lib_names") or []),
        "right_native_lib_names": list(right.get("native_lib_names") or []),
        "apk_entry_layout_delta_sample": _sample_delta(
            left_layout_tokens,
            right_layout_tokens,
        ),
        "left_packaging_profile_status": left.get("status"),
        "right_packaging_profile_status": right.get("status"),
    }

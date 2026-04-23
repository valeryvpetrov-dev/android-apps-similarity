#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import zipfile
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from script.system_requirements import verify_required_dependencies
    from script.evidence_formatter import collect_evidence_from_screening_layers
except Exception:
    from system_requirements import verify_required_dependencies  # type: ignore[no-redef]
    from evidence_formatter import collect_evidence_from_screening_layers  # type: ignore[no-redef]


logger = logging.getLogger(__name__)

# EXEC-091: high-confidence shortcut
# Если на этапе первичного отбора оценка сходства очень высока и цифровая
# подпись APK совпадает — кандидат помечается как shortcut_applied=True с
# шорткат-статусом SHORTCUT_STATUS. Решение о пропуске углублённого сравнения
# принимает downstream (deepening_runner/pairwise_runner) по этим флагам.
HIGH_CONFIDENCE_SCORE_THRESHOLD = 0.95  # library_reduced или агрегированная
SHORTCUT_REQUIRES_SIGNATURE_MATCH = True  # жёсткое требование совпадения подписи
SHORTCUT_STATUS = "success_shortcut"
SHORTCUT_REASON_HIGH_CONFIDENCE = "high_confidence_signature_match"

M_STATIC_LAYERS = ("code", "component", "resource", "metadata", "library")
LAYER_ALIASES = {
    "code": ("code", "code_features", "graph_names", "code_graph_names"),
    "component": ("component", "component_features"),
    "resource": ("resource", "resource_features"),
    "metadata": ("metadata", "metadata_features"),
    "library": ("library", "library_features", "library_profile_features"),
}
MANIFEST_COMPONENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])(\.?[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*(?:Activity|Service|Receiver|Provider|Application))(?![A-Za-z0-9_$.])"
)
MANIFEST_PACKAGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])package(?:Name)?\s*(?:=|:)\s*[\"']?([A-Za-z0-9_$.]+)"
)
MANIFEST_VERSION_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:android:)?versionCode\s*(?:=|:)\s*[\"']?([0-9]+)"
)
MANIFEST_SDK_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$.])(?:android:)?(minSdkVersion|targetSdkVersion)\s*(?:=|:)\s*[\"']?([0-9]+)"
)
MANIFEST_PERMISSION_PATTERN = re.compile(
    r"android\.permission\.([A-Z][A-Z0-9_]*)"
)
MANIFEST_HARDWARE_FEATURE_PATTERN = re.compile(
    r"android\.hardware\.([A-Za-z0-9_][A-Za-z0-9_.]*)"
)
MANIFEST_SOFTWARE_FEATURE_PATTERN = re.compile(
    r"android\.software\.([A-Za-z0-9_][A-Za-z0-9_.]*)"
)
DEX_MAGIC_PREFIX = b"dex\n"
DEX_MAGIC_SUFFIX = b"\x00"
DEX_VERSION_LENGTH = 3
APK_SIG_V1_EXTENSIONS = (".RSA", ".DSA", ".EC")


def strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    result = []
    for char in line:
        if char == "\\" and not escaped:
            escaped = True
            result.append(char)
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        escaped = False
        result.append(char)
    return "".join(result).rstrip()


def split_top_level(text: str, delimiter: str) -> list[str]:
    parts = []
    current = []
    level = 0
    in_single = False
    in_double = False
    escaped = False
    for char in text:
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif not in_single and not in_double:
            if char in "[{(":
                level += 1
            elif char in "]})":
                level = max(0, level - 1)
            elif char == delimiter and level == 0:
                parts.append("".join(current).strip())
                current = []
                escaped = False
                continue
        escaped = False
        current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def parse_yaml_scalar(token: str):
    token = token.strip()
    if token == "":
        return ""
    lowered = token.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        return token[1:-1].replace('\\"', '"')
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        return token[1:-1].replace("\\'", "'")
    if token.startswith("[") and token.endswith("]"):
        inside = token[1:-1].strip()
        if not inside:
            return []
        return [parse_yaml_scalar(item) for item in split_top_level(inside, ",")]
    if token.startswith("{") and token.endswith("}"):
        inside = token[1:-1].strip()
        if not inside:
            return {}
        result = {}
        for item in split_top_level(inside, ","):
            if ":" not in item:
                raise ValueError("Invalid inline mapping item: {!r}".format(item))
            key, value = item.split(":", 1)
            result[key.strip()] = parse_yaml_scalar(value)
        return result
    try:
        if any(mark in token for mark in (".", "e", "E")):
            return float(token)
        return int(token)
    except ValueError:
        return token


def parse_yaml_lines(lines: list[tuple[int, str]], start_index: int, indent: int):
    if start_index >= len(lines):
        return None, start_index
    current_indent, current_content = lines[start_index]
    if current_indent < indent:
        return None, start_index
    if current_indent > indent:
        raise ValueError("Unexpected indentation at line item: {!r}".format(current_content))
    if current_content.startswith("- "):
        return parse_yaml_list(lines, start_index, indent)
    return parse_yaml_mapping(lines, start_index, indent)


def parse_yaml_list(lines: list[tuple[int, str]], start_index: int, indent: int):
    result = []
    index = start_index
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent != indent or not content.startswith("- "):
            break

        item_content = content[2:].strip()
        index += 1
        if item_content == "":
            item_value, index = parse_yaml_lines(lines, index, indent + 2)
            result.append(item_value)
            continue

        if ":" in item_content and not item_content.startswith(("'", '"')):
            key, rest = item_content.split(":", 1)
            item_dict = {}
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                nested_value, index = parse_yaml_lines(lines, index, indent + 2)
                item_dict[key] = nested_value
            else:
                item_dict[key] = parse_yaml_scalar(rest)

            while index < len(lines):
                next_indent, next_content = lines[index]
                if next_indent < indent + 2:
                    break
                if next_indent > indent + 2:
                    raise ValueError("Unexpected indentation in list mapping: {!r}".format(next_content))
                if next_content.startswith("- "):
                    break
                if ":" not in next_content:
                    raise ValueError("Invalid mapping item: {!r}".format(next_content))
                next_key, next_rest = next_content.split(":", 1)
                next_key = next_key.strip()
                next_rest = next_rest.strip()
                index += 1
                if next_rest == "":
                    nested_value, index = parse_yaml_lines(lines, index, indent + 4)
                    item_dict[next_key] = nested_value
                else:
                    item_dict[next_key] = parse_yaml_scalar(next_rest)
            result.append(item_dict)
            continue

        result.append(parse_yaml_scalar(item_content))
    return result, index


def parse_yaml_mapping(lines: list[tuple[int, str]], start_index: int, indent: int):
    result = {}
    index = start_index
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent != indent:
            break
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError("Invalid mapping line: {!r}".format(content))
        key, rest = content.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest == "":
            nested_value, index = parse_yaml_lines(lines, index, indent + 2)
            result[key] = nested_value
        else:
            result[key] = parse_yaml_scalar(rest)
    return result, index


def parse_simple_yaml(text: str) -> dict:
    processed: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if "\t" in raw_line:
            raise ValueError("Tabs are not supported in YAML indentation")
        line = strip_yaml_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        processed.append((indent, line.strip()))
    if not processed:
        return {}
    payload, index = parse_yaml_lines(processed, 0, processed[0][0])
    if index != len(processed):
        raise ValueError("Unable to parse YAML, trailing content starts at index {}".format(index))
    if not isinstance(payload, dict):
        raise ValueError("Top-level YAML payload must be a mapping")
    return payload


def load_yaml_or_json(path: Path) -> dict:
    raw_text = path.read_text(encoding="utf-8")
    if raw_text.lstrip().startswith("{"):
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be a mapping/object")
        return payload

    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be a mapping/object")
        return payload
    except ImportError:
        payload = parse_simple_yaml(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be a mapping/object")
        return payload


def normalize_layer_values(raw_value) -> set[str]:
    if raw_value is None:
        return set()
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        return {normalized} if normalized else set()
    if isinstance(raw_value, dict):
        values = []
        for key, value in raw_value.items():
            values.append("{}={}".format(str(key).strip(), str(value).strip()))
        return {item for item in values if item}
    if isinstance(raw_value, (list, tuple, set)):
        values = set()
        for item in raw_value:
            if item is None:
                continue
            if isinstance(item, (dict, list, tuple, set)):
                values.add(json.dumps(item, sort_keys=True, ensure_ascii=False))
            else:
                normalized = str(item).strip()
                if normalized:
                    values.add(normalized)
        return values
    normalized = str(raw_value).strip()
    return {normalized} if normalized else set()


def get_layer_value(raw_app: dict, layer: str):
    if isinstance(raw_app.get("layers"), dict):
        layers = raw_app["layers"]
        if layer in layers:
            return layers[layer]
    if isinstance(raw_app.get("features"), dict):
        features = raw_app["features"]
        if layer in features:
            return features[layer]
    for key in LAYER_ALIASES[layer]:
        if key in raw_app:
            return raw_app[key]
    return []


def normalize_app_record(raw_app: dict) -> dict:
    app_id = (
        str(raw_app.get("app_id") or raw_app.get("id") or raw_app.get("name") or "").strip()
    )
    if not app_id:
        raise ValueError("Each app record must include app_id/id/name")

    layers = {}
    for layer in M_STATIC_LAYERS:
        layers[layer] = normalize_layer_values(get_layer_value(raw_app, layer))

    apk_path_raw = raw_app.get("apk_path") or raw_app.get("resolved_apk_path") or raw_app.get("apk")
    apk_path = str(apk_path_raw).strip() if apk_path_raw else None
    if apk_path == "":
        apk_path = None
    return {
        "app_id": app_id,
        "layers": layers,
        "apk_path": apk_path,
    }


def load_app_records_from_json(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if isinstance(payload.get("apps"), list):
            raw_apps = payload["apps"]
        else:
            raw_apps = []
            for app_id, app_payload in payload.items():
                if not isinstance(app_payload, dict):
                    continue
                item = {"app_id": app_id}
                item.update(app_payload)
                raw_apps.append(item)
    elif isinstance(payload, list):
        raw_apps = payload
    else:
        raise ValueError("apps-features payload must be list or object")

    app_records = []
    for raw_app in raw_apps:
        if not isinstance(raw_app, dict):
            continue
        app_records.append(normalize_app_record(raw_app))
    return app_records


def size_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value <= 3:
        return "1_3"
    if value <= 7:
        return "4_7"
    if value <= 15:
        return "8_15"
    if value <= 31:
        return "16_31"
    if value <= 63:
        return "32_63"
    return "64_plus"


def decode_manifest_candidates(manifest_bytes: bytes) -> list[str]:
    candidates = []
    seen = set()
    for encoding in ("utf-8", "utf-16le", "utf-16be"):
        decoded = manifest_bytes.decode(encoding, errors="ignore")
        for variant in (decoded, decoded.replace("\x00", "")):
            cleaned = variant.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                candidates.append(cleaned)

    printable = "".join(
        chr(byte) if 32 <= byte <= 126 else " "
        for byte in manifest_bytes
    )
    printable = re.sub(r"\s+", " ", printable).strip()
    if printable and printable not in seen:
        candidates.append(printable)

    return candidates


def extract_manifest_metadata_tokens(manifest_bytes: bytes) -> set[str]:
    """Best-effort metadata token extraction from AndroidManifest.xml bytes.

    The stdlib-only path cannot fully decode binary AXML, so this scans multiple
    decoded views of the raw manifest bytes and extracts tokens only when the
    relevant strings are visible in the APK payload.
    """
    package_name = ""
    version_code = ""
    min_sdk = ""
    target_sdk = ""

    for text in decode_manifest_candidates(manifest_bytes):
        if not package_name:
            match = MANIFEST_PACKAGE_PATTERN.search(text)
            if match:
                package_name = match.group(1).strip()

        if not version_code:
            match = MANIFEST_VERSION_CODE_PATTERN.search(text)
            if match:
                version_code = match.group(1).strip()

        if not min_sdk or not target_sdk:
            for match in MANIFEST_SDK_PATTERN.finditer(text):
                sdk_key, sdk_value = match.groups()
                if sdk_key == "minSdkVersion" and not min_sdk:
                    min_sdk = sdk_value.strip()
                elif sdk_key == "targetSdkVersion" and not target_sdk:
                    target_sdk = sdk_value.strip()

        if package_name and version_code and min_sdk and target_sdk:
            break

    tokens: set[str] = set()
    if package_name:
        tokens.add("package_name:{}".format(package_name))
    if version_code:
        tokens.add("version_code:{}".format(version_code))
    if min_sdk:
        tokens.add("min_sdk:{}".format(min_sdk))
    if target_sdk:
        tokens.add("target_sdk:{}".format(target_sdk))
    return tokens


def _extract_dex_version_token(archive: zipfile.ZipFile) -> str | None:
    """Read first 8 bytes of classes.dex and return a 'dex_version:NNN' token.

    Returns None silently if classes.dex is absent, header unreadable, or
    magic prefix does not match the expected ``dex\\n`` signature.
    """
    try:
        header = archive.read("classes.dex")[:8]
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        logger.warning("screening_runner: cannot read classes.dex: %s", exc)
        return None
    if len(header) < 8:
        return None
    if not header.startswith(DEX_MAGIC_PREFIX):
        return None
    version_bytes = header[4:4 + DEX_VERSION_LENGTH]
    if header[7:8] != DEX_MAGIC_SUFFIX:
        return None
    try:
        version = version_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(version) != DEX_VERSION_LENGTH or not version.isdigit():
        return None
    return "dex_version:{}".format(version)


def _detect_signing_scheme(archive: zipfile.ZipFile) -> str | None:
    """Return 'v1' if META-INF has .RSA/.DSA/.EC, 'v2' if APK Sig Block v2/v3 present.

    Returns None when neither is detected. Safe to call on any zip.
    """
    try:
        for name in archive.namelist():
            if not name.startswith("META-INF/"):
                continue
            upper = name.upper()
            if upper.endswith(APK_SIG_V1_EXTENSIONS):
                return "v1"
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("screening_runner: cannot enumerate META-INF: %s", exc)
    return None


def _extract_signing_tokens(apk_path: Path, archive: zipfile.ZipFile) -> set[str]:
    """Build signing_* tokens via signing_view module.

    Produces up to three tokens:
      - signing_present:0|1
      - signing_scheme:v1|v2  (omitted if no signature)
      - signing_prefix:XXXXXXXX (first 8 hex chars; omitted if no signature)
    Each lookup is wrapped so one failure cannot mask the others.
    """
    tokens: set[str] = set()
    signing_hash: str | None = None
    try:
        try:
            from signing_view import (
                extract_apk_signature_hash,
                extract_apk_signatures_v2_fingerprint,
            )
        except ImportError:
            from script.signing_view import (  # type: ignore
                extract_apk_signature_hash,
                extract_apk_signatures_v2_fingerprint,
            )
        signing_hash = extract_apk_signature_hash(apk_path)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("screening_runner: signature hash failed for %s: %s", apk_path, exc)
        signing_hash = None

    if signing_hash:
        tokens.add("signing_present:1")
    else:
        tokens.add("signing_present:0")
        return tokens

    try:
        scheme = _detect_signing_scheme(archive)
        if scheme is None:
            # Fallback path via APK Sig Block 42 implies v2/v3 scheme
            try:
                v2_hash = extract_apk_signatures_v2_fingerprint(apk_path)
            except Exception:  # pragma: no cover
                v2_hash = None
            if v2_hash:
                scheme = "v2"
        if scheme:
            tokens.add("signing_scheme:{}".format(scheme))
    except Exception as exc:  # pragma: no cover
        logger.warning("screening_runner: scheme detection failed: %s", exc)

    try:
        prefix = signing_hash[:8]
        if len(prefix) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in prefix):
            tokens.add("signing_prefix:{}".format(prefix.lower()))
    except Exception as exc:  # pragma: no cover
        logger.warning("screening_runner: prefix extraction failed: %s", exc)

    return tokens


def _extract_permission_feature_tokens(manifest_bytes: bytes) -> set[str]:
    """Scan AndroidManifest bytes across decoded candidates for perms/features.

    Works on both plain-text XML and binary AXML: strings like
    ``android.permission.INTERNET`` are extracted from any decoded view.
    """
    tokens: set[str] = set()
    try:
        for text in decode_manifest_candidates(manifest_bytes):
            for name in MANIFEST_PERMISSION_PATTERN.findall(text):
                tokens.add("uses_permission:{}".format(name))
            for full in MANIFEST_HARDWARE_FEATURE_PATTERN.findall(text):
                tail = full.rsplit(".", 1)[-1]
                if tail:
                    tokens.add("uses_feature:{}".format(tail))
            for full in MANIFEST_SOFTWARE_FEATURE_PATTERN.findall(text):
                tail = full.rsplit(".", 1)[-1]
                if tail:
                    tokens.add("uses_feature:{}".format(tail))
    except Exception as exc:  # pragma: no cover
        logger.warning("screening_runner: permission/feature scan failed: %s", exc)
    return tokens


def extract_layers_from_apk(apk_path: Path) -> dict[str, set[str]]:
    with zipfile.ZipFile(apk_path, "r") as archive:
        entries = [entry for entry in archive.namelist() if entry and not entry.endswith("/")]
        entry_set = set(entries)

        dex_entries = sorted(
            [entry for entry in entries if entry.startswith("classes") and entry.endswith(".dex")]
        )
        has_manifest = "AndroidManifest.xml" in entry_set
        has_resources_arsc = "resources.arsc" in entry_set
        manifest_component_features = set()
        metadata = {
            "apk_name:{}".format(apk_path.stem),
            "entry_bin:{}".format(size_bucket(len(entries))),
            "dex_count_bin:{}".format(size_bucket(len(dex_entries))),
            "manifest_present:{}".format(1 if has_manifest else 0),
            "resources_arsc_present:{}".format(1 if has_resources_arsc else 0),
        }

        # EXEC-R_metadata_v2: DEX version token
        try:
            if "classes.dex" in entry_set:
                dex_version_token = _extract_dex_version_token(archive)
                if dex_version_token:
                    metadata.add(dex_version_token)
        except Exception as exc:  # pragma: no cover
            logger.warning("screening_runner: dex_version token failed: %s", exc)

        # EXEC-R_metadata_v2: signing tokens (present / scheme / prefix)
        try:
            metadata.update(_extract_signing_tokens(apk_path, archive))
        except Exception as exc:  # pragma: no cover
            logger.warning("screening_runner: signing tokens failed: %s", exc)

        if has_manifest:
            try:
                manifest_bytes = archive.read("AndroidManifest.xml")
                metadata.update(extract_manifest_metadata_tokens(manifest_bytes))
                # EXEC-R_metadata_v2: permission / feature tokens
                try:
                    metadata.update(_extract_permission_feature_tokens(manifest_bytes))
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "screening_runner: permission/feature tokens failed: %s", exc
                    )
                for text in decode_manifest_candidates(manifest_bytes):
                    for match in MANIFEST_COMPONENT_PATTERN.findall(text):
                        component = match.strip().replace("/", ".")
                        if component:
                            manifest_component_features.add("manifest_component:{}".format(component))
            except KeyError:
                has_manifest = False
                metadata.discard("manifest_present:1")
                metadata.add("manifest_present:0")

        resource = set()
        component = set(manifest_component_features)
        library = set()
        code = set("dex:{}".format(entry) for entry in dex_entries)

        for entry in entries:
            if entry.startswith("res/"):
                parts = entry.split("/")
                if len(parts) >= 2 and parts[1]:
                    res_type = parts[1].split("-", 1)[0]
                    resource.add("res_type:{}".format(res_type))
                    if res_type.startswith("layout"):
                        layout_name = Path(entry).stem
                        if layout_name:
                            component.add("layout:{}".format(layout_name))
                suffix = Path(entry).suffix.lower().lstrip(".")
                if suffix:
                    resource.add("res_ext:{}".format(suffix))
            elif entry.startswith("assets/"):
                suffix = Path(entry).suffix.lower().lstrip(".")
                if suffix:
                    resource.add("asset_ext:{}".format(suffix))
            elif entry.startswith("lib/"):
                parts = entry.split("/")
                if len(parts) >= 2 and parts[1]:
                    library.add("lib_abi:{}".format(parts[1]))
            elif entry.startswith("META-INF/"):
                suffix = Path(entry).suffix.upper().lstrip(".")
                if suffix:
                    library.add("meta_inf_ext:{}".format(suffix))

        if not code:
            code.add("dex:absent")

        return {
            "code": code,
            "component": component,
            "resource": resource,
            "metadata": metadata,
            "library": library,
        }


def extract_code_v2_hash(apk_path: Path, app_only: bool = False) -> str | None:
    """Extract opcode n-gram TLSH hash for R_code v2. Returns None on error.

    EXEC-075: When ``app_only=True``, opcodes from detected third-party
    libraries (Jetpack Compose, Material3, OkHttp, etc.) are excluded before
    hashing. This reduces screening FPR caused by shared-library overlap.
    """
    try:
        from code_view_v2 import extract_opcode_ngram_tlsh
    except ImportError:
        try:
            from script.code_view_v2 import extract_opcode_ngram_tlsh
        except ImportError:
            return None
    return extract_opcode_ngram_tlsh(apk_path, app_only=app_only)


def extract_code_v3_set(apk_path: Path):
    """Extract method opcode fingerprint (frozenset) for R_code v3.

    Returns a frozenset of per-method opcode tuples, or None on error.
    Invariant to DEX packaging (single-dex vs multi-dex).
    Inspired by MOSDroid (Computers & Security, 2025).
    """
    try:
        from code_view_v3 import extract_method_opcode_fingerprint
        return extract_method_opcode_fingerprint(Path(apk_path))
    except Exception:
        try:
            import sys as _sys
            import os as _os
            _sys.path.insert(0, _os.path.dirname(__file__))
            from code_view_v3 import extract_method_opcode_fingerprint
            return extract_method_opcode_fingerprint(Path(apk_path))
        except Exception:
            return None


def extract_api_markov(apk_path):
    """Extract API call Markov chain for R_api layer.

    Returns a dict of {(from_family, to_family): probability} or None on error.
    Inspired by MaMaDroid (NDSS 2017): API calls abstracted to package family,
    transition matrix built as Markov chain, compared via cosine similarity.
    Invariant to Java/Kotlin rewrite — both call the same Android framework APIs.
    """
    try:
        from api_view import build_markov_chain
        return build_markov_chain(Path(apk_path))
    except Exception:
        try:
            import sys as _sys
            import os as _os
            _sys.path.insert(0, _os.path.dirname(__file__))
            from api_view import build_markov_chain
            return build_markov_chain(Path(apk_path))
        except Exception:
            return None


def discover_app_records_from_apk_root(apk_root: Path) -> list[dict]:
    apk_files = sorted(apk_root.rglob("*.apk"))
    records = []
    for apk_path in apk_files:
        layers = extract_layers_from_apk(apk_path)
        records.append(
            {
                "app_id": apk_path.stem,
                "apk_path": str(apk_path.resolve()),
                "layers": layers,
            }
        )
    return records


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    union_size = len(left | right)
    if union_size == 0:
        return 0.0
    return len(left & right) / union_size


def cosine_similarity(left: set[str], right: set[str]) -> float:
    denominator = math.sqrt(len(left)) * math.sqrt(len(right))
    if denominator == 0.0:
        return 0.0
    return len(left & right) / denominator


def containment_similarity(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def dice_similarity(left: set[str], right: set[str]) -> float:
    denominator = len(left) + len(right)
    if denominator == 0:
        return 0.0
    return (2.0 * len(left & right)) / denominator


def overlap_similarity(left: set[str], right: set[str]) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def shared_count_similarity(left: set[str], right: set[str]) -> float:
    return float(len(left & right))


def levenshtein_similarity(left: set[str], right: set[str]) -> float:
    import textdistance

    left_seq = sorted(left)
    right_seq = sorted(right)
    maximum = max(len(left_seq), len(right_seq))
    if maximum == 0:
        return 0.0
    distance = textdistance.levenshtein.distance(left_seq, right_seq)
    return max(0.0, 1.0 - (distance / maximum))


def normalize_metric_name(metric: str) -> str:
    normalized = metric.strip().lower()
    aliases = {
        "jac": "jaccard",
        "jaccard_similarity": "jaccard",
        "cos": "cosine",
        "cosine_similarity": "cosine",
        "cnt": "containment",
        "intersection_over_min": "containment",
        "overlap_coefficient": "containment",
        "dice_coefficient": "dice",
        "shared_graph_count_v1": "shared_count",
    }
    return aliases.get(normalized, normalized)


def aggregate_features(app_record: dict, selected_layers: list[str]) -> set[str]:
    aggregated = set()
    layers = app_record.get("layers", {})
    for layer in selected_layers:
        layer_values = layers.get(layer, set())
        for feature in layer_values:
            aggregated.add("{}:{}".format(layer, feature))
    return aggregated


def calculate_cfg_ged_similarity(
    apk_a: str,
    apk_b: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> float:
    try:
        from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
        from script.calculate_apks_similarity.build_model import build_model
        from script.calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity
    except ImportError:
        import sys

        sys.path.append(str(Path(__file__).resolve().parent))
        from calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
        from calculate_apks_similarity.build_model import build_model
        from calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity

    with TemporaryDirectory(prefix="screening_cfg_ged_") as tmp_dir:
        output_1 = Path(tmp_dir) / "first"
        output_2 = Path(tmp_dir) / "second"
        output_1.mkdir(parents=True, exist_ok=True)
        output_2.mkdir(parents=True, exist_ok=True)
        dots_1 = build_model(apk_a, str(output_1))
        dots_2 = build_model(apk_b, str(output_2))
        if not dots_1 or not dots_2:
            return 0.0
        m_comp = build_comparison_matrix(
            dots_1,
            dots_2,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )
        similarity_score, _ = calculate_models_similarity(m_comp, dots_1, dots_2)
        return float(similarity_score)


def compute_per_view_scores(
    app_a: dict,
    app_b: dict,
    layers: list[str],
    metric: str,
) -> dict[str, float]:
    """Compute per-layer similarity scores for a pair of apps.

    Uses the same set-based metric selected for screening, but applied
    layer-by-layer instead of the union of all selected layers. For GED, the
    runtime score is not set-based, so per-layer evidence is computed post-hoc
    as Jaccard over the selected layer features. The result is a mapping
    {layer: score} suitable for downstream deepening/pairwise calibration
    (per EXEC-086 logistic regression).

    Empty features on both sides yield 0.0 by convention of the underlying
    metric functions (``jaccard_similarity`` returns 0.0 on empty union, etc.).
    """
    normalized_metric = normalize_metric_name(metric)
    if normalized_metric == "ged":
        metric_fn = jaccard_similarity
    else:
        metric_fn = {
            "jaccard": jaccard_similarity,
            "cosine": cosine_similarity,
            "containment": containment_similarity,
            "dice": dice_similarity,
            "overlap": overlap_similarity,
            "shared_count": shared_count_similarity,
            "levenshtein": levenshtein_similarity,
            "edit_distance": levenshtein_similarity,
        }.get(normalized_metric)
    if metric_fn is None:
        raise ValueError(
            "Unsupported metric for per-view scores: {!r}".format(metric)
        )

    scores: dict[str, float] = {}
    for layer in layers:
        features_a = aggregate_features(app_a, [layer])
        features_b = aggregate_features(app_b, [layer])
        scores[layer] = float(metric_fn(features_a, features_b))
    return scores


def calculate_pair_score(
    app_a: dict,
    app_b: dict,
    metric: str,
    selected_layers: list[str],
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
) -> float:
    if metric == "ged" and selected_layers == ["code"]:
        apk_a = app_a.get("apk_path")
        apk_b = app_b.get("apk_path")
        if apk_a and apk_b:
            return calculate_cfg_ged_similarity(
                apk_a=apk_a,
                apk_b=apk_b,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
        return 0.0

    features_a = aggregate_features(app_a, selected_layers)
    features_b = aggregate_features(app_b, selected_layers)

    if metric == "jaccard":
        return jaccard_similarity(features_a, features_b)
    if metric == "cosine":
        return cosine_similarity(features_a, features_b)
    if metric == "containment":
        return containment_similarity(features_a, features_b)
    if metric == "dice":
        return dice_similarity(features_a, features_b)
    if metric == "overlap":
        return overlap_similarity(features_a, features_b)
    if metric == "shared_count":
        return shared_count_similarity(features_a, features_b)
    if metric in {"levenshtein", "edit_distance"}:
        return levenshtein_similarity(features_a, features_b)
    raise ValueError("Unsupported screening metric: {!r}".format(metric))


def extract_screening_stage(config: dict) -> tuple[list[str], str, float]:
    stages = config.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("cascade-config must include object 'stages'")
    screening = stages.get("screening")
    if not isinstance(screening, dict):
        raise ValueError("cascade-config must include object 'stages.screening'")

    features = screening.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("'stages.screening.features' must be a non-empty list")

    normalized_features = []
    seen_features = set()
    for feature in features:
        feature_value = str(feature).strip().lower()
        if feature_value not in M_STATIC_LAYERS:
            raise ValueError("Unsupported feature layer in screening config: {!r}".format(feature))
        if feature_value in seen_features:
            continue
        seen_features.add(feature_value)
        normalized_features.append(feature_value)

    metric_raw = screening.get("metric", "jaccard")
    if not isinstance(metric_raw, str):
        raise ValueError("'stages.screening.metric' must be a string")
    metric = normalize_metric_name(metric_raw)

    threshold_raw = screening.get("threshold", 0.0)
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        raise ValueError("'stages.screening.threshold' must be numeric") from None

    return normalized_features, metric, threshold


def extract_candidate_index_params(
    config: dict,
    default_features: list[str],
    metric: str,
) -> dict | None:
    """Return normalized candidate_index params or None if not configured.

    Parses ``stages.screening.candidate_index`` according to
    ``system/cascade-config-schema-v1.md``. Returns a dict with keys
    ``type``, ``num_perm``, ``bands``, ``seed``, ``features`` or
    ``None`` if the block is absent. Raises ``ValueError`` for malformed
    configuration (unsupported type, non-jaccard metric, bands does not
    divide num_perm, unknown feature layer).
    """
    stages = config.get("stages")
    if not isinstance(stages, dict):
        return None
    screening = stages.get("screening")
    if not isinstance(screening, dict):
        return None
    block = screening.get("candidate_index")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("'stages.screening.candidate_index' must be a mapping")

    index_type = str(block.get("type", "")).strip().lower()
    if index_type != "minhash_lsh":
        raise ValueError(
            "Unsupported candidate_index.type: {!r}; only 'minhash_lsh' is supported".format(
                block.get("type")
            )
        )
    if metric != "jaccard":
        raise ValueError(
            "candidate_index.type=minhash_lsh requires stages.screening.metric=jaccard, got {!r}".format(metric)
        )

    try:
        num_perm = int(block.get("num_perm", 128))
    except (TypeError, ValueError):
        raise ValueError("'candidate_index.num_perm' must be an integer") from None
    if num_perm <= 0:
        raise ValueError("'candidate_index.num_perm' must be positive")

    try:
        bands = int(block.get("bands", 32))
    except (TypeError, ValueError):
        raise ValueError("'candidate_index.bands' must be an integer") from None
    if bands <= 0:
        raise ValueError("'candidate_index.bands' must be positive")
    if num_perm % bands != 0:
        raise ValueError(
            "'candidate_index.bands' ({}) must divide 'num_perm' ({}) without remainder".format(
                bands, num_perm
            )
        )

    try:
        seed = int(block.get("seed", 42))
    except (TypeError, ValueError):
        raise ValueError("'candidate_index.seed' must be an integer") from None

    features_raw = block.get("features")
    if features_raw is None:
        selected_features = list(default_features)
    else:
        if not isinstance(features_raw, list) or not features_raw:
            raise ValueError("'candidate_index.features' must be a non-empty list when provided")
        selected_features = []
        seen = set()
        for feature in features_raw:
            feature_value = str(feature).strip().lower()
            if feature_value not in M_STATIC_LAYERS:
                raise ValueError(
                    "Unsupported feature layer in candidate_index.features: {!r}".format(feature)
                )
            if feature_value in seen:
                continue
            seen.add(feature_value)
            selected_features.append(feature_value)

    return {
        "type": index_type,
        "num_perm": num_perm,
        "bands": bands,
        "seed": seed,
        "features": selected_features,
    }


def validate_app_records(app_records: list[dict]) -> None:
    if len(app_records) < 2:
        raise ValueError("At least two apps are required to build candidate pairs")
    seen_ids = set()
    for app in app_records:
        app_id = app["app_id"]
        if app_id in seen_ids:
            raise ValueError("Duplicate app_id detected: {!r}".format(app_id))
        seen_ids.add(app_id)


def collect_signature_match(apk_a_path: str | Path | None, apk_b_path: str | Path | None) -> dict:
    """EXEC-091: посчитать signature_match для пары APK на этапе первичного отбора.

    Если у пары кандидатов доступны пути к обоим APK — считаем хеш подписи
    каждого и сравниваем через ``compare_signatures`` из signing_view. Если
    хотя бы один путь пуст или файл отсутствует/не даёт хеша — возвращаем
    ``{"score": 0.0, "status": "missing"}``.

    Попытка импорта сначала из ``pairwise_runner`` (если там появится
    каноничная функция в будущем), иначе — локальный fallback на
    ``signing_view.extract_apk_signature_hash`` + ``compare_signatures``.
    """
    if apk_a_path is None or apk_b_path is None:
        return {"score": 0.0, "status": "missing"}

    path_a = Path(str(apk_a_path))
    path_b = Path(str(apk_b_path))

    # Предпочитаем каноничную реализацию из pairwise_runner, если появится.
    try:
        from pairwise_runner import collect_signature_match as _canonical  # type: ignore
        if _canonical is not collect_signature_match:  # защита от рекурсии
            return _canonical(path_a, path_b)  # type: ignore[misc]
    except Exception:
        pass

    try:
        from signing_view import extract_apk_signature_hash, compare_signatures
    except ImportError:
        try:
            from script.signing_view import (  # type: ignore
                extract_apk_signature_hash,
                compare_signatures,
            )
        except ImportError:
            return {"score": 0.0, "status": "missing"}

    hash_a = extract_apk_signature_hash(path_a)
    hash_b = extract_apk_signature_hash(path_b)
    return compare_signatures(hash_a, hash_b)


def _compute_shortcut_flags(
    aggregated_score: float,
    signature_match: dict,
) -> tuple[bool, str | None, str | None]:
    """EXEC-091: решить, применять ли сокращённый путь.

    Возвращает кортеж ``(shortcut_applied, shortcut_reason, shortcut_status)``.
    ``shortcut_applied=True`` требует одновременно:
      - ``aggregated_score >= HIGH_CONFIDENCE_SCORE_THRESHOLD``;
      - ``signature_match.status == 'match'`` (если
        ``SHORTCUT_REQUIRES_SIGNATURE_MATCH=True``).
    """
    if aggregated_score < HIGH_CONFIDENCE_SCORE_THRESHOLD:
        return False, None, None
    if SHORTCUT_REQUIRES_SIGNATURE_MATCH:
        status = signature_match.get("status") if isinstance(signature_match, dict) else None
        if status != "match":
            return False, None, None
    return True, SHORTCUT_REASON_HIGH_CONFIDENCE, SHORTCUT_STATUS


def _extract_noise_profile_fields(app_record: dict) -> tuple[list[str], str | None]:
    """Extract downstream_warnings and noise_profile_ref from an app_record.

    Looks for noise_profile_envelope stored either directly on the app_record
    (e.g. loaded from representation store) or under the 'noise_profile_envelope' key.

    Returns:
        (downstream_warnings, noise_profile_ref)
        - downstream_warnings: list of warning strings to merge into screening_warnings
        - noise_profile_ref: compact string reference to the envelope (path or id), or None
    """
    envelope: dict | None = app_record.get("noise_profile_envelope")
    if not isinstance(envelope, dict):
        return [], None

    warnings = envelope.get("downstream_warnings", [])
    if not isinstance(warnings, list):
        warnings = []

    # Build a compact ref: detector_source + status + schema_version
    detector = envelope.get("detector_source", "")
    status = envelope.get("status", "")
    schema = envelope.get("schema_version", "")
    noise_profile_ref = "noise_profile:{}:{}:{}".format(schema, detector, status) if detector else None

    return list(warnings), noise_profile_ref


def _pair_key(app_a_id: str, app_b_id: str) -> tuple[str, str]:
    if app_a_id <= app_b_id:
        return (app_a_id, app_b_id)
    return (app_b_id, app_a_id)


def _build_candidate_pairs_via_lsh(
    records: list[dict],
    candidate_index_params: dict,
) -> set[tuple[str, str]]:
    """Build the set of candidate (app_a_id, app_b_id) pairs via MinHash/LSH.

    Returns pairs with ``app_a_id < app_b_id`` to match the
    deterministic order produced by ``itertools.combinations`` over
    records sorted by ``app_id``.
    """
    from minhash_lsh import (
        LSHIndex,
        MinHashSignature,
    )

    num_perm = candidate_index_params["num_perm"]
    bands = candidate_index_params["bands"]
    seed = candidate_index_params["seed"]
    index_features = candidate_index_params["features"]

    signatures: dict[str, "MinHashSignature"] = {}
    index = LSHIndex(num_perm=num_perm, bands=bands)
    for record in records:
        feature_set = aggregate_features(record, index_features)
        signature = MinHashSignature.from_features(
            feature_set, num_perm=num_perm, seed=seed
        )
        signatures[record["app_id"]] = signature
        index.add(record["app_id"], signature)

    candidate_pairs: set[tuple[str, str]] = set()
    for record in records:
        query_id = record["app_id"]
        candidate_ids = index.query(signatures[query_id])
        for candidate_id in candidate_ids:
            if candidate_id == query_id:
                continue
            candidate_pairs.add(_pair_key(query_id, candidate_id))
    return candidate_pairs


def build_candidate_list(
    app_records: list[dict],
    selected_layers: list[str],
    metric: str,
    threshold: float,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
    processes_count: int,
    threads_count: int,
    candidate_index_params: dict | None = None,
) -> list[dict]:
    records = sorted(app_records, key=lambda item: item["app_id"])

    allowed_pairs: set[tuple[str, str]] | None = None
    if candidate_index_params is not None and metric == "jaccard":
        allowed_pairs = _build_candidate_pairs_via_lsh(records, candidate_index_params)

    candidate_list = []
    for app_a, app_b in combinations(records, 2):
        if allowed_pairs is not None:
            if _pair_key(app_a["app_id"], app_b["app_id"]) not in allowed_pairs:
                continue
        score = calculate_pair_score(
            app_a=app_a,
            app_b=app_b,
            metric=metric,
            selected_layers=selected_layers,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
            processes_count=processes_count,
            threads_count=threads_count,
        )
        if score < threshold:
            continue

        # Thread noise_profile_envelope from query_app (app_a) into screening output.
        noise_warnings, noise_profile_ref = _extract_noise_profile_fields(app_a)

        screening_explanation: dict | None = None
        if noise_profile_ref is not None:
            screening_explanation = {"noise_profile_ref": noise_profile_ref}

        # EXEC-087.1: compute per-layer scores so downstream stages can reuse
        # screening evidence (e.g. for EXEC-086 per-view weight calibration).
        per_view_scores: dict[str, float] | None
        try:
            per_view_scores = compute_per_view_scores(
                app_a=app_a,
                app_b=app_b,
                layers=list(selected_layers),
                metric=metric,
            )
        except ValueError:
            per_view_scores = None

        # EXEC-091: рассчитать signature_match и флаги сокращённого пути
        # при высоком доверии + совпадении подписи.
        signature_match = collect_signature_match(
            app_a.get("apk_path"), app_b.get("apk_path")
        )
        shortcut_applied, shortcut_reason, shortcut_status = _compute_shortcut_flags(
            aggregated_score=float(score),
            signature_match=signature_match,
        )

        # EXEC-SCREENING-APK-PATH: кладём apk_path обеих сторон прямо в запись
        # кандидата, чтобы deepening_runner / pairwise_runner читали путь
        # из записи, а оркестратор каскада не пробрасывал его вручную.
        # Если у app_record путь отсутствует (искусственные app-объекты
        # в тестах) — пишем None, downstream это понимает.
        app_a_apk_path = app_a.get("apk_path")
        app_b_apk_path = app_b.get("apk_path")

        row = {
            "app_a": app_a["app_id"],
            "app_b": app_b["app_id"],
            "query_app_id": app_a["app_id"],
            "candidate_app_id": app_b["app_id"],
            "app_a_apk_path": app_a_apk_path,
            "app_b_apk_path": app_b_apk_path,
            "retrieval_score": float(score),
            "features_used": list(selected_layers),
            "retrieval_features_used": list(selected_layers),
            "screening_warnings": noise_warnings,
            "screening_explanation": screening_explanation,
            "signature_match": signature_match,
            "shortcut_applied": shortcut_applied,
            "shortcut_reason": shortcut_reason,
            "shortcut_status": shortcut_status,
        }
        if per_view_scores is not None:
            row["per_view_scores"] = per_view_scores
            # EXEC-088-WRITERS: единый формат Evidence для первичного отбора.
            # Записывается параллельно с per_view_scores; для ged эти scores
            # считаются post-hoc через Jaccard по выбранным слоям.
            row["evidence"] = collect_evidence_from_screening_layers(
                per_view_scores, stage_name="screening"
            )
        candidate_list.append(row)
    candidate_list.sort(
        key=lambda item: (-item["retrieval_score"], item["app_a"], item["app_b"])
    )
    for index, item in enumerate(candidate_list, start=1):
        item["retrieval_rank"] = index
    return candidate_list


def _compose_candidate_row(
    query_app: dict,
    corpus_app: dict,
    score: float,
    selected_layers: list[str],
    metric: str,
) -> dict:
    """Build one candidate row from a query/corpus pair with full screening fields.

    Выделено из ``build_candidate_list`` ради переиспользования в
    ``build_candidate_list_batch``. Заполняет те же поля: per_view_scores,
    app_a_apk_path, app_b_apk_path, signature_match, shortcut_* и evidence.
    Ранжирование (``retrieval_rank``) выставляется в вызывающем коде.
    """
    noise_warnings, noise_profile_ref = _extract_noise_profile_fields(query_app)

    screening_explanation: dict | None = None
    if noise_profile_ref is not None:
        screening_explanation = {"noise_profile_ref": noise_profile_ref}

    per_view_scores: dict[str, float] | None
    try:
        per_view_scores = compute_per_view_scores(
            app_a=query_app,
            app_b=corpus_app,
            layers=list(selected_layers),
            metric=metric,
        )
    except ValueError:
        per_view_scores = None

    signature_match = collect_signature_match(
        query_app.get("apk_path"), corpus_app.get("apk_path")
    )
    shortcut_applied, shortcut_reason, shortcut_status = _compute_shortcut_flags(
        aggregated_score=float(score),
        signature_match=signature_match,
    )

    app_a_apk_path = query_app.get("apk_path")
    app_b_apk_path = corpus_app.get("apk_path")

    row = {
        "app_a": query_app["app_id"],
        "app_b": corpus_app["app_id"],
        "query_app_id": query_app["app_id"],
        "candidate_app_id": corpus_app["app_id"],
        "app_a_apk_path": app_a_apk_path,
        "app_b_apk_path": app_b_apk_path,
        "retrieval_score": float(score),
        "features_used": list(selected_layers),
        "retrieval_features_used": list(selected_layers),
        "screening_warnings": noise_warnings,
        "screening_explanation": screening_explanation,
        "signature_match": signature_match,
        "shortcut_applied": shortcut_applied,
        "shortcut_reason": shortcut_reason,
        "shortcut_status": shortcut_status,
    }
    if per_view_scores is not None:
        row["per_view_scores"] = per_view_scores
        row["evidence"] = collect_evidence_from_screening_layers(
            per_view_scores, stage_name="screening"
        )
    return row


def build_candidate_list_batch(
    query_apps: list[dict],
    corpus_apps: list[dict],
    config: dict,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    processes_count: int = 1,
    threads_count: int = 2,
) -> list[list[dict]]:
    """Пакетный первичный отбор: индекс MinHash/LSH строится один раз.

    Принцип: для ``N`` запросов (``query_apps``) против ``M`` записей
    корпуса (``corpus_apps``) индекс по ``corpus_apps`` строится
    однократно и затем переиспользуется. Функция возвращает список
    списков — по одному списку кандидатов на каждый ``query_app`` в
    порядке исходного ``query_apps``.

    Контракт формата строки кандидата совпадает с
    ``build_candidate_list``: поля ``per_view_scores``,
    ``app_a_apk_path``, ``app_b_apk_path``, ``signature_match``,
    ``shortcut_*`` и ``evidence`` заполняются аналогично. ``retrieval_rank``
    выставляется внутри каждого подсписка после сортировки по
    ``retrieval_score``.

    Обратная совместимость: ``build_candidate_list`` не меняется и
    продолжает работать на смешанном списке приложений через
    ``combinations``. ``build_candidate_list_batch`` —
    самостоятельный вход для сценариев query/corpus.

    Fallback: если ``candidate_index.type != minhash_lsh`` или блок
    отсутствует — функция честно падает обратно на
    ``build_candidate_list`` для каждого ``query_app`` в связке с полным
    ``corpus_apps``. Это сохраняет корректность результата, но теряет
    преимущество батча по скорости.
    """
    if not query_apps:
        return []

    selected_layers, metric, threshold = extract_screening_stage(config)
    candidate_index_params = extract_candidate_index_params(
        config, default_features=selected_layers, metric=metric
    )

    # Fallback: если индекс не MinHash/LSH — честно прогоняем
    # последовательный build_candidate_list для каждого query в паре с
    # полным corpus. Это даёт одинаковый контракт (list[list[dict]]) и
    # сохраняет корректность, жертвуя скоростью.
    if candidate_index_params is None or candidate_index_params.get("type") != "minhash_lsh":
        results: list[list[dict]] = []
        for query_app in query_apps:
            combined = [query_app]
            query_id = query_app["app_id"]
            for corpus_app in corpus_apps:
                if corpus_app["app_id"] == query_id:
                    continue
                combined.append(corpus_app)
            if len(combined) < 2:
                results.append([])
                continue
            per_query = build_candidate_list(
                app_records=combined,
                selected_layers=selected_layers,
                metric=metric,
                threshold=threshold,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
                candidate_index_params=None,
            )
            # Оставляем только строки, где фигурирует текущий query_app.
            filtered = [
                row for row in per_query
                if row.get("query_app_id") == query_id or row.get("candidate_app_id") == query_id
            ]
            results.append(filtered)
        return results

    # Основной путь: индекс по corpus строится один раз.
    from minhash_lsh import LSHIndex, MinHashSignature

    num_perm = candidate_index_params["num_perm"]
    bands = candidate_index_params["bands"]
    seed = candidate_index_params["seed"]
    index_features = candidate_index_params["features"]

    index = LSHIndex(num_perm=num_perm, bands=bands)
    corpus_by_id: dict[str, dict] = {}
    for record in corpus_apps:
        app_id = record["app_id"]
        if app_id in corpus_by_id:
            raise ValueError("Duplicate corpus app_id detected: {!r}".format(app_id))
        corpus_by_id[app_id] = record
        feature_set = aggregate_features(record, index_features)
        signature = MinHashSignature.from_features(
            feature_set, num_perm=num_perm, seed=seed
        )
        index.add(app_id, signature)

    results_batch: list[list[dict]] = []
    for query_app in query_apps:
        query_id = query_app["app_id"]
        query_feature_set = aggregate_features(query_app, index_features)
        query_signature = MinHashSignature.from_features(
            query_feature_set, num_perm=num_perm, seed=seed
        )
        candidate_ids = index.query(query_signature)

        per_query_rows: list[dict] = []
        for candidate_id in candidate_ids:
            if candidate_id == query_id:
                continue
            corpus_app = corpus_by_id.get(candidate_id)
            if corpus_app is None:
                continue
            score = calculate_pair_score(
                app_a=query_app,
                app_b=corpus_app,
                metric=metric,
                selected_layers=selected_layers,
                ins_block_sim_threshold=ins_block_sim_threshold,
                ged_timeout_sec=ged_timeout_sec,
                processes_count=processes_count,
                threads_count=threads_count,
            )
            if score < threshold:
                continue
            row = _compose_candidate_row(
                query_app=query_app,
                corpus_app=corpus_app,
                score=score,
                selected_layers=selected_layers,
                metric=metric,
            )
            per_query_rows.append(row)

        per_query_rows.sort(
            key=lambda item: (-item["retrieval_score"], item["app_a"], item["app_b"])
        )
        for rank, item in enumerate(per_query_rows, start=1):
            item["retrieval_rank"] = rank
        results_batch.append(per_query_rows)

    return results_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run cascade-config screening stage and return candidate_list in JSON "
            "with screening handoff fields "
            "[{app_a, app_b, query_app_id, candidate_app_id, retrieval_rank, "
            "retrieval_score, features_used, retrieval_features_used, "
            "screening_warnings, screening_explanation}]."
        )
    )
    parser.add_argument("cascade_config_path", help="Path to YAML cascade-config")
    parser.add_argument(
        "--apps-features-json",
        default="",
        help=(
            "Optional path to JSON with app features. "
            "If omitted, APKs are discovered under --apk-root."
        ),
    )
    parser.add_argument(
        "--apk-root",
        default=str(Path(__file__).resolve().parents[1] / "apk"),
        help="Root folder for APK auto-discovery when --apps-features-json is not provided.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path where candidate_list JSON will be saved.",
    )
    parser.add_argument("--ins-block-sim-threshold", type=float, default=0.80)
    parser.add_argument("--ged-timeout-sec", type=int, default=30)
    parser.add_argument("--processes-count", type=int, default=1)
    parser.add_argument("--threads-count", type=int, default=2)
    return parser.parse_args()


def run_screening(
    cascade_config_path: str | Path,
    app_records: list[dict] | None = None,
    apps_features_json_path: str | Path | None = None,
    apk_root: str | Path | None = None,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    processes_count: int = 1,
    threads_count: int = 2,
) -> list[dict]:
    if os.environ.get("SIMILARITY_SKIP_REQ_CHECK") != "1":
        verify_required_dependencies()

    config_path = Path(cascade_config_path).expanduser().resolve()
    config = load_yaml_or_json(config_path)
    selected_layers, metric, threshold = extract_screening_stage(config)
    candidate_index_params = extract_candidate_index_params(
        config, default_features=selected_layers, metric=metric
    )

    if app_records is None:
        if apps_features_json_path:
            app_records = load_app_records_from_json(Path(apps_features_json_path).expanduser().resolve())
        else:
            resolved_apk_root = (
                Path(apk_root).expanduser().resolve()
                if apk_root
                else Path(__file__).resolve().parents[1] / "apk"
            )
            app_records = discover_app_records_from_apk_root(resolved_apk_root)

    validate_app_records(app_records)
    return build_candidate_list(
        app_records=app_records,
        selected_layers=selected_layers,
        metric=metric,
        threshold=threshold,
        ins_block_sim_threshold=ins_block_sim_threshold,
        ged_timeout_sec=ged_timeout_sec,
        processes_count=processes_count,
        threads_count=threads_count,
        candidate_index_params=candidate_index_params,
    )


def main() -> None:
    # SYS-INT-16-VERIFY-DEPS-WIRE: fail-fast при отсутствии обязательных
    # зависимостей similarity-системы. Дублирует проверку внутри run_screening
    # намеренно — явный вызов в main() документирует контракт точки входа
    # и ловит ошибку до парсинга CLI-аргументов.
    if os.environ.get("SIMILARITY_SKIP_REQ_CHECK") != "1":
        verify_required_dependencies()

    args = parse_args()
    candidate_list = run_screening(
        cascade_config_path=args.cascade_config_path,
        apps_features_json_path=args.apps_features_json or None,
        apk_root=args.apk_root,
        ins_block_sim_threshold=args.ins_block_sim_threshold,
        ged_timeout_sec=args.ged_timeout_sec,
        processes_count=args.processes_count,
        threads_count=args.threads_count,
    )

    payload = json.dumps(candidate_list, ensure_ascii=False, indent=2)
    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""SCREENING-30 package rename synthetic benchmark.

Builds namespace-shift APK pairs from F-Droid v2 and measures whether the
original/shifted pair survives the screening MinHash LSH shortlist.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import string
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

try:
    from script import screening_runner
except ImportError:  # pragma: no cover - direct script execution from script/
    import screening_runner  # type: ignore[no-redef]


ARTIFACT_ID = "SCREENING-30-PACKAGE-RENAME"
SCHEMA_VERSION = "screening-package-rename-bench-v1"
DEFAULT_SEED = 42
DEFAULT_N_PAIRS = 20
DEFAULT_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_OUT = Path("experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json")
DEFAULT_APKTOOL = Path("/opt/homebrew/bin/apktool")
DEFAULT_LSH_PARAMS = {
    "type": "minhash_lsh",
    "num_perm": 128,
    "bands": 32,
    "seed": DEFAULT_SEED,
    "features": list(screening_runner.M_STATIC_LAYERS),
}

_MANIFEST_PACKAGE_RE = re.compile(
    r"(<manifest\b[^>]*\bpackage\s*=\s*)(['\"])([^'\"]+)(\2)",
    re.DOTALL,
)
_JAVA_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NamespaceShiftError(RuntimeError):
    """Raised when one APK cannot be decoded, patched, rebuilt, or signed."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def _run_command(command: list[str], *, stage: str, cwd: Path | None = None) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise NamespaceShiftError(stage, str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise NamespaceShiftError(
            stage,
            "{} exited with {}{}".format(
                command[0],
                completed.returncode,
                ": {}".format(detail[:1200]) if detail else "",
            ),
        )


def _select_apks(corpus_dir: Path, *, n_pairs: int, seed: int) -> list[Path]:
    apk_files = sorted(path for path in corpus_dir.glob("*.apk") if path.is_file())
    if len(apk_files) < n_pairs:
        raise ValueError(
            "Need at least {} APK files under {}, found {}".format(
                n_pairs, corpus_dir, len(apk_files)
            )
        )
    rng = random.Random(seed)
    return sorted(rng.sample(apk_files, n_pairs), key=lambda path: path.name)


def _random_package_suffix(rng: random.Random) -> str:
    alphabet = string.ascii_lowercase + string.digits
    tail = "".join(rng.choice(alphabet) for _ in range(10))
    return "ns{}".format(tail)


def _read_manifest_package(manifest_path: Path) -> str:
    text = manifest_path.read_text(encoding="utf-8")
    match = _MANIFEST_PACKAGE_RE.search(text)
    if not match:
        raise NamespaceShiftError(
            "manifest_patch",
            "AndroidManifest.xml has no package attribute",
        )
    return match.group(3)


def _patch_manifest_package(
    manifest_path: Path,
    *,
    original_package: str,
    shifted_package: str,
) -> None:
    text = manifest_path.read_text(encoding="utf-8")
    text = _MANIFEST_PACKAGE_RE.sub(
        lambda match: "{}{}{}{}".format(
            match.group(1),
            match.group(2),
            shifted_package,
            match.group(4),
        ),
        text,
        count=1,
    )
    text = text.replace(original_package, shifted_package)
    manifest_path.write_text(text, encoding="utf-8")


def _package_to_path(package_name: str) -> Path:
    parts = package_name.split(".")
    if not parts or any(not _JAVA_SEGMENT_RE.match(part) for part in parts):
        raise NamespaceShiftError(
            "manifest_patch",
            "Invalid package name: {}".format(package_name),
        )
    return Path(*parts)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _remove_empty_package_parents(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and _is_relative_to(current, stop_at):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _move_package_tree(root: Path, *, original_package: str, shifted_package: str) -> bool:
    original_rel = _package_to_path(original_package)
    shifted_rel = _package_to_path(shifted_package)
    source = root / original_rel
    if not source.exists():
        return False

    target = root / shifted_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise NamespaceShiftError(
            "smali_patch",
            "Target smali package path already exists: {}".format(target),
        )
    shutil.move(str(source), str(target))
    _remove_empty_package_parents(source.parent, root)
    return True


def _patch_smali_text(
    decoded_dir: Path,
    *,
    original_package: str,
    shifted_package: str,
) -> int:
    original_slash = original_package.replace(".", "/")
    shifted_slash = shifted_package.replace(".", "/")
    patched_files = 0
    for smali_file in decoded_dir.glob("smali*/**/*.smali"):
        try:
            text = smali_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        patched = text.replace(original_slash, shifted_slash).replace(
            original_package, shifted_package
        )
        if patched != text:
            smali_file.write_text(patched, encoding="utf-8")
            patched_files += 1
    return patched_files


def _patch_smali_package_dirs(
    decoded_dir: Path,
    *,
    original_package: str,
    shifted_package: str,
) -> tuple[int, int]:
    moved_roots = 0
    smali_roots = [path for path in decoded_dir.glob("smali*") if path.is_dir()]
    for root in smali_roots:
        if _move_package_tree(
            root,
            original_package=original_package,
            shifted_package=shifted_package,
        ):
            moved_roots += 1
    patched_files = _patch_smali_text(
        decoded_dir,
        original_package=original_package,
        shifted_package=shifted_package,
    )
    return moved_roots, patched_files


def _ensure_keystore(keystore_path: Path) -> Path:
    if keystore_path.exists():
        return keystore_path
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            "keytool",
            "-genkeypair",
            "-keystore",
            str(keystore_path),
            "-storepass",
            "screening30",
            "-keypass",
            "screening30",
            "-alias",
            "screening30",
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-dname",
            "CN=SCREENING-30 Package Rename Bench,O=Research,C=US",
        ],
        stage="keytool",
    )
    return keystore_path


def _sign_apk(unsigned_apk: Path, signed_apk: Path, keystore_path: Path) -> None:
    _ensure_keystore(keystore_path)
    _run_command(
        [
            "jarsigner",
            "-keystore",
            str(keystore_path),
            "-storepass",
            "screening30",
            "-keypass",
            "screening30",
            "-sigalg",
            "SHA256withRSA",
            "-digestalg",
            "SHA-256",
            "-signedjar",
            str(signed_apk),
            str(unsigned_apk),
            "screening30",
        ],
        stage="jarsigner",
    )


def build_namespace_shifted_apk(
    apk_path: Path,
    *,
    output_dir: Path,
    work_dir: Path,
    apktool_path: Path = DEFAULT_APKTOOL,
    shifted_package: str,
    keystore_path: Path,
) -> dict[str, Any]:
    decoded_dir = work_dir / "{}.decoded".format(apk_path.stem)
    unsigned_apk = work_dir / "{}.unsigned.apk".format(apk_path.stem)
    shifted_apk = output_dir / "{}__namespace_shift.apk".format(apk_path.stem)

    _run_command(
        [
            str(apktool_path),
            "decode",
            "--force",
            "--output",
            str(decoded_dir),
            str(apk_path),
        ],
        stage="apktool_decode",
    )

    manifest_path = decoded_dir / "AndroidManifest.xml"
    if not manifest_path.exists():
        raise NamespaceShiftError("manifest_patch", "AndroidManifest.xml not found")
    original_package = _read_manifest_package(manifest_path)
    _patch_manifest_package(
        manifest_path,
        original_package=original_package,
        shifted_package=shifted_package,
    )
    moved_smali_roots, patched_smali_files = _patch_smali_package_dirs(
        decoded_dir,
        original_package=original_package,
        shifted_package=shifted_package,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            str(apktool_path),
            "build",
            str(decoded_dir),
            "--output",
            str(unsigned_apk),
        ],
        stage="apktool_build",
    )
    _sign_apk(unsigned_apk, shifted_apk, keystore_path)
    return {
        "original_apk": str(apk_path.resolve()),
        "shifted_apk": str(shifted_apk.resolve()),
        "original_package": original_package,
        "shifted_package": shifted_package,
        "moved_smali_roots": moved_smali_roots,
        "patched_smali_files": patched_smali_files,
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _signature_diff_row(pair: dict[str, Any], left: set[str], right: set[str]) -> dict[str, Any]:
    original_only = sorted(left - right)
    shifted_only = sorted(right - left)
    return {
        "pair_id": pair["pair_id"],
        "original_app_id": pair["original_record"]["app_id"],
        "shifted_app_id": pair["shifted_record"]["app_id"],
        "original_only_count": len(original_only),
        "shifted_only_count": len(shifted_only),
        "common_count": len(left & right),
        "original_signature_size": len(left),
        "shifted_signature_size": len(right),
        "original_only_sample": original_only[:10],
        "shifted_only_sample": shifted_only[:10],
    }


def build_recall_report_from_pairs(
    pairs: list[dict[str, Any]],
    *,
    candidate_index_params: dict[str, Any] | None = None,
    failed_apks: list[dict[str, Any]] | None = None,
    selected_apks: list[str] | None = None,
) -> dict[str, Any]:
    params = dict(candidate_index_params or DEFAULT_LSH_PARAMS)
    records: list[dict[str, Any]] = []
    target_pairs: set[tuple[str, str]] = set()
    signatures_by_app_id: dict[str, set[str]] = {}

    for pair in pairs:
        original_record = pair["original_record"]
        shifted_record = pair["shifted_record"]
        records.extend([original_record, shifted_record])
        target_pairs.add(
            _pair_key(str(original_record["app_id"]), str(shifted_record["app_id"]))
        )
        signatures_by_app_id[str(original_record["app_id"])] = set(
            screening_runner.build_screening_signature(original_record)
        )
        signatures_by_app_id[str(shifted_record["app_id"])] = set(
            screening_runner.build_screening_signature(shifted_record)
        )

    shortlist_pairs = (
        screening_runner._build_candidate_pairs_via_lsh(records, params) if records else set()
    )
    hits = sorted(target_pairs & shortlist_pairs)
    n_pairs = len(target_pairs)
    n_in_shortlist = len(hits)
    recall = n_in_shortlist / n_pairs if n_pairs else 0.0

    jaccard_rows: list[dict[str, Any]] = []
    diff_rows: list[dict[str, Any]] = []
    lost_rows: list[dict[str, Any]] = []
    for pair in pairs:
        original_id = str(pair["original_record"]["app_id"])
        shifted_id = str(pair["shifted_record"]["app_id"])
        key = _pair_key(original_id, shifted_id)
        left = signatures_by_app_id[original_id]
        right = signatures_by_app_id[shifted_id]
        jaccard = _jaccard(left, right)
        in_shortlist = key in shortlist_pairs
        jaccard_rows.append(
            {
                "pair_id": pair["pair_id"],
                "original_app_id": original_id,
                "shifted_app_id": shifted_id,
                "original_apk": pair.get("original_apk"),
                "shifted_apk": pair.get("shifted_apk"),
                "original_package": pair.get("original_package"),
                "shifted_package": pair.get("shifted_package"),
                "jaccard": jaccard,
                "in_shortlist": in_shortlist,
            }
        )
        diff = _signature_diff_row(pair, left, right)
        diff_rows.append(diff)
        if not in_shortlist:
            lost_rows.append(
                {
                    "pair_id": pair["pair_id"],
                    "original_app_id": original_id,
                    "shifted_app_id": shifted_id,
                    "original_apk": pair.get("original_apk"),
                    "shifted_apk": pair.get("shifted_apk"),
                    "jaccard": jaccard,
                    "original_only_count": diff["original_only_count"],
                    "shifted_only_count": diff["shifted_only_count"],
                }
            )

    lost_rows.sort(
        key=lambda row: (
            float(row["jaccard"]),
            -(int(row["original_only_count"]) + int(row["shifted_only_count"])),
            str(row["pair_id"]),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": ARTIFACT_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "seed": int(params.get("seed", DEFAULT_SEED)),
            "n_requested_pairs": DEFAULT_N_PAIRS,
            "candidate_index_params": params,
        },
        "selected_apks": list(selected_apks or []),
        "n_pairs": n_pairs,
        "n_in_shortlist": n_in_shortlist,
        "recall": recall,
        "shortlist_size": len(shortlist_pairs),
        "jaccard_per_pair": jaccard_rows,
        "screening_signature_diff_per_pair": diff_rows,
        "top_3_lost_pairs": lost_rows[:3],
        "failed_apks": list(failed_apks or []),
    }


def _build_records_for_pair(pair_meta: dict[str, Any]) -> dict[str, Any]:
    original_apk = Path(pair_meta["original_apk"])
    shifted_apk = Path(pair_meta["shifted_apk"])
    original_record = {
        "app_id": "{}__original".format(original_apk.stem),
        "apk_path": str(original_apk.resolve()),
        "layers": screening_runner.extract_layers_from_apk(original_apk),
    }
    shifted_record = {
        "app_id": "{}__namespace_shift".format(original_apk.stem),
        "apk_path": str(shifted_apk.resolve()),
        "layers": screening_runner.extract_layers_from_apk(shifted_apk),
    }
    screening_runner.build_screening_signature(original_record)
    screening_runner.build_screening_signature(shifted_record)
    return {
        "pair_id": original_apk.stem,
        **pair_meta,
        "original_record": original_record,
        "shifted_record": shifted_record,
    }


def run_package_rename_bench(
    *,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    out_path: Path = DEFAULT_OUT,
    n_pairs: int = DEFAULT_N_PAIRS,
    seed: int = DEFAULT_SEED,
    apktool_path: Path = DEFAULT_APKTOOL,
    keep_work_dir: Path | None = None,
) -> dict[str, Any]:
    corpus_dir = Path(corpus_dir).expanduser().resolve()
    out_path = Path(out_path)
    selected_apks = _select_apks(corpus_dir, n_pairs=n_pairs, seed=seed)
    rng = random.Random(seed)
    failed_apks: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []

    temp_context: Any
    if keep_work_dir is not None:
        work_root = Path(keep_work_dir).expanduser().resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        temp_context = None
    else:
        temp_context = TemporaryDirectory(prefix="screening30_package_rename_")
        work_root = Path(temp_context.name)

    try:
        shifted_dir = out_path.parent / "shifted_apks"
        keystore_path = work_root / "screening30.keystore.jks"
        for apk_path in selected_apks:
            suffix = _random_package_suffix(rng)
            shifted_package = "com.fake.{}".format(suffix)
            pair_work_dir = work_root / apk_path.stem
            pair_work_dir.mkdir(parents=True, exist_ok=True)
            try:
                pair_meta = build_namespace_shifted_apk(
                    apk_path,
                    output_dir=shifted_dir,
                    work_dir=pair_work_dir,
                    apktool_path=apktool_path,
                    shifted_package=shifted_package,
                    keystore_path=keystore_path,
                )
                pairs.append(_build_records_for_pair(pair_meta))
            except NamespaceShiftError as exc:
                failed_apks.append(
                    {
                        "apk_path": str(apk_path.resolve()),
                        "stage": exc.stage,
                        "error": str(exc),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive bench guard
                failed_apks.append(
                    {
                        "apk_path": str(apk_path.resolve()),
                        "stage": "unexpected",
                        "error": str(exc) or repr(exc),
                    }
                )

        params = dict(DEFAULT_LSH_PARAMS)
        params["seed"] = int(seed)
        report = build_recall_report_from_pairs(
            pairs,
            candidate_index_params=params,
            failed_apks=failed_apks,
            selected_apks=[str(path.resolve()) for path in selected_apks],
        )
        report["config"]["n_requested_pairs"] = int(n_pairs)
        report["config"]["corpus_dir"] = str(corpus_dir)
        report["config"]["apktool_path"] = str(apktool_path)
        report["config"]["shifted_apk_dir"] = str(shifted_dir)
        write_report(out_path, report)
        return report
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus_dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--n_pairs", type=int, default=DEFAULT_N_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--apktool", type=Path, default=DEFAULT_APKTOOL)
    parser.add_argument(
        "--keep_work_dir",
        type=Path,
        default=None,
        help="Optional decoded/build work directory to keep for debugging.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = run_package_rename_bench(
        corpus_dir=args.corpus_dir,
        out_path=args.out,
        n_pairs=args.n_pairs,
        seed=args.seed,
        apktool_path=args.apktool,
        keep_work_dir=args.keep_work_dir,
    )
    print(
        "wrote {} (n_pairs={}, n_in_shortlist={}, recall={:.4f}, failed_apks={})".format(
            Path(args.out),
            report["n_pairs"],
            report["n_in_shortlist"],
            report["recall"],
            len(report["failed_apks"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

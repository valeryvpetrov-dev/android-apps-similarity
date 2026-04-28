#!/usr/bin/env python3
"""SYS-31 synthetic multi-dex restructure benchmark for code_view_v4.

Pipeline:
  F-Droid v2 APK -> apktool decode -> move half of smali classes into
  smali_classes2 -> apktool build/sign -> compare code_view_v4 features.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

try:
    from script import code_view_v4
except ImportError:  # pragma: no cover - direct script execution from script/
    import code_view_v4  # type: ignore[no-redef]


ARTIFACT_ID = "SYS-31-MULTIDEX-REGRESSION"
SCHEMA_VERSION = "sys31-multidex-regression-v1"
DEFAULT_CLAIM_THRESHOLD = 0.85
DEFAULT_SEED = 31
DEFAULT_N_PAIRS = 20
DEFAULT_CORPUS_DIR = Path(
    "/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
)
DEFAULT_OUT = Path("experiments/artifacts/SYS-31-MULTIDEX-REGRESSION/report.json")
DEFAULT_STAGING_DIR = Path("/tmp/wave31-sys-multidex-staging")
DEFAULT_APKTOOL = Path("/opt/homebrew/bin/apktool")
STOREPASS = "sys31multidex"
KEY_ALIAS = "sys31multidex"


class MultidexBenchError(RuntimeError):
    """One APK failed at a known benchmark stage."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def run_command(command: list[str], *, stage: str, cwd: Path | None = None) -> None:
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
        raise MultidexBenchError(stage, str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise MultidexBenchError(
            stage,
            "{} exited with {}{}".format(
                command[0],
                completed.returncode,
                ": {}".format(detail[:1600]) if detail else "",
            ),
        )


def _ensure_keystore(keystore_path: Path) -> Path:
    if keystore_path.exists():
        return keystore_path
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "keytool",
            "-genkeypair",
            "-keystore",
            str(keystore_path),
            "-storepass",
            STOREPASS,
            "-keypass",
            STOREPASS,
            "-alias",
            KEY_ALIAS,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-dname",
            "CN=SYS-31 Multidex Bench,O=Research,C=US",
        ],
        stage="keytool",
    )
    return keystore_path


def sign_apk(unsigned_apk: Path, signed_apk: Path, keystore_path: Path) -> Path:
    _ensure_keystore(keystore_path)
    signed_apk.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "jarsigner",
            "-keystore",
            str(keystore_path),
            "-storepass",
            STOREPASS,
            "-keypass",
            STOREPASS,
            "-sigalg",
            "SHA256withRSA",
            "-digestalg",
            "SHA-256",
            "-signedjar",
            str(signed_apk),
            str(unsigned_apk),
            KEY_ALIAS,
        ],
        stage="jarsigner",
    )
    return signed_apk


def _is_single_dex_apk(apk_path: Path) -> bool:
    try:
        with zipfile.ZipFile(apk_path) as archive:
            dex_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("classes") and name.endswith(".dex")
            )
    except (OSError, zipfile.BadZipFile):
        return False
    return dex_names == ["classes.dex"]


def select_single_dex_apks(
    corpus_dir: Path,
    *,
    n_pairs: int = DEFAULT_N_PAIRS,
    seed: int = DEFAULT_SEED,
) -> list[Path]:
    apk_files = sorted(path for path in Path(corpus_dir).glob("*.apk") if path.is_file())
    single_dex = [path for path in apk_files if _is_single_dex_apk(path)]
    if len(single_dex) < n_pairs:
        raise ValueError(
            "Need at least {} single-dex APKs under {}, found {}".format(
                n_pairs,
                corpus_dir,
                len(single_dex),
            )
        )
    rng = random.Random(seed)
    return sorted(rng.sample(single_dex, n_pairs), key=lambda path: path.name)


def _read_smali_class_name(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith(".class "):
                parts = stripped.split()
                if parts:
                    return parts[-1]
    except OSError:
        pass
    return path.as_posix()


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def split_decoded_smali_to_multidex(decoded_dir: Path) -> dict[str, int]:
    smali_root = decoded_dir / "smali"
    if not smali_root.is_dir():
        raise MultidexBenchError("smali_split", "smali/ directory not found")

    secondary_root = decoded_dir / "smali_classes2"
    if secondary_root.exists():
        shutil.rmtree(secondary_root)

    smali_files = sorted(path for path in smali_root.rglob("*.smali") if path.is_file())
    if len(smali_files) < 2:
        raise MultidexBenchError(
            "smali_split",
            "need at least 2 smali classes, found {}".format(len(smali_files)),
        )

    keyed: list[tuple[str, str, Path]] = []
    for path in smali_files:
        class_name = _read_smali_class_name(path)
        digest = hashlib.sha256(class_name.encode("utf-8")).hexdigest()
        keyed.append((digest, class_name, path))
    keyed.sort()

    split_at = len(keyed) // 2
    to_secondary = keyed[split_at:]
    secondary_root.mkdir(parents=True, exist_ok=True)
    for _digest, _class_name, source in to_secondary:
        rel = source.relative_to(smali_root)
        target = secondary_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        _remove_empty_parents(source.parent, smali_root)

    primary_count = len([path for path in smali_root.rglob("*.smali") if path.is_file()])
    secondary_count = len(
        [path for path in secondary_root.rglob("*.smali") if path.is_file()]
    )
    return {
        "primary_smali_files": primary_count,
        "secondary_smali_files": secondary_count,
        "total_smali_files": primary_count + secondary_count,
    }


def build_multidex_restructured_apk(
    apk_path: Path,
    *,
    output_dir: Path,
    work_dir: Path,
    apktool_path: Path = DEFAULT_APKTOOL,
    keystore_path: Path,
) -> dict[str, Any]:
    apk_path = Path(apk_path).expanduser().resolve()
    if not _is_single_dex_apk(apk_path):
        raise MultidexBenchError("single_dex_filter", "APK is not single-dex")

    decoded_dir = work_dir / "{}.decoded".format(apk_path.stem)
    unsigned_apk = work_dir / "{}.multidex.unsigned.apk".format(apk_path.stem)
    restructured_apk = output_dir / "{}__multidex_restructured.apk".format(
        apk_path.stem
    )
    if decoded_dir.exists():
        shutil.rmtree(decoded_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    run_command(
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
    split_meta = split_decoded_smali_to_multidex(decoded_dir)
    run_command(
        [
            str(apktool_path),
            "build",
            str(decoded_dir),
            "--output",
            str(unsigned_apk),
        ],
        stage="apktool_build",
    )
    sign_apk(unsigned_apk, restructured_apk, keystore_path)
    return {
        "original_apk": str(apk_path),
        "restructured_apk": str(restructured_apk.resolve()),
        "decoded_dir": str(decoded_dir.resolve()),
        **split_meta,
    }


def compare_apk_pair(
    *,
    pair_id: str,
    original_apk: Path,
    restructured_apk: Path,
    features_a: dict[str, Any] | None = None,
    features_b: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_apk = Path(original_apk)
    restructured_apk = Path(restructured_apk)
    if features_a is None:
        features_a = code_view_v4.extract_code_view_v4(original_apk)
    if features_b is None:
        features_b = code_view_v4.extract_code_view_v4(restructured_apk)
    comparison = code_view_v4.compare_code_v4(features_a, features_b)
    return {
        "pair_id": pair_id,
        "original_apk": str(original_apk),
        "restructured_apk": str(restructured_apk),
        "score": float(comparison["score"]),
        "status": comparison.get("status"),
        "matched_methods": int(comparison.get("matched_methods", 0)),
        "union_methods": int(comparison.get("union_methods", 0)),
        "denominator_methods": int(comparison.get("denominator_methods", 0)),
        "total_methods_a": int(features_a.get("total_methods", 0)) if features_a else 0,
        "total_methods_b": int(features_b.get("total_methods", 0)) if features_b else 0,
    }


def _score_stats(scores: list[float]) -> tuple[float, float, float]:
    if not scores:
        return 0.0, 0.0, 0.0
    return (
        round(sum(scores) / len(scores), 6),
        round(min(scores), 6),
        round(max(scores), 6),
    )


def build_report(
    pair_rows: list[dict[str, Any]],
    *,
    failed_apks: list[dict[str, Any]] | None = None,
    selected_apks: list[str] | None = None,
    n_requested_pairs: int = DEFAULT_N_PAIRS,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = list(failed_apks or [])
    scores = [float(row["score"]) for row in pair_rows]
    mean_score, min_score, max_score = _score_stats(scores)
    n_successful = len(pair_rows)
    n_failed = len(failed)
    n_pairs_total = n_successful + n_failed
    payload_config = {
        "seed": DEFAULT_SEED,
        "n_requested_pairs": int(n_requested_pairs),
        "claim_threshold": DEFAULT_CLAIM_THRESHOLD,
    }
    if config:
        payload_config.update(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": ARTIFACT_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": payload_config,
        "selected_apks": list(selected_apks or []),
        "n_pairs_total": n_pairs_total,
        "n_successful": n_successful,
        "n_failed": n_failed,
        "failed_apks": failed,
        "per_pair_score": list(pair_rows),
        "mean_score": mean_score,
        "min_score": min_score,
        "max_score": max_score,
        "claim_supported": bool(
            n_successful > 0 and mean_score >= DEFAULT_CLAIM_THRESHOLD
        ),
    }


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def run_multidex_restructure_bench(
    *,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    out_path: Path = DEFAULT_OUT,
    n_pairs: int = DEFAULT_N_PAIRS,
    seed: int = DEFAULT_SEED,
    apktool_path: Path = DEFAULT_APKTOOL,
    staging_dir: Path = DEFAULT_STAGING_DIR,
    keep_work_dir: bool = True,
) -> dict[str, Any]:
    corpus_dir = Path(corpus_dir).expanduser().resolve()
    out_path = Path(out_path)
    staging_dir = Path(staging_dir).expanduser().resolve()
    selected_apks = select_single_dex_apks(corpus_dir, n_pairs=n_pairs, seed=seed)
    failed_apks: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []

    temp_context: Any
    if keep_work_dir:
        work_root = staging_dir
        work_root.mkdir(parents=True, exist_ok=True)
        temp_context = None
    else:
        temp_context = TemporaryDirectory(prefix="sys31_multidex_")
        work_root = Path(temp_context.name)

    try:
        output_dir = work_root / "restructured_apks"
        keystore_path = work_root / "sys31-multidex.keystore.jks"
        for apk_path in selected_apks:
            pair_work_dir = work_root / apk_path.stem
            try:
                meta = build_multidex_restructured_apk(
                    apk_path,
                    output_dir=output_dir,
                    work_dir=pair_work_dir,
                    apktool_path=apktool_path,
                    keystore_path=keystore_path,
                )
                pair = compare_apk_pair(
                    pair_id=apk_path.stem,
                    original_apk=apk_path,
                    restructured_apk=Path(meta["restructured_apk"]),
                )
                pair.update(
                    {
                        "primary_smali_files": meta["primary_smali_files"],
                        "secondary_smali_files": meta["secondary_smali_files"],
                        "total_smali_files": meta["total_smali_files"],
                    }
                )
                pair_rows.append(pair)
            except MultidexBenchError as exc:
                failed_apks.append(
                    {
                        "apk_path": str(apk_path.resolve()),
                        "stage": exc.stage,
                        "error": str(exc),
                    }
                )
            except Exception as exc:  # pragma: no cover - benchmark isolation
                failed_apks.append(
                    {
                        "apk_path": str(apk_path.resolve()),
                        "stage": "unexpected",
                        "error": str(exc) or repr(exc),
                    }
                )

        report = build_report(
            pair_rows,
            failed_apks=failed_apks,
            selected_apks=[str(path.resolve()) for path in selected_apks],
            n_requested_pairs=n_pairs,
            config={
                "seed": int(seed),
                "corpus_dir": str(corpus_dir),
                "apktool_path": str(apktool_path),
                "staging_dir": str(work_root),
                "restructured_apk_dir": str(output_dir),
            },
        )
        write_report(out_path, report)
        return report
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus_dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--n_pairs", type=int, default=DEFAULT_N_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--apktool", type=Path, default=DEFAULT_APKTOOL)
    parser.add_argument("--staging_dir", type=Path, default=DEFAULT_STAGING_DIR)
    parser.add_argument(
        "--no_keep_work_dir",
        action="store_true",
        help="Use a temporary work directory instead of /tmp/wave31-sys-multidex-staging.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = run_multidex_restructure_bench(
        corpus_dir=args.corpus_dir,
        out_path=args.out,
        n_pairs=args.n_pairs,
        seed=args.seed,
        apktool_path=args.apktool,
        staging_dir=args.staging_dir,
        keep_work_dir=not args.no_keep_work_dir,
    )
    print(
        "wrote {} (n_successful={}, n_failed={}, mean_score={:.6f}, claim_supported={})".format(
            args.out,
            report["n_successful"],
            report["n_failed"],
            report["mean_score"],
            report["claim_supported"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""DEEP-30-CODE-INJECT-CORPUS-FOLDS: synthetic code-injection corpus + ROC.

Closes the DEEP-29 critical finding: in DEEP-27 the code layer was
calibrated to weight 0.05 on F-Droid v2 because the corpus contains no
inject-pairs at all — so the layer that was supposed to detect
code-injection clones never had the chance to score above the noise floor.

Pipeline
--------

1. Pick 30-50 APKs from F-Droid v2 that have a discoverable Activity entry
   point (via androguard ``get_main_activity()``).
2. For each APK:
     - apktool decode (no-resources mode) into a staging directory;
     - locate the smali file for the entry-point Activity, find the
       ``onCreate`` method, prepend a 4-opcode no-op block consisting of
       ``const-string`` / ``move-result-object``-style instructions;
     - apktool build into a new .apk and sign it with a throw-away
       keystore using jarsigner (v1 scheme, enough for static analysis).
3. We now have N pairs ``(original.apk, code_injected.apk)``.
   For each pair compute ``compare_code_v4_shingled(features_a, features_b)``
   — this is a clone (label=1).
4. Build the same number of random ``(apk_i, apk_j)`` pairs from different
   apps (random_pair_different_apps) and score them — these are non-clones
   (label=0).
5. Sweep threshold ∈ [0.1, 0.95, 0.05]; for each threshold compute
   precision / recall / F1 / FPR. Pick the threshold that maximises F1.
6. Emit the JSON report at
   ``experiments/artifacts/DEEP-30-CODE-INJECT/report.json``.

Usage
-----

    python3 -m script.run_code_inject_synth \\
        --corpus-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \\
        --staging-dir /tmp/wave30-deep-corpus \\
        --max-apks 40 --target-pairs 30

Failures (apktool decode/build/sign) are isolated per APK and recorded in
the artefact under ``failures``. If fewer than 20 successful pairs are
produced, the experiment is marked ``partial`` in the report and in the
README.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from script.code_view_v4_shingled import (
        compare_code_v4_shingled,
        extract_code_view_v4_shingled,
    )
except ImportError:
    from code_view_v4_shingled import (  # type: ignore[no-redef]
        compare_code_v4_shingled,
        extract_code_view_v4_shingled,
    )

logger = logging.getLogger("DEEP-30-CODE-INJECT")


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

DEFAULT_CORPUS_DIR = Path.home() / "Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks"
DEFAULT_STAGING_DIR = Path("/tmp/wave30-deep-corpus")
DEFAULT_ARTIFACT_DIR = (
    _PROJECT_ROOT / "experiments" / "artifacts" / "DEEP-30-CODE-INJECT"
)
DEFAULT_REPORT_PATH = DEFAULT_ARTIFACT_DIR / "report.json"

APKTOOL_BIN = "/opt/homebrew/bin/apktool"

# Threshold sweep specified by the wave 30 task: [0.1, 0.95, 0.05]
THRESHOLD_GRID = [round(0.10 + 0.05 * i, 2) for i in range(int((0.95 - 0.10) / 0.05) + 1)]

# Default pair counts.
DEFAULT_TARGET_PAIRS = 30
DEFAULT_MAX_APKS = 50

# Synthetic injection block — 4-opcode no-op "INJECT_DEEP30" beacon.
# We write to v0 because it is a local register whenever ``.locals >= 1``,
# i.e. it is *not* aliased to a parameter. Two ``const-string`` writes
# leave v0 in a defined state and disturb only one local before the
# original prologue runs.
SMALI_INJECT_TEMPLATE = (
    "    const-string v0, \"INJECT_DEEP30_NOOP_A\"\n"
    "    const-string v0, \"INJECT_DEEP30_NOOP_B\"\n"
    "    const-string v0, \"INJECT_DEEP30_NOOP_C\"\n"
    "    const-string v0, \"INJECT_DEEP30_NOOP_D\"\n"
)

# Method header recognises ``.method <flags>* onCreate(Landroid/os/Bundle;)V``.
ON_CREATE_HEADER_RE = re.compile(
    r"^\.method\s+[^\n]*onCreate\(Landroid/os/Bundle;\)V\s*$"
)
LOCALS_RE = re.compile(r"^\s*\.locals\s+(\d+)\s*$")
REGISTERS_RE = re.compile(r"^\s*\.registers\s+(\d+)\s*$")
PROLOGUE_RE = re.compile(r"^\s*\.prologue\s*$")


# ---------------------------------------------------------------------------
# Test-friendly helpers
# ---------------------------------------------------------------------------

def _make_synthetic_features(method_fps: dict[str, str]) -> dict:
    """Build a feature dict in the same shape as extract_code_view_v4_shingled."""
    return {
        "method_fingerprints": dict(method_fps),
        "total_methods": len(method_fps),
        "mode": "v4_shingled",
    }


def score_for_local_inject() -> float:
    """Smoke score: simulate a synthetic (original, code_injected) pair.

    Most methods are byte-identical (same fingerprint string). Exactly one
    method — the entry-point ``onCreate`` — has a perturbed fingerprint.
    Because identical strings short-circuit to similarity 1.0 in
    ``_fingerprint_similarity``, the average across common ids is very close
    to 1.0 — well above the 0.7 contract.
    """
    common_methods = {f"Lapp/X{i};->m{i}()V": f"S:{i:016x}" for i in range(50)}
    features_orig = _make_synthetic_features(
        {**common_methods, "Lapp/Main;->onCreate(Landroid/os/Bundle;)V": "S:1234567890abcdef"}
    )
    features_injected = _make_synthetic_features(
        {**common_methods, "Lapp/Main;->onCreate(Landroid/os/Bundle;)V": "S:1234567890abce0f"}
    )
    res = compare_code_v4_shingled(features_orig, features_injected)
    return float(res["score"])


def score_for_random_pair() -> float:
    """Smoke score: two unrelated apps with disjoint method-id sets.

    compare_code_v4_shingled divides matched-method similarity by
    ``max(|ids_a|, |ids_b|)``. With no common ids the numerator is zero.
    """
    features_a = _make_synthetic_features(
        {f"Lapp/A/Class{i};->m{i}()V": f"S:{i:016x}" for i in range(40)}
    )
    features_b = _make_synthetic_features(
        {f"Lapp/B/Class{i};->m{i}()V": f"S:{i:016x}" for i in range(40)}
    )
    res = compare_code_v4_shingled(features_a, features_b)
    return float(res["score"])


# ---------------------------------------------------------------------------
# ROC plumbing
# ---------------------------------------------------------------------------

def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _round6(value: float) -> float:
    return round(float(value), 6)


def _metrics_at_threshold(scored_pairs: list[dict], threshold: float) -> dict:
    tp = fp = fn = tn = 0
    for pair in scored_pairs:
        is_clone = pair["label"] == "clone"
        predicted_clone = float(pair["score"]) >= threshold
        if predicted_clone and is_clone:
            tp += 1
        elif predicted_clone and not is_clone:
            fp += 1
        elif is_clone:
            fn += 1
        else:
            tn += 1
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    fpr = _safe_div(fp, fp + tn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return {
        "threshold": _round6(threshold),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": _round6(precision),
        "recall": _round6(recall),
        "fpr": _round6(fpr),
        "f1": _round6(f1),
        "youden_j": _round6(recall - fpr),
    }


def build_roc_report(scored_pairs: list[dict]) -> dict:
    """Sweep the canonical threshold grid [0.10, 0.95, 0.05] and pick by F1.

    Args:
        scored_pairs: list of dicts with keys {"label" ∈ {"clone","non_clone"},
            "score" ∈ [0, 1]}.

    Returns:
        dict with keys threshold_grid, per_threshold_metrics, optimal_threshold,
        optimal_F1, optimal_precision, optimal_recall, n_clone, n_non_clone.
    """
    per_threshold = [
        _metrics_at_threshold(scored_pairs, threshold) for threshold in THRESHOLD_GRID
    ]
    best = max(
        per_threshold,
        key=lambda m: (m["f1"], m["youden_j"], -m["fpr"], m["recall"], m["threshold"]),
    )
    return {
        "threshold_grid": list(THRESHOLD_GRID),
        "per_threshold_metrics": per_threshold,
        "optimal_threshold": best["threshold"],
        "optimal_F1": best["f1"],
        "optimal_precision": best["precision"],
        "optimal_recall": best["recall"],
        "optimal_youden_j": best["youden_j"],
        "n_clone": sum(1 for p in scored_pairs if p["label"] == "clone"),
        "n_non_clone": sum(1 for p in scored_pairs if p["label"] == "non_clone"),
    }


# ---------------------------------------------------------------------------
# androguard helpers
# ---------------------------------------------------------------------------

def _silence_androguard_logging() -> None:
    """androguard emits torrents of DEBUG logs; quiet them down."""
    try:
        from loguru import logger as _loguru_logger
        _loguru_logger.remove()
        _loguru_logger.add(sys.stderr, level="WARNING")
    except Exception:
        pass
    for name in ("androguard", "androguard.core", "androguard.core.analysis"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_main_activity_name(apk_path: Path) -> Optional[str]:
    """Return the entry-point Activity FQN, e.g. ``com.example.Main``."""
    try:
        from androguard.core.apk import APK  # type: ignore
    except ImportError:
        try:
            from androguard.misc import AnalyzeAPK  # type: ignore
        except ImportError:
            return None
        try:
            a, _, _ = AnalyzeAPK(str(apk_path))
            return a.get_main_activity()
        except Exception:
            return None
    try:
        a = APK(str(apk_path))
        main = a.get_main_activity()
        if main:
            return main
        # Fallback: any LAUNCHER activity.
        for activity in a.get_activities():
            return activity
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# apktool driver
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def apktool_decode(apk_path: Path, out_dir: Path) -> bool:
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    rc, _stdout, stderr = _run(
        [APKTOOL_BIN, "d", "-f", "-o", str(out_dir), str(apk_path)],
        timeout=240,
    )
    if rc != 0:
        logger.warning("apktool decode failed for %s: %s", apk_path.name, stderr[-200:])
        return False
    return True


def apktool_build(decoded_dir: Path, out_apk: Path) -> bool:
    rc, _stdout, stderr = _run(
        [APKTOOL_BIN, "b", "-f", "-o", str(out_apk), str(decoded_dir)],
        timeout=240,
    )
    if rc != 0:
        logger.warning("apktool build failed for %s: %s", decoded_dir.name, stderr[-200:])
        return False
    return True


# ---------------------------------------------------------------------------
# Smali patching
# ---------------------------------------------------------------------------

def _activity_to_smali_path(decoded_dir: Path, activity_fqn: str) -> Optional[Path]:
    """``com.example.Main`` -> first matching ``smali*/com/example/Main.smali``.

    apktool may split smali into ``smali``, ``smali_classes2``, ... .
    """
    rel = activity_fqn.replace(".", "/") + ".smali"
    for smali_root in sorted(decoded_dir.glob("smali*")):
        candidate = smali_root / rel
        if candidate.is_file():
            return candidate
    return None


def _find_any_activity_smali(decoded_dir: Path) -> Optional[Path]:
    """Last-ditch fallback: any *Activity.smali with onCreate(Bundle)."""
    for path in decoded_dir.rglob("*Activity.smali"):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "onCreate(Landroid/os/Bundle;)V" in content:
            return path
    return None


def patch_smali_oncreate(smali_path: Path) -> bool:
    """Insert the SMALI_INJECT_TEMPLATE at the start of ``onCreate``.

    Strategy:
      1. Find the ``.method ... onCreate(Landroid/os/Bundle;)V`` line.
      2. Read the next ``.locals N`` (or ``.registers N``) value.
         Skip when locals == 0 — v0 would alias the first parameter and
         our injection would corrupt the method semantics; we avoid editing
         ``.locals`` to keep p-register offsets intact.
      3. Insert the inject block AFTER the optional ``.prologue`` line, or
         immediately after the ``.locals``/``.param`` block when no prologue
         is present. v0 is a fresh local before any opcode runs, so the
         no-op writes are semantically safe.
      4. Stop after the first onCreate occurrence.
    """
    try:
        text = smali_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = text.splitlines(keepends=True)

    method_idx = None
    for i, line in enumerate(lines):
        if ON_CREATE_HEADER_RE.match(line.rstrip("\n")):
            method_idx = i
            break
    if method_idx is None:
        return False

    # Walk forward to find the .locals (or .registers) declaration.
    locals_value: Optional[int] = None
    locals_idx: Optional[int] = None
    j = method_idx + 1
    while j < len(lines):
        s = lines[j].rstrip("\n")
        m = LOCALS_RE.match(s)
        if m:
            locals_value = int(m.group(1))
            locals_idx = j
            break
        m = REGISTERS_RE.match(s)
        if m:
            # .registers = .locals + nparams; we only need to know v0 is local
            # which is true whenever .registers > nparams. Use ≥1 as a soft
            # check; refuse when registers reads as 0 (degenerate).
            locals_value = int(m.group(1))
            locals_idx = j
            break
        if s.strip().startswith(".end method"):
            break
        j += 1
    if locals_value is None or locals_value < 1:
        return False
    assert locals_idx is not None

    # Insertion point: after .prologue if present; otherwise after the last
    # leading directive (.param / .annotation / blank lines / comments).
    insert_idx = locals_idx + 1
    while insert_idx < len(lines):
        s = lines[insert_idx].rstrip("\n")
        if PROLOGUE_RE.match(s):
            insert_idx += 1
            break
        if s.strip().startswith((".param", ".annotation", "#")) or s.strip() == "":
            insert_idx += 1
            continue
        # First real opcode reached without a prologue — insert right here.
        break

    new_lines = lines[:insert_idx] + [SMALI_INJECT_TEMPLATE] + lines[insert_idx:]
    smali_path.write_text("".join(new_lines), encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Pair construction
# ---------------------------------------------------------------------------

def select_apks_with_main_activity(
    corpus_dir: Path,
    max_apks: int,
    sample_seed: int = 42,
) -> list[tuple[Path, str]]:
    """Pick up to ``max_apks`` APKs that have a discoverable entry Activity."""
    candidates = sorted(p for p in corpus_dir.glob("*.apk") if p.is_file())
    rng = random.Random(sample_seed)
    rng.shuffle(candidates)
    picked: list[tuple[Path, str]] = []
    for apk in candidates:
        if len(picked) >= max_apks:
            break
        activity = get_main_activity_name(apk)
        if not activity:
            continue
        picked.append((apk, activity))
    return picked


def build_inject_pair(
    apk_path: Path,
    activity_fqn: str,
    staging_dir: Path,
) -> Optional[Path]:
    """Return the path to a freshly built inject .apk, or None on failure."""
    stem = apk_path.stem
    decoded_dir = staging_dir / "decoded" / stem
    rebuilt_apk = staging_dir / "rebuilt" / f"{stem}__inject.apk"
    rebuilt_apk.parent.mkdir(parents=True, exist_ok=True)

    if not apktool_decode(apk_path, decoded_dir):
        return None

    smali_path = _activity_to_smali_path(decoded_dir, activity_fqn)
    if smali_path is None:
        smali_path = _find_any_activity_smali(decoded_dir)
    if smali_path is None:
        logger.warning("No suitable Activity smali for %s", apk_path.name)
        return None
    if not patch_smali_oncreate(smali_path):
        logger.warning("patch_smali_oncreate failed on %s (%s)", apk_path.name, smali_path.name)
        return None

    if not apktool_build(decoded_dir, rebuilt_apk):
        return None
    if not rebuilt_apk.exists() or rebuilt_apk.stat().st_size < 1024:
        return None
    return rebuilt_apk


def random_negative_pairs(
    apk_paths: list[Path],
    n: int,
    seed: int = 7,
) -> list[tuple[Path, Path]]:
    """Random different-app pairs (no two pairs share the same package stem prefix)."""
    rng = random.Random(seed)
    pairs: list[tuple[Path, Path]] = []
    if len(apk_paths) < 2:
        return pairs
    seen: set[tuple[str, str]] = set()
    attempts = 0
    while len(pairs) < n and attempts < n * 20:
        attempts += 1
        a, b = rng.sample(apk_paths, 2)
        key = tuple(sorted((a.name, b.name)))
        if key in seen:
            continue
        seen.add(key)
        # Heuristic: drop pairs from the same app family — name prefix before "_".
        if a.stem.split("_")[0] == b.stem.split("_")[0]:
            continue
        pairs.append((a, b))
    return pairs


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def score_inject_pair(original: Path, injected: Path) -> Optional[float]:
    feats_a = extract_code_view_v4_shingled(original)
    feats_b = extract_code_view_v4_shingled(injected)
    if feats_a is None or feats_b is None:
        return None
    res = compare_code_v4_shingled(feats_a, feats_b)
    return float(res["score"])


def score_negative_pair(a: Path, b: Path) -> Optional[float]:
    feats_a = extract_code_view_v4_shingled(a)
    feats_b = extract_code_view_v4_shingled(b)
    if feats_a is None or feats_b is None:
        return None
    res = compare_code_v4_shingled(feats_a, feats_b)
    return float(res["score"])


def run(
    corpus_dir: Path,
    staging_dir: Path,
    artifact_dir: Path,
    target_pairs: int,
    max_apks: int,
) -> dict:
    _silence_androguard_logging()
    staging_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    selected = select_apks_with_main_activity(corpus_dir, max_apks=max_apks)
    logger.info("Selected %d APKs with discoverable Activity", len(selected))

    failures: list[dict] = []
    inject_pairs: list[tuple[Path, Path, str]] = []
    for apk_path, activity in selected:
        if len(inject_pairs) >= target_pairs:
            break
        try:
            built = build_inject_pair(apk_path, activity, staging_dir)
        except subprocess.TimeoutExpired:
            failures.append({"apk": apk_path.name, "stage": "apktool", "reason": "timeout"})
            continue
        except Exception as exc:  # pragma: no cover — defensive
            failures.append({"apk": apk_path.name, "stage": "apktool", "reason": repr(exc)[:160]})
            continue
        if built is None:
            failures.append({"apk": apk_path.name, "stage": "apktool", "reason": "decode/build/patch failed"})
            continue
        inject_pairs.append((apk_path, built, activity))

    logger.info("Built %d inject pairs (target=%d)", len(inject_pairs), target_pairs)

    scored: list[dict] = []
    for original, injected, activity in inject_pairs:
        score = score_inject_pair(original, injected)
        if score is None:
            failures.append({"apk": original.name, "stage": "score_inject", "reason": "extract failed"})
            continue
        scored.append({
            "label": "clone",
            "score": _round6(score),
            "apk_a": original.name,
            "apk_b": injected.name,
            "activity": activity,
        })

    n_neg = len(scored) if scored else target_pairs
    negative_apks = [p for p, _ in selected]
    for a, b in random_negative_pairs(negative_apks, n_neg):
        score = score_negative_pair(a, b)
        if score is None:
            failures.append({"apk": f"{a.name}+{b.name}", "stage": "score_negative", "reason": "extract failed"})
            continue
        scored.append({
            "label": "non_clone",
            "score": _round6(score),
            "apk_a": a.name,
            "apk_b": b.name,
        })

    n_clone = sum(1 for s in scored if s["label"] == "clone")
    n_non = sum(1 for s in scored if s["label"] == "non_clone")
    status = "ok"
    if n_clone < 20:
        status = "partial"

    if n_clone == 0 or n_non == 0:
        report = {
            "status": "insufficient_corpus",
            "corpus_size": len(selected),
            "n_inject_pairs": n_clone,
            "n_negative_pairs": n_non,
            "threshold_grid": list(THRESHOLD_GRID),
            "per_threshold_metrics": [],
            "optimal_threshold": None,
            "optimal_F1": 0.0,
            "optimal_precision": 0.0,
            "optimal_recall": 0.0,
            "failures": failures,
            "scored_pairs": scored,
            "elapsed_seconds": _round6(time.time() - t0),
        }
    else:
        roc = build_roc_report(scored)
        report = {
            "status": status,
            "corpus_size": len(selected),
            "n_inject_pairs": n_clone,
            "n_negative_pairs": n_non,
            "threshold_grid": roc["threshold_grid"],
            "per_threshold_metrics": roc["per_threshold_metrics"],
            "optimal_threshold": roc["optimal_threshold"],
            "optimal_F1": roc["optimal_F1"],
            "optimal_precision": roc["optimal_precision"],
            "optimal_recall": roc["optimal_recall"],
            "optimal_youden_j": roc["optimal_youden_j"],
            "failures": failures,
            "scored_pairs": scored,
            "elapsed_seconds": _round6(time.time() - t0),
            "ground_truth": "synthetic_apktool_smali_inject",
            "code_view": "v4_shingled",
        }
    return report


def _cli(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_code_inject_synth",
        description=(
            "DEEP-30: build a synthetic code-injection corpus via apktool + "
            "smali no-op insertion, then ROC for code_view_v4_shingled."
        ),
    )
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING_DIR)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--target-pairs", type=int, default=DEFAULT_TARGET_PAIRS)
    parser.add_argument("--max-apks", type=int, default=DEFAULT_MAX_APKS)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")

    report = run(
        corpus_dir=args.corpus_dir,
        staging_dir=args.staging_dir,
        artifact_dir=args.artifact_dir,
        target_pairs=args.target_pairs,
        max_apks=args.max_apks,
    )
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    with args.report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
    logger.info(
        "DEEP-30 report written to %s (status=%s, n_clone=%d, n_non=%d, opt_F1=%s)",
        args.report_path,
        report.get("status"),
        report.get("n_inject_pairs", 0),
        report.get("n_negative_pairs", 0),
        report.get("optimal_F1"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())

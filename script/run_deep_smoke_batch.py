#!/usr/bin/env python3
"""run_deep_smoke_batch.py — DEEP-002 smoke batch runner.

Loads a pairs JSON, runs deep pairwise verification for each pair using
pairwise_runner, and writes results to an output JSON file.

Usage (sequential, backward-compatible):
    python script/run_deep_smoke_batch.py \
        --pairs path/to/smoke_pairs.json \
        --config exp/configs/optimal-cascade-v4-pairwise-fix.yaml \
        --output path/to/results.json

Usage (parallel, 4 workers):
    python script/run_deep_smoke_batch.py \
        --pairs path/to/smoke_pairs.json \
        --config exp/configs/optimal-cascade-v4-pairwise-fix.yaml \
        --output path/to/results.json \
        --workers 4 \
        --pair-timeout 600
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.pairwise_runner import (
        ensure_enriched_items,
        load_config,
        parse_pairwise_stage,
        run_pairwise,
    )
except Exception:
    from pairwise_runner import (  # type: ignore[no-redef]
        ensure_enriched_items,
        load_config,
        parse_pairwise_stage,
        run_pairwise,
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_deep_smoke_batch.py",
        description=(
            "Run deep pairwise verification on a smoke batch of pairs. "
            "Reads pairs from --pairs JSON, applies cascade config from --config, "
            "writes results to --output JSON."
        ),
    )
    parser.add_argument(
        "--pairs",
        required=True,
        help="Path to smoke pairs JSON (array of enriched candidate objects).",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to cascade config YAML (must include stages.pairwise with metric+threshold).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output results JSON.",
    )
    parser.add_argument(
        "--ins-block-sim-threshold",
        type=float,
        default=0.80,
        help="Instruction block similarity threshold for GED metric (default: 0.80).",
    )
    parser.add_argument(
        "--ged-timeout-sec",
        type=int,
        default=30,
        help="GED per-pair timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of parallel worker processes (default: 1 = sequential). "
            "When >1, uses ProcessPoolExecutor. "
            "Recommended: set --ged-timeout-sec lower when using many workers."
        ),
    )
    parser.add_argument(
        "--pair-timeout",
        type=int,
        default=600,
        help="Per-pair wall-clock timeout in seconds for parallel mode (default: 600).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def load_pairs(pairs_path: Path) -> list[dict[str, Any]]:
    """Load and validate smoke pairs from JSON file."""
    raw = pairs_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    return ensure_enriched_items(payload)


def run_batch(
    pairs_path: Path,
    config_path: Path,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
) -> dict[str, Any]:
    """Run pairwise verification for all pairs sequentially.

    Returns a dict with keys:
      - config_ref: str
      - pairs_ref: str
      - pairwise_config: dict (features, metric, threshold)
      - total: int
      - results: list[dict]
    """
    config = load_config(config_path)
    selected_layers, metric, threshold = parse_pairwise_stage(config)

    # Write pairs to a temp file so run_pairwise can consume it via its API
    pairs = load_pairs(pairs_path)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(pairs, tmp)
        tmp_path = Path(tmp.name)

    try:
        results = run_pairwise(
            config_path=config_path,
            enriched_path=tmp_path,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "config_ref": str(config_path),
        "pairs_ref": str(pairs_path),
        "pairwise_config": {
            "features": list(selected_layers),
            "metric": metric,
            "threshold": threshold,
        },
        "total": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Parallel batch support
# ---------------------------------------------------------------------------

def _worker_process_single_pair(
    pair_json: str,
    config_path_str: str,
    ins_block_sim_threshold: float,
    ged_timeout_sec: int,
) -> str:
    """Top-level worker function for ProcessPoolExecutor (pickle-compatible).

    Processes a single pair JSON string and returns a result row JSON string.
    Runs in a separate process — all imports happen inside this function to
    ensure a clean process environment.
    """
    import json as _json
    import sys as _sys
    import tempfile as _tempfile
    from pathlib import Path as _Path

    _project_root = _Path(__file__).resolve().parent.parent
    if str(_project_root) not in _sys.path:
        _sys.path.insert(0, str(_project_root))

    try:
        from script.pairwise_runner import run_pairwise as _run_pairwise
    except Exception:
        from pairwise_runner import run_pairwise as _run_pairwise  # type: ignore[no-redef]

    pair = _json.loads(pair_json)
    config_path = _Path(config_path_str)

    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        _json.dump([pair], tmp)
        tmp_path = _Path(tmp.name)

    try:
        results = _run_pairwise(
            config_path=config_path,
            enriched_path=tmp_path,
            ins_block_sim_threshold=ins_block_sim_threshold,
            ged_timeout_sec=ged_timeout_sec,
        )
        row = results[0] if results else {
            "app_a": "unknown",
            "app_b": "unknown",
            "full_similarity_score": None,
            "library_reduced_score": None,
            "status": "analysis_failed",
            "views_used": [],
        }
    except Exception as exc:
        row = {
            "app_a": "unknown",
            "app_b": "unknown",
            "full_similarity_score": None,
            "library_reduced_score": None,
            "status": "analysis_failed",
            "error": str(exc),
            "views_used": [],
        }
    finally:
        tmp_path.unlink(missing_ok=True)

    return _json.dumps(row)


def _make_failed_row(pair: dict[str, Any], reason: str) -> dict[str, Any]:
    """Build a failed result row preserving app labels if resolvable."""
    app_a = "unknown_app_a"
    app_b = "unknown_app_b"
    try:
        from script.pairwise_runner import extract_apps, resolve_app_label
        app_a_raw, app_b_raw = extract_apps(pair)
        app_a = resolve_app_label(app_a_raw, "unknown_app_a")
        app_b = resolve_app_label(app_b_raw, "unknown_app_b")
    except Exception:
        pass
    return {
        "app_a": app_a,
        "app_b": app_b,
        "full_similarity_score": None,
        "library_reduced_score": None,
        "status": "analysis_failed",
        "error": reason,
        "views_used": [],
    }


def run_parallel_batch(
    pairs_path: Path,
    config_path: Path,
    workers: int,
    ins_block_sim_threshold: float = 0.80,
    ged_timeout_sec: int = 30,
    pair_timeout: int = 600,
) -> dict[str, Any]:
    """Run pairwise verification in parallel using ProcessPoolExecutor.

    Each worker processes one pair independently.
    Failed pairs get status='analysis_failed'; remaining pairs continue.

    Returns same contract as run_batch():
      - config_ref, pairs_ref, pairwise_config, total, results
    """
    config = load_config(config_path)
    selected_layers, metric, threshold = parse_pairwise_stage(config)

    pairs = load_pairs(pairs_path)
    total = len(pairs)

    config_path_str = str(config_path)

    # Map future -> (index, pair) to preserve order and allow failed row labeling
    future_to_meta: dict[Any, tuple[int, dict[str, Any]]] = {}
    results: list[dict[str, Any] | None] = [None] * total

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for idx, pair in enumerate(pairs):
            pair_json = json.dumps(pair)
            future = executor.submit(
                _worker_process_single_pair,
                pair_json,
                config_path_str,
                ins_block_sim_threshold,
                ged_timeout_sec,
            )
            future_to_meta[future] = (idx, pair)

        completed = 0
        for future in as_completed(future_to_meta):
            idx, pair = future_to_meta[future]
            completed += 1
            try:
                result_json = future.result(timeout=pair_timeout)
                row = json.loads(result_json)
            except FuturesTimeoutError:
                row = _make_failed_row(pair, "pair_timeout")
            except Exception as exc:
                row = _make_failed_row(pair, str(exc))

            results[idx] = row
            print(
                f"[{completed}/{total}] pair {idx} -> {row.get('status', 'unknown')}",
                file=sys.stderr,
            )

    # Safeguard: fill any None slots (should not happen in normal flow)
    for idx, row in enumerate(results):
        if row is None:
            results[idx] = _make_failed_row(pairs[idx], "unknown_worker_failure")

    return {
        "config_ref": str(config_path),
        "pairs_ref": str(pairs_path),
        "pairwise_config": {
            "features": list(selected_layers),
            "metric": metric,
            "threshold": threshold,
        },
        "total": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    pairs_path = Path(args.pairs)
    config_path = Path(args.config)
    output_path = Path(args.output)

    if not pairs_path.is_file():
        print(f"ERROR: pairs file not found: {pairs_path}", file=sys.stderr)
        sys.exit(1)
    if not config_path.is_file():
        print(f"ERROR: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    workers = args.workers
    if workers < 1:
        print("ERROR: --workers must be >= 1", file=sys.stderr)
        sys.exit(1)

    if workers == 1:
        batch_result = run_batch(
            pairs_path=pairs_path,
            config_path=config_path,
            ins_block_sim_threshold=args.ins_block_sim_threshold,
            ged_timeout_sec=args.ged_timeout_sec,
        )
    else:
        print(
            f"Parallel mode: {workers} workers, pair-timeout={args.pair_timeout}s",
            file=sys.stderr,
        )
        batch_result = run_parallel_batch(
            pairs_path=pairs_path,
            config_path=config_path,
            workers=workers,
            ins_block_sim_threshold=args.ins_block_sim_threshold,
            ged_timeout_sec=args.ged_timeout_sec,
            pair_timeout=args.pair_timeout,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(batch_result, indent=2), encoding="utf-8")
    print(
        f"Done: {batch_result['total']} pairs processed -> {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

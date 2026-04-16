#!/usr/bin/env python3
"""run_deep_smoke_batch.py — DEEP-002 smoke batch runner.

Loads a pairs JSON, runs deep pairwise verification for each pair using
pairwise_runner, and writes results to an output JSON file.

Usage:
    python script/run_deep_smoke_batch.py \
        --pairs path/to/smoke_pairs.json \
        --config exp/configs/optimal-cascade-v4-pairwise-fix.yaml \
        --output path/to/results.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
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
    """Run pairwise verification for all pairs.

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

    batch_result = run_batch(
        pairs_path=pairs_path,
        config_path=config_path,
        ins_block_sim_threshold=args.ins_block_sim_threshold,
        ged_timeout_sec=args.ged_timeout_sec,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(batch_result, indent=2), encoding="utf-8")
    print(
        f"Done: {batch_result['total']} pairs processed -> {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

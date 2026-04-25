#!/usr/bin/env python3
"""Generate a manual labelling form for hint-faithfulness self-consistency.

EXEC-HINT-21-MANUAL-KAPPA-MIN.

The script picks `n_pairs` pairs from a pairwise JSON run with a deterministic
random seed and writes a CSV form the expert fills in. The expert labels two
sessions of the same set of pairs with a gap of at least 7 days. The two
labellings are then compared via Cohen's kappa (`compute_hint_kappa.py`).

Form columns:
- pair_id: stable identifier copied from the pairwise row;
- full_similarity_score: deep similarity score from the pairwise run, helps
  the expert orient;
- hint: short text built from the row's evidence list (up to three top
  signals);
- useful: int [0, 1, 2] — to be filled by the expert (is the hint useful for
  understanding the verdict?);
- accurate: int [0, 1, 2] — to be filled by the expert (does the hint match
  the actual evidence in the pair?).

Score scale rationale: a 3-point ordinal scale (0=no, 1=partly, 2=yes) is the
minimum that allows Cohen's kappa to distinguish more than chance agreement
on a 10-pair sample without forcing the expert into a binary call.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import Optional


def _load_pairs(pairwise_json: Path) -> list[dict[str, Any]]:
    payload = json.loads(pairwise_json.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("pairs", "pairwise", "results", "candidates", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _pair_id(pair_row: Mapping[str, Any]) -> str:
    raw = pair_row.get("pair_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    app_a = str(pair_row.get("app_a") or "unknown_app_a").strip() or "unknown_app_a"
    app_b = str(pair_row.get("app_b") or "unknown_app_b").strip() or "unknown_app_b"
    return f"{app_a}__{app_b}"


def extract_hint_from_pair(pair_row: Mapping[str, Any], top_k: int = 3) -> str:
    """Build a short human-readable hint from `pair_row['evidence']`.

    Picks up to `top_k` evidence items with the largest absolute magnitude and
    formats them as ``signal_type:ref=magnitude``. Returns an empty string if
    the row carries no usable evidence — the expert form still receives a row
    so the layout stays consistent.
    """
    raw_evidence = pair_row.get("evidence")
    if not isinstance(raw_evidence, list):
        return ""

    items: list[tuple[str, str, float]] = []
    for entry in raw_evidence:
        if not isinstance(entry, dict):
            continue
        signal_type = entry.get("signal_type")
        ref = entry.get("ref")
        magnitude = entry.get("magnitude")
        if not isinstance(signal_type, str) or not signal_type.strip():
            continue
        if not isinstance(ref, str) or not ref.strip():
            continue
        try:
            value = float(magnitude)
        except (TypeError, ValueError):
            continue
        items.append((signal_type.strip(), ref.strip(), value))

    items.sort(key=lambda item: abs(item[2]), reverse=True)
    head = items[: max(0, int(top_k))]
    return "; ".join(f"{signal}:{ref}={value:.4f}" for signal, ref, value in head)


def run_hint_kappa_session(
    *,
    pairwise_json: Path,
    n_pairs: int,
    session_id: str,
    output_dir: Path,
    seed: int = 42,
) -> dict[str, Any]:
    """Pick `n_pairs` pairs deterministically and write a labelling CSV.

    Output layout::

        <output_dir>/<session_id>/labels.csv

    The CSV has columns ``pair_id``, ``full_similarity_score``, ``hint``,
    ``useful``, ``accurate`` (the last two start empty for the expert).
    """
    pair_rows = _load_pairs(pairwise_json)
    if len(pair_rows) < n_pairs:
        raise ValueError(
            f"pairwise_json contains {len(pair_rows)} pairs but {n_pairs} were requested"
        )

    rng = random.Random(seed)
    indices = list(range(len(pair_rows)))
    rng.shuffle(indices)
    selected = [pair_rows[index] for index in indices[:n_pairs]]
    # Sort by pair_id so the CSV order matches the deterministic seed without
    # depending on hash bucket order.
    selected.sort(key=_pair_id)

    session_dir = output_dir / str(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    labels_path = session_dir / "labels.csv"

    with labels_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "full_similarity_score", "hint", "useful", "accurate"])
        for pair in selected:
            score = pair.get("full_similarity_score")
            try:
                score_str = f"{float(score):.6f}"
            except (TypeError, ValueError):
                score_str = ""
            writer.writerow(
                [
                    _pair_id(pair),
                    score_str,
                    extract_hint_from_pair(pair),
                    "",
                    "",
                ]
            )

    return {
        "session_id": str(session_id),
        "n_pairs": n_pairs,
        "seed": seed,
        "labels_csv": labels_path,
        "pair_ids": [_pair_id(pair) for pair in selected],
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a manual labelling form (CSV) for hint-faithfulness "
            "self-consistency on N pairs from a pairwise run."
        )
    )
    parser.add_argument(
        "--pairwise-json",
        required=True,
        help="Path to a pairwise JSON artifact (list of pairs with evidence).",
    )
    parser.add_argument(
        "--n-pairs",
        type=int,
        default=10,
        help="Number of pairs to include in the labelling form (default: 10).",
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="Session label, e.g. 2026-04-25 for the first labelling session.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help=(
            "Output directory; the CSV is written under "
            "<output-dir>/<session-id>/labels.csv."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic seed for pair selection (default: 42).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    result = run_hint_kappa_session(
        pairwise_json=Path(args.pairwise_json),
        n_pairs=int(args.n_pairs),
        session_id=str(args.session_id),
        output_dir=Path(args.output_dir),
        seed=int(args.seed),
    )
    print(
        json.dumps(
            {
                "session_id": result["session_id"],
                "n_pairs": result["n_pairs"],
                "labels_csv": str(result["labels_csv"]),
                "pair_ids": result["pair_ids"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

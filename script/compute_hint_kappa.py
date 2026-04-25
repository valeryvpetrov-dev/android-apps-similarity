#!/usr/bin/env python3
"""Compute Cohen's kappa between two labelling sessions of the same expert.

EXEC-HINT-21-MANUAL-KAPPA-MIN.

The script ingests two CSV labellings of the same set of pairs and computes
Cohen's kappa for the `useful` and `accurate` axes separately. It returns a
JSON-friendly dict with both per-axis kappa values, the minimum (used as the
overall pass criterion), the target threshold (κ ≥ 0.70) and a boolean
``pass``.

The kappa implementation follows the standard formula from
[Cohen 1960, "A coefficient of agreement for nominal scales"]
(https://doi.org/10.1177/001316446002000104):

    κ = (p_o - p_e) / (1 - p_e),

where ``p_o`` is the observed agreement rate and ``p_e`` is the chance
agreement rate computed from the marginal label distributions of the two
sessions. We deliberately keep this dependency-free so the script does not
require ``sklearn`` (which is not in the project's requirements lock).

Pass rule: ``min(kappa_useful, kappa_accurate) >= target_kappa`` (default
0.70). The 0.70 threshold is the realistic fallback agreed for
EXEC-HINT-21-MANUAL-KAPPA-MIN — a single expert labelling twice with a 7-day
gap, 10 pairs. For inter-rater agreement the canonical critical-feedback
target stays at 0.80.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Optional


TARGET_KAPPA: float = 0.70


def _read_labels(csv_path: Path) -> dict[str, dict[str, int]]:
    """Read a labelling CSV. Returns mapping pair_id -> {useful, accurate}.

    Rows with an empty `useful` or `accurate` cell are skipped — the expert
    has not yet labelled them. We only compare pair_ids labelled in both
    sessions.
    """
    labels: dict[str, dict[str, int]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pair_id = (row.get("pair_id") or "").strip()
            if not pair_id:
                continue
            useful_raw = (row.get("useful") or "").strip()
            accurate_raw = (row.get("accurate") or "").strip()
            if useful_raw == "" or accurate_raw == "":
                continue
            try:
                useful = int(useful_raw)
                accurate = int(accurate_raw)
            except ValueError:
                continue
            labels[pair_id] = {"useful": useful, "accurate": accurate}
    return labels


def cohen_kappa(labels_a: Iterable[int], labels_b: Iterable[int]) -> float:
    """Compute Cohen's kappa from two equal-length label sequences.

    Implementation matches `sklearn.metrics.cohen_kappa_score` with default
    parameters (no weights, equal categories built from the union of labels).
    Returns 1.0 when both sequences are constant and equal (perfect trivial
    agreement) and 0.0 when ``p_e == 1`` and ``p_o < 1``.
    """
    seq_a = list(labels_a)
    seq_b = list(labels_b)
    if len(seq_a) != len(seq_b):
        raise ValueError("label sequences must have the same length")
    if not seq_a:
        return 0.0

    n = len(seq_a)
    categories = sorted(set(seq_a) | set(seq_b))

    counts_a = {category: 0 for category in categories}
    counts_b = {category: 0 for category in categories}
    agreement = 0
    for label_a, label_b in zip(seq_a, seq_b):
        counts_a[label_a] += 1
        counts_b[label_b] += 1
        if label_a == label_b:
            agreement += 1

    p_o = agreement / n
    p_e = sum(
        (counts_a[category] / n) * (counts_b[category] / n) for category in categories
    )
    if p_e == 1.0:
        # Both sessions agree on every pair AND every label is the same single
        # category -> p_o == 1, return 1.0; otherwise treat as 0 (degenerate).
        return 1.0 if p_o == 1.0 else 0.0
    return (p_o - p_e) / (1.0 - p_e)


def compute_hint_kappa(
    *,
    session_1_csv: Path,
    session_2_csv: Path,
    session_1_id: str,
    session_2_id: str,
    target_kappa: float = TARGET_KAPPA,
) -> dict[str, Any]:
    """Compare two CSV labellings and return a JSON-serialisable kappa report."""
    labels_1 = _read_labels(session_1_csv)
    labels_2 = _read_labels(session_2_csv)

    common_pair_ids = sorted(set(labels_1.keys()) & set(labels_2.keys()))

    useful_1 = [labels_1[pair_id]["useful"] for pair_id in common_pair_ids]
    useful_2 = [labels_2[pair_id]["useful"] for pair_id in common_pair_ids]
    accurate_1 = [labels_1[pair_id]["accurate"] for pair_id in common_pair_ids]
    accurate_2 = [labels_2[pair_id]["accurate"] for pair_id in common_pair_ids]

    kappa_useful = cohen_kappa(useful_1, useful_2) if common_pair_ids else 0.0
    kappa_accurate = cohen_kappa(accurate_1, accurate_2) if common_pair_ids else 0.0
    min_kappa = min(kappa_useful, kappa_accurate) if common_pair_ids else 0.0

    return {
        "session_1_id": str(session_1_id),
        "session_2_id": str(session_2_id),
        "session_1_csv": str(session_1_csv),
        "session_2_csv": str(session_2_csv),
        "n_pairs": len(common_pair_ids),
        "kappa_useful": round(kappa_useful, 6),
        "kappa_accurate": round(kappa_accurate, 6),
        "min_kappa": round(min_kappa, 6),
        "target_kappa": float(target_kappa),
        "pass": bool(common_pair_ids) and min_kappa >= target_kappa,
        "common_pair_ids": common_pair_ids,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute Cohen's kappa between two CSV labelling sessions of the "
            "same expert (self-consistency check for EXEC-HINT-21-MANUAL-KAPPA-MIN)."
        )
    )
    parser.add_argument("--session-1-csv", required=True, help="Path to session 1 labels CSV.")
    parser.add_argument("--session-2-csv", required=True, help="Path to session 2 labels CSV.")
    parser.add_argument("--session-1-id", required=True, help="Session 1 identifier (e.g. date).")
    parser.add_argument("--session-2-id", required=True, help="Session 2 identifier (e.g. date).")
    parser.add_argument(
        "--target-kappa",
        type=float,
        default=TARGET_KAPPA,
        help=f"Target kappa threshold (default: {TARGET_KAPPA}).",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write the JSON report; if omitted, prints to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    report = compute_hint_kappa(
        session_1_csv=Path(args.session_1_csv),
        session_2_csv=Path(args.session_2_csv),
        session_1_id=str(args.session_1_id),
        session_2_id=str(args.session_2_id),
        target_kappa=float(args.target_kappa),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

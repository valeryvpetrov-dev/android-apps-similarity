#!/usr/bin/env python3
"""Export scored pairs from screening JSON results to CSV.

Output CSV columns:
  pair_id                — "{app_a}__{app_b}"
  post_api_fix_score     — final_score alias (= retrieval_score from screening)
  label                  — human label if present, otherwise "unknown"

Usage:
  python export_scored_pairs.py --results-dir path/to/results --output pairs.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def load_screening_results(results_dir: Path) -> list[dict]:
    """Scan directory for JSON files and load all screening result entries."""
    json_files = sorted(results_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(
            "No JSON files found in results directory: {}".format(results_dir)
        )

    all_entries: list[dict] = []
    for json_path in json_files:
        raw = json_path.read_text(encoding="utf-8")
        payload = json.loads(raw)

        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            # Wrap single-dict files for uniform processing
            entries = [payload]
        else:
            raise ValueError(
                "Unexpected JSON structure in {}: must be list or object".format(json_path)
            )

        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(
                    "Each entry must be a JSON object in {}".format(json_path)
                )
        all_entries.extend(entries)

    return all_entries


def build_pair_id(entry: dict) -> str:
    """Build a canonical pair_id from app_a / app_b fields."""
    app_a = str(entry.get("app_a") or entry.get("query_app_id") or "").strip()
    app_b = str(entry.get("app_b") or entry.get("candidate_app_id") or "").strip()
    if not app_a or not app_b:
        raise ValueError(
            "Entry missing app_a/app_b identifiers: {}".format(
                json.dumps(entry, ensure_ascii=False)[:200]
            )
        )
    return "{}__{}".format(app_a, app_b)


def extract_score(entry: dict) -> float:
    """Extract final_score (post_api_fix_score) from entry.

    Prefers 'final_score', then 'retrieval_score' for backward compatibility
    with plain screening output (which does not yet have deepening applied).
    """
    for field in ("final_score", "post_api_fix_score", "retrieval_score"):
        raw = entry.get(field)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    "Field '{}' is not numeric: {!r}".format(field, raw)
                )
    raise ValueError(
        "Entry has no recognized score field (final_score / retrieval_score): {}".format(
            json.dumps(entry, ensure_ascii=False)[:200]
        )
    )


def extract_label(entry: dict) -> str:
    """Extract human label; return 'unknown' when absent or empty."""
    raw = entry.get("label")
    if raw is None:
        return "unknown"
    label = str(raw).strip()
    return label if label else "unknown"


def validate_entry(entry: dict) -> tuple[str, float, str]:
    """Validate and extract (pair_id, post_api_fix_score, label) from entry.

    Raises ValueError on invalid data.
    """
    pair_id = build_pair_id(entry)
    score = extract_score(entry)
    label = extract_label(entry)
    return pair_id, score, label


def export_to_csv(entries: list[dict], output_path: Path) -> dict:
    """Export entries to CSV and return summary statistics."""
    rows: list[tuple[str, float, str]] = []
    for entry in entries:
        pair_id, score, label = validate_entry(entry)
        rows.append((pair_id, score, label))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["pair_id", "post_api_fix_score", "label"])
        for pair_id, score, label in rows:
            writer.writerow([pair_id, score, label])

    scores = [score for _, score, _ in rows]
    labeled = sum(1 for _, _, label in rows if label != "unknown")

    return {
        "total_pairs": len(rows),
        "labeled": labeled,
        "unlabeled": len(rows) - labeled,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export scored pairs from screening JSON results to CSV. "
            "CSV columns: pair_id, post_api_fix_score, label."
        )
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Directory containing JSON files produced by screening_runner.py",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output CSV file path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_dir = Path(args.results_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not results_dir.is_dir():
        print("ERROR: results-dir does not exist or is not a directory: {}".format(results_dir), file=sys.stderr)
        sys.exit(1)

    entries = load_screening_results(results_dir)
    summary = export_to_csv(entries, output_path)

    print("Exported {} pairs to {}".format(summary["total_pairs"], output_path))
    print(
        "  labeled: {}  unlabeled: {}  score range: [{}, {}]".format(
            summary["labeled"],
            summary["unlabeled"],
            summary["score_min"],
            summary["score_max"],
        )
    )


if __name__ == "__main__":
    main()

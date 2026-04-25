"""Tests for hint kappa session generation and self-consistency Cohen's kappa.

EXEC-HINT-21-MANUAL-KAPPA-MIN: minimal manual self-consistency check on 10 pairs.
The expert labels twice (>=7 days apart). We expect Cohen's kappa >= 0.70 on
both `useful` and `accurate` axes for the experiment to count as a pass.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def _write_pairwise_fixture(tmp_path: Path, n_pairs: int = 12) -> Path:
    """Write a synthetic pairwise JSON with `n_pairs` items so tests stay
    independent from the real artifact file."""
    pairs = []
    for index in range(n_pairs):
        pairs.append(
            {
                "app_a": f"app_a_{index:02d}",
                "app_b": f"app_b_{index:02d}",
                "pair_id": f"PAIR-{index:03d}",
                "full_similarity_score": round(0.10 * index, 4),
                "evidence": [
                    {
                        "source_stage": "pairwise",
                        "signal_type": "layer_score",
                        "magnitude": round(0.10 * index, 4),
                        "ref": "code",
                    }
                ],
            }
        )
    pairwise_path = tmp_path / "pairwise.json"
    pairwise_path.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    return pairwise_path


def test_run_hint_kappa_session_creates_form_with_n_pairs_and_is_deterministic(tmp_path):
    """(a) `run_hint_kappa_session` creates a labelling form with N pairs and
    the same seed yields the same pair_id ordering."""
    from hint_kappa_session import run_hint_kappa_session

    pairwise_path = _write_pairwise_fixture(tmp_path, n_pairs=12)
    out_dir = tmp_path / "artifacts"

    result_first = run_hint_kappa_session(
        pairwise_json=pairwise_path,
        n_pairs=10,
        session_id="session-1",
        output_dir=out_dir,
        seed=42,
    )
    labels_path_first = result_first["labels_csv"]

    assert labels_path_first.exists()
    with labels_path_first.open("r", encoding="utf-8", newline="") as handle:
        rows_first = list(csv.DictReader(handle))
    assert len(rows_first) == 10

    expected_columns = {
        "pair_id",
        "full_similarity_score",
        "hint",
        "useful",
        "accurate",
    }
    assert expected_columns.issubset(set(rows_first[0].keys()))
    # Cells reserved for the expert must start empty.
    for row in rows_first:
        assert row["useful"] == ""
        assert row["accurate"] == ""

    # Same seed, different session_id -> same pair_id order.
    result_second = run_hint_kappa_session(
        pairwise_json=pairwise_path,
        n_pairs=10,
        session_id="session-2",
        output_dir=out_dir,
        seed=42,
    )
    with result_second["labels_csv"].open("r", encoding="utf-8", newline="") as handle:
        rows_second = list(csv.DictReader(handle))

    assert [row["pair_id"] for row in rows_first] == [row["pair_id"] for row in rows_second]


def test_compute_hint_kappa_returns_one_for_identical_labellings(tmp_path):
    """(b) Two identical CSV labellings -> kappa = 1.0, pass = True."""
    from compute_hint_kappa import compute_hint_kappa

    session_1 = tmp_path / "labels_s1.csv"
    session_2 = tmp_path / "labels_s2.csv"
    pairs = [
        ("PAIR-001", 1, 2),
        ("PAIR-002", 0, 1),
        ("PAIR-003", 2, 2),
        ("PAIR-004", 1, 0),
        ("PAIR-005", 0, 0),
        ("PAIR-006", 2, 1),
        ("PAIR-007", 1, 2),
        ("PAIR-008", 0, 1),
        ("PAIR-009", 2, 2),
        ("PAIR-010", 1, 1),
    ]

    def _write(path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["pair_id", "useful", "accurate"])
            for pair_id, useful, accurate in pairs:
                writer.writerow([pair_id, useful, accurate])

    _write(session_1)
    _write(session_2)

    report = compute_hint_kappa(
        session_1_csv=session_1,
        session_2_csv=session_2,
        session_1_id="2026-04-25",
        session_2_id="2026-05-02",
    )

    assert report["session_1_id"] == "2026-04-25"
    assert report["session_2_id"] == "2026-05-02"
    assert report["n_pairs"] == 10
    assert report["kappa_useful"] == pytest.approx(1.0)
    assert report["kappa_accurate"] == pytest.approx(1.0)
    assert report["min_kappa"] == pytest.approx(1.0)
    assert report["target_kappa"] == pytest.approx(0.70)
    assert report["pass"] is True


def test_compute_hint_kappa_fails_threshold_for_strongly_disagreeing_labellings(tmp_path):
    """(c) Labellings where one session puts 0 and the other puts 2 across all
    pairs -> kappa < 0.70, pass = False. The artifact (returned dict) records
    the failure so we can check it explicitly."""
    from compute_hint_kappa import compute_hint_kappa

    session_1 = tmp_path / "labels_s1.csv"
    session_2 = tmp_path / "labels_s2.csv"

    with session_1.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "useful", "accurate"])
        for index in range(10):
            writer.writerow([f"PAIR-{index:03d}", 0, 0])

    with session_2.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "useful", "accurate"])
        for index in range(10):
            writer.writerow([f"PAIR-{index:03d}", 2, 2])

    report = compute_hint_kappa(
        session_1_csv=session_1,
        session_2_csv=session_2,
        session_1_id="2026-04-25",
        session_2_id="2026-05-02",
    )

    assert report["n_pairs"] == 10
    # When one session marks all 0 and the other all 2 there is no overlap
    # in categories. Cohen's kappa for completely disjoint single-category
    # sessions equals 0.0 (both marginals collapse).
    assert report["kappa_useful"] < 0.70
    assert report["kappa_accurate"] < 0.70
    assert report["min_kappa"] < 0.70
    assert report["pass"] is False

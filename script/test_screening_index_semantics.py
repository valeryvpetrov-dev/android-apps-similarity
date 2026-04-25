#!/usr/bin/env python3
"""TDD for SCREENING-21-INDEX-SEMANTICS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from minhash_lsh import build_candidate_index
from screening_runner import (
    _build_candidate_pairs_via_lsh,
    build_candidate_list,
    build_screening_signature,
)


CONTRACT_PATHS = (
    Path("/Users/valeryvpetrov/phd/system/screening-contract-v1.md"),
    Path(__file__).resolve().parent.parent / "docs/phd-drafts/screening-contract-v1.md",
)
FALLBACK_WARNING = (
    "screening_signature missing in app_record; built on-the-fly from M_static layers"
)


def _serialize_index(index) -> tuple[list[str], tuple[tuple[tuple[bytes, tuple[str, ...]], ...], ...]]:
    buckets = []
    for band in index._buckets:
        band_items = []
        for band_key, keys in sorted(band.items(), key=lambda item: item[0]):
            band_items.append((band_key, tuple(keys)))
        buckets.append(tuple(band_items))
    return (list(index.keys), tuple(buckets))


def _build_record(
    app_id: str,
    *,
    code: set[str] | None = None,
    resource: set[str] | None = None,
    screening_signature: list[str] | None = None,
) -> dict:
    record = {
        "app_id": app_id,
        "layers": {
            "code": set(code or set()),
            "component": set(),
            "resource": set(resource or set()),
            "metadata": set(),
            "library": set(),
        },
    }
    if screening_signature is not None:
        record["screening_signature"] = list(screening_signature)
    return record


class TestScreeningIndexSemantics(unittest.TestCase):
    def test_lsh_uses_explicit_screening_signature_as_single_token_source(self) -> None:
        records = [
            _build_record(
                "APP-A",
                code={"code_only_a"},
                screening_signature=["sig:shared", "sig:stable"],
            ),
            _build_record(
                "APP-B",
                code={"code_only_b"},
                screening_signature=["sig:shared", "sig:stable"],
            ),
            _build_record(
                "APP-C",
                code={"code_only_a"},
                screening_signature=["sig:other"],
            ),
        ]

        candidate_pairs = _build_candidate_pairs_via_lsh(
            records,
            {
                "type": "minhash_lsh",
                "num_perm": 64,
                "bands": 32,
                "seed": 42,
                "features": ["code", "resource"],
            },
        )

        self.assertIn(("APP-A", "APP-B"), candidate_pairs)
        self.assertNotIn(("APP-A", "APP-C"), candidate_pairs)

    def test_repeated_builds_produce_identical_lsh_index_for_same_signature_tokens(self) -> None:
        corpus = {
            "APP-A": set(build_screening_signature(_build_record("APP-A", screening_signature=["sig:1"]))),
            "APP-B": set(build_screening_signature(_build_record("APP-B", screening_signature=["sig:1", "sig:2"]))),
            "APP-C": set(build_screening_signature(_build_record("APP-C", screening_signature=["sig:3"]))),
        }

        first_index = build_candidate_index(corpus, num_perm=64, bands=32, seed=42)
        second_index = build_candidate_index(corpus, num_perm=64, bands=32, seed=42)

        self.assertEqual(_serialize_index(first_index), _serialize_index(second_index))

    def test_missing_screening_signature_falls_back_and_surfaces_warning(self) -> None:
        query = _build_record("APP-A", code={"shared_code"}, resource={"shared_res"})
        candidate = _build_record(
            "APP-B",
            code={"other_code"},
            resource={"other_res"},
            screening_signature=["code:shared_code", "resource:shared_res"],
        )

        signature = build_screening_signature(query)
        self.assertEqual(signature, ["code:shared_code", "resource:shared_res"])
        self.assertEqual(query["screening_signature"], signature)
        self.assertIn(FALLBACK_WARNING, query["screening_warnings"])

        rows = build_candidate_list(
            app_records=[query, candidate],
            selected_layers=["code", "resource"],
            metric="jaccard",
            threshold=0.0,
            ins_block_sim_threshold=0.80,
            ged_timeout_sec=30,
            processes_count=1,
            threads_count=2,
            candidate_index_params={
                "type": "minhash_lsh",
                "num_perm": 64,
                "bands": 32,
                "seed": 42,
                "features": ["code", "resource"],
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertIn(FALLBACK_WARNING, rows[0]["screening_warnings"])

    def test_contract_references_build_screening_signature_as_index_source(self) -> None:
        contract_texts = [
            contract_path.read_text(encoding="utf-8")
            for contract_path in CONTRACT_PATHS
            if contract_path.exists()
        ]

        self.assertTrue(contract_texts)
        self.assertTrue(
            any(
                "build_screening_signature" in contract_text
                and "screening_signature" in contract_text
                for contract_text in contract_texts
            )
        )


if __name__ == "__main__":
    unittest.main()

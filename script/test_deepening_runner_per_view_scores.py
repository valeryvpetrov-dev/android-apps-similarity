#!/usr/bin/env python3
"""Tests for deepening_runner propagation of per_view_scores (EXEC-087.1).

Deepening must read the ``per_view_scores`` payload produced by screening and
surface it on every enriched candidate as ``prior_per_view_scores``, without
recomputing anything. When the field is absent the output stays backward
compatible (no prior field emitted).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import deepening_runner


CONFIG_YAML = """
stages:
  screening:
    features: [code]
  deepening:
    features: [code]
  pairwise:
    features: [code]
""".strip()


def _write_candidate(root: Path, candidate: dict) -> tuple[Path, Path]:
    config_path = root / "config.yaml"
    candidates_path = root / "candidates.json"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")
    candidates_path.write_text(json.dumps([candidate], ensure_ascii=False), encoding="utf-8")
    return config_path, candidates_path


class TestDeepeningPerViewScoresPropagation(unittest.TestCase):
    def test_reads_per_view_scores_from_candidate_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate = {
                "app_a": {"app_id": "A", "apk_path": str(root / "a.apk")},
                "app_b": {"app_id": "B", "apk_path": str(root / "b.apk")},
                "per_view_scores": {
                    "code": 0.82,
                    "component": 0.45,
                    "resource": 0.67,
                    "metadata": 0.30,
                    "library": 0.55,
                },
            }
            config_path, candidates_path = _write_candidate(root, candidate)

            with mock.patch.object(
                deepening_runner,
                "build_code_layer",
                side_effect=[(5, False), (7, False)],
            ):
                payload = deepening_runner.run_deepening(config_path, candidates_path)

        # No error bubbled up, enriched_candidates returned for the pair.
        self.assertEqual(len(payload["enriched_candidates"]), 1)

    def test_prior_per_view_scores_appears_in_enriched_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            per_view = {
                "code": 0.82,
                "component": 0.45,
                "resource": 0.67,
                "metadata": 0.30,
                "library": 0.55,
            }
            candidate = {
                "app_a": {"app_id": "A", "apk_path": str(root / "a.apk")},
                "app_b": {"app_id": "B", "apk_path": str(root / "b.apk")},
                "per_view_scores": per_view,
            }
            config_path, candidates_path = _write_candidate(root, candidate)

            with mock.patch.object(
                deepening_runner,
                "build_code_layer",
                side_effect=[(5, False), (7, False)],
            ):
                payload = deepening_runner.run_deepening(config_path, candidates_path)

        item = payload["enriched_candidates"][0]
        self.assertIn("prior_per_view_scores", item)
        self.assertEqual(item["prior_per_view_scores"], per_view)

    def test_backward_compat_without_per_view_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate = {
                "app_a": {"app_id": "A", "apk_path": str(root / "a.apk")},
                "app_b": {"app_id": "B", "apk_path": str(root / "b.apk")},
            }
            config_path, candidates_path = _write_candidate(root, candidate)

            with mock.patch.object(
                deepening_runner,
                "build_code_layer",
                side_effect=[(5, False), (7, False)],
            ):
                payload = deepening_runner.run_deepening(config_path, candidates_path)

        item = payload["enriched_candidates"][0]
        # Accept either "field fully absent" or explicit None/empty dict.
        value = item.get("prior_per_view_scores", None)
        self.assertIn(value, (None, {}))


if __name__ == "__main__":
    unittest.main()

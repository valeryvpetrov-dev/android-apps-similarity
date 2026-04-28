#!/usr/bin/env python3
"""DEEP-30-CODE-INJECT-CORPUS-FOLDS: TDD tests for run_code_inject_synth.

Goal: verify the synthetic code-injection corpus + ROC pipeline that
addresses the DEEP-29 finding (code layer received weight 0.05 in DEEP-27
because F-Droid v2 contains no real inject-pairs).

The tests cover three contracts:

  (a) For a synthetic (original, code_injected) pair, the shingled v4
      score must be > 0.7 — local smali no-op insertions are exactly the
      "small edit -> small distance" case shingling is built for.
  (b) For a random pair of unrelated APKs, the shingled v4 score must be
      < 0.3 — different apps share very few method ids.
  (c) After ROC sweep over the threshold grid, optimal_threshold > 0.5.

Run from project root or script/:

    python3 -m unittest script.test_code_inject_synth -v
    python3 -m pytest script/test_code_inject_synth.py -v

The CLI module ``run_code_inject_synth`` does not exist yet — the tests
must therefore FAIL on import / on real-pipeline calls until the feature
commit lands. That is the TDD gate.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_import_run_module():
    """Import run_code_inject_synth or skip cleanly when the CLI doesn't exist yet."""
    try:
        import script.run_code_inject_synth as run_module  # type: ignore
        return run_module
    except ImportError:
        try:
            import run_code_inject_synth as run_module  # type: ignore[no-redef]
            return run_module
        except ImportError:
            return None


# ---------------------------------------------------------------------------
# (a) synthetic inject-pair score > 0.7
# ---------------------------------------------------------------------------

class TestSyntheticInjectScoreHigh(unittest.TestCase):
    """A synthetic (original, code_injected) pair must have shingled-v4 score > 0.7.

    The pair is produced by the ``score_inject_pair`` helper in the CLI:
    take two opcode-sequence dicts (original method ids → opcodes, injected
    method ids → opcodes) and run them through compare_code_v4_shingled.
    """

    def test_local_noop_insert_score_above_threshold(self):
        run_module = _try_import_run_module()
        if run_module is None:
            self.fail(
                "run_code_inject_synth module not importable yet "
                "— TDD gate (a) is red as expected."
            )
        score_for_local_inject = getattr(run_module, "score_for_local_inject", None)
        if score_for_local_inject is None:
            self.fail(
                "run_code_inject_synth.score_for_local_inject() must exist."
            )
        # Helper builds a synthetic pair where method id "Lapp/Main;->onCreate"
        # gets a 4-opcode no-op block prepended; all other methods are identical.
        score = score_for_local_inject()
        self.assertGreater(
            score, 0.7,
            f"Expected synthetic inject score > 0.7, got {score:.4f}",
        )


# ---------------------------------------------------------------------------
# (b) random pair score < 0.3
# ---------------------------------------------------------------------------

class TestRandomPairScoreLow(unittest.TestCase):
    """An unrelated random pair must have shingled-v4 score < 0.3.

    The CLI exposes ``score_for_random_pair`` which builds two synthetic
    feature dicts whose method-id sets are disjoint. compare_code_v4_shingled
    divides matched-method similarity by max(|ids_a|, |ids_b|) — when no ids
    are common, score is exactly 0.0.
    """

    def test_disjoint_method_sets_score_below_threshold(self):
        run_module = _try_import_run_module()
        if run_module is None:
            self.fail(
                "run_code_inject_synth module not importable yet "
                "— TDD gate (b) is red as expected."
            )
        score_for_random_pair = getattr(run_module, "score_for_random_pair", None)
        if score_for_random_pair is None:
            self.fail(
                "run_code_inject_synth.score_for_random_pair() must exist."
            )
        score = score_for_random_pair()
        self.assertLess(
            score, 0.3,
            f"Expected random-pair score < 0.3, got {score:.4f}",
        )


# ---------------------------------------------------------------------------
# (c) ROC optimal_threshold > 0.5
# ---------------------------------------------------------------------------

class TestRocOptimalThreshold(unittest.TestCase):
    """build_roc_report() over a synthetic mixed list of pairs must return
    optimal_threshold > 0.5.

    We feed it a synthetic list of scored pairs (clones near 0.85, non-clones
    near 0.05) — it should produce a ROC sweep over the canonical threshold
    grid [0.1, 0.95, 0.05] and pick a high-F1 operating point above 0.5.
    """

    def test_optimal_threshold_above_half_on_synthetic_scores(self):
        run_module = _try_import_run_module()
        if run_module is None:
            self.fail(
                "run_code_inject_synth module not importable yet "
                "— TDD gate (c) is red as expected."
            )
        build_roc_report = getattr(run_module, "build_roc_report", None)
        if build_roc_report is None:
            self.fail(
                "run_code_inject_synth.build_roc_report() must exist."
            )
        # Synthetic well-separated scores: clones bunched near 0.85, non-clones
        # near 0.05. F1 should peak somewhere in (0.5, 0.9].
        scored_pairs = (
            [{"label": "clone", "score": 0.90} for _ in range(20)]
            + [{"label": "clone", "score": 0.78} for _ in range(5)]
            + [{"label": "non_clone", "score": 0.04} for _ in range(40)]
            + [{"label": "non_clone", "score": 0.18} for _ in range(5)]
        )
        report = build_roc_report(scored_pairs)
        self.assertIn("optimal_threshold", report)
        self.assertGreater(
            report["optimal_threshold"], 0.5,
            f"Expected optimal_threshold > 0.5, got {report['optimal_threshold']:.4f}",
        )
        # Also check the report shape matches the artefact contract.
        for key in (
            "threshold_grid",
            "per_threshold_metrics",
            "optimal_threshold",
            "optimal_F1",
            "optimal_precision",
            "optimal_recall",
        ):
            self.assertIn(key, report, f"missing key: {key}")


# ---------------------------------------------------------------------------
# Artefact contract (smoke)
# ---------------------------------------------------------------------------

class TestArtefactContract(unittest.TestCase):
    """If a previous run produced report.json, its top-level keys are stable."""

    def test_report_top_level_keys_when_present(self):
        report_path = (
            _PROJECT_ROOT
            / "experiments"
            / "artifacts"
            / "DEEP-30-CODE-INJECT"
            / "report.json"
        )
        if not report_path.exists():
            self.skipTest("report.json not generated yet — feature commit pending.")
        with report_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        for key in (
            "corpus_size",
            "n_inject_pairs",
            "n_negative_pairs",
            "threshold_grid",
            "per_threshold_metrics",
            "optimal_threshold",
            "optimal_F1",
            "optimal_precision",
            "optimal_recall",
        ):
            self.assertIn(key, payload, f"missing report key: {key}")


if __name__ == "__main__":
    unittest.main()

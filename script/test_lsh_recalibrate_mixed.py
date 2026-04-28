"""Тесты SCREENING-31-INDEX-RECALIBRATE-MIXED-CORPUS.

Замер recall_at_shortlist для каждого класса модификации (1/4/5/6) отдельно,
объединяя SCRN-30 (class 4 — package rename), DEEP-30 (class 5 — code inject),
HINT-30 (class 6 — R8 mock) и synthetic clones (class 1).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class LshRecalibrateMixedTest(unittest.TestCase):
    """SCRN-31: replay LSH recall on mixed multi-class corpus."""

    def _make_fixture(self, root: Path) -> dict:
        """Подготовить три mini-артефакта (SCRN-30, DEEP-30, HINT-30) на диске."""
        # SCRN-30 (class 4): 20 пар, recall=0.35.
        scrn = {
            "artifact_id": "SCREENING-30-PACKAGE-RENAME",
            "n_pairs": 20,
            "shortlist_size": 93,
            "recall": 0.35,
            "jaccard_per_pair": [
                {"pair_id": f"sr-{i}", "in_shortlist": i < 7, "jaccard": 0.5 if i < 7 else 0.05}
                for i in range(20)
            ],
        }
        _write(root / "SCREENING-30-PACKAGE-RENAME" / "report.json", scrn)
        # DEEP-30 (class 5): 35 inject пар, F1=1.0 → все в shortlist.
        deep = {
            "artifact_id": "DEEP-30-CODE-INJECT",
            "n_inject_pairs": 35,
            "optimal_F1": 1.0,
            "scored_pairs": [
                {
                    "label": "clone",
                    "score": 0.95 + (i * 1e-3),
                    "apk_a": f"a{i}.apk",
                    "apk_b": f"a{i}__inject.apk",
                }
                for i in range(35)
            ],
        }
        _write(root / "DEEP-30-CODE-INJECT" / "report.json", deep)
        # HINT-30 (class 6): 10 R8 mock-пар.
        hint = {
            "artifact_id": "EXEC-HINT-30-OBFUSCATION-DATASET",
            "n_pairs": 10,
            "mode": "mock",
            "pairs": [
                {
                    "pair_id": f"MOCK-R8-{i:03d}",
                    "full_similarity_score": 0.55 + 0.02 * i,
                }
                for i in range(10)
            ],
        }
        _write(root / "EXEC-HINT-30-OBFUSCATION-DATASET" / "r8_pairs.json", hint)
        return {
            "scrn30_path": root / "SCREENING-30-PACKAGE-RENAME" / "report.json",
            "deep30_path": root / "DEEP-30-CODE-INJECT" / "report.json",
            "hint30_path": root / "EXEC-HINT-30-OBFUSCATION-DATASET" / "r8_pairs.json",
        }

    def test_a_returns_per_class_recall_dict_with_at_least_5_keys(self):
        """Тест (a): recall_at_shortlist_per_class возвращает 5+ ключей."""
        from run_lsh_recalibrate_mixed import calibrate_mixed_corpus

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._make_fixture(Path(tmp))
            report = calibrate_mixed_corpus(
                scrn30_path=fixture["scrn30_path"],
                deep30_path=fixture["deep30_path"],
                hint30_path=fixture["hint30_path"],
            )
        self.assertIn("recall_at_shortlist_per_class", report)
        keys = list(report["recall_at_shortlist_per_class"].keys())
        self.assertGreaterEqual(
            len(keys),
            5,
            f"per-class recall должен иметь >=5 ключей, получили: {keys}",
        )

    def test_b_class5_code_injection_recall_high(self):
        """Тест (b): на DEEP-30 (class_5 code-injection) recall>=0.85 (F1=1.0)."""
        from run_lsh_recalibrate_mixed import calibrate_mixed_corpus

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._make_fixture(Path(tmp))
            report = calibrate_mixed_corpus(
                scrn30_path=fixture["scrn30_path"],
                deep30_path=fixture["deep30_path"],
                hint30_path=fixture["hint30_path"],
            )
        recall_c5 = report["recall_at_shortlist_per_class"].get("class_5")
        self.assertIsNotNone(recall_c5)
        self.assertGreaterEqual(
            recall_c5,
            0.85,
            f"class_5 (code injection) recall ожидаем >=0.85 при DEEP-30 F1=1.0, got {recall_c5}",
        )

    def test_c_class6_r8_recall_lower_than_class5(self):
        """Тест (c): class_6 (R8 obfuscation) recall < class_5 (R8 ломает minhash)."""
        from run_lsh_recalibrate_mixed import calibrate_mixed_corpus

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._make_fixture(Path(tmp))
            report = calibrate_mixed_corpus(
                scrn30_path=fixture["scrn30_path"],
                deep30_path=fixture["deep30_path"],
                hint30_path=fixture["hint30_path"],
            )
        recall_c5 = report["recall_at_shortlist_per_class"]["class_5"]
        recall_c6 = report["recall_at_shortlist_per_class"]["class_6"]
        self.assertLess(
            recall_c6,
            recall_c5,
            f"R8 obfuscation должен ломать minhash больше чем code-injection: "
            f"class_6 ({recall_c6}) < class_5 ({recall_c5})",
        )

    def test_d_artifact_structure(self):
        """Тест (d): артефакт содержит обязательные поля."""
        from run_lsh_recalibrate_mixed import calibrate_mixed_corpus

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._make_fixture(Path(tmp))
            report = calibrate_mixed_corpus(
                scrn30_path=fixture["scrn30_path"],
                deep30_path=fixture["deep30_path"],
                hint30_path=fixture["hint30_path"],
            )
        self.assertIn("n_pairs_per_class", report)
        self.assertIn("recall_at_shortlist_per_class", report)
        self.assertIn("current_thresh_002", report)
        self.assertIn("proposed_thresh_002", report)
        self.assertEqual(report["current_thresh_002"], 0.70)


if __name__ == "__main__":
    unittest.main()

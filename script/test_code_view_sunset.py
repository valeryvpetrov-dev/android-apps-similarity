#!/usr/bin/env python3
"""REPR-26-CODE-VIEW-SUNSET-PLAN: sunset contract for code views."""
from __future__ import annotations

import ast
import inspect
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import m_static_views
from m_static_views import _compare_code


CODE_VIEW_FILES = sorted(_SCRIPT_DIR.glob("code_view*.py"))
DEPRECATED_FILES = {"code_view_v2.py", "code_view_v3.py"}
CANONICAL_FILES = {"code_view_v4.py", "code_view_v4_shingled.py"}


def _module_docstring(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def _bundle(marker: str) -> dict:
    return {
        "method_fingerprints": {"Lcom/example/A;->foo()V": marker},
        "total_methods": 1,
        "mode": "v4",
    }


class TestCodeViewDocstringMarkers(unittest.TestCase):
    def test_every_existing_code_view_has_sunset_marker(self) -> None:
        self.assertGreaterEqual(len(CODE_VIEW_FILES), 4)
        for path in CODE_VIEW_FILES:
            with self.subTest(path=path.name):
                docstring = _module_docstring(path)
                self.assertRegex(docstring, r"@(deprecated|canonical)\b")

    def test_legacy_and_canonical_files_have_expected_marker(self) -> None:
        existing = {path.name for path in CODE_VIEW_FILES}
        self.assertTrue(DEPRECATED_FILES.issubset(existing))
        self.assertTrue(CANONICAL_FILES.issubset(existing))
        for filename in DEPRECATED_FILES:
            self.assertIn("@deprecated", _module_docstring(_SCRIPT_DIR / filename))
        for filename in CANONICAL_FILES:
            self.assertIn("@canonical", _module_docstring(_SCRIPT_DIR / filename))


class TestCompareCodeCanonicalOnly(unittest.TestCase):
    def test_compare_code_prefers_canonical_shingled_over_all_deprecated_inputs(self) -> None:
        calls: list[str] = []

        def fake_shingled(features_a: dict, features_b: dict) -> dict:
            calls.append("v4_shingled")
            self.assertEqual(features_a["mode"], "v4_shingled")
            self.assertEqual(features_b["mode"], "v4_shingled")
            return {"score": 0.42, "status": "canonical_v4_shingled"}

        def fake_v4(features_a: dict, features_b: dict) -> dict:
            calls.append("v4")
            return {"score": 0.13, "status": "canonical_v4"}

        with patch.object(m_static_views, "compare_code_v4_shingled", fake_shingled), \
             patch.object(m_static_views, "compare_code_v4", fake_v4), \
             warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = _compare_code(
                {"legacy:dex:a"},
                {"legacy:dex:b"},
                code_ged_score=0.99,
                code_v2_hash_a="legacy-v2-a",
                code_v2_hash_b="legacy-v2-b",
                code_v3_set_a=frozenset({("legacy", "v3", "a")}),
                code_v3_set_b=frozenset({("legacy", "v3", "b")}),
                code_v4_features_a={"method_fingerprints": {"x": "S:1"}, "mode": "v4"},
                code_v4_features_b={"method_fingerprints": {"x": "S:2"}, "mode": "v4"},
                code_v4_shingled_a={"method_fingerprints": {"x": "S:3"}, "mode": "v4_shingled"},
                code_v4_shingled_b={"method_fingerprints": {"x": "S:4"}, "mode": "v4_shingled"},
            )

        self.assertEqual(result["status"], "canonical_v4_shingled")
        self.assertEqual(result["score"], 0.42)
        self.assertEqual(calls, ["v4_shingled"])
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))

    def test_compare_code_source_has_no_v2_or_v3_comparator_imports(self) -> None:
        source = inspect.getsource(_compare_code)
        self.assertNotIn("compare_code_v2", source)
        self.assertNotIn("compare_code_v3", source)
        self.assertNotIn("code_view_v2", source)
        self.assertNotIn("code_view_v3", source)


class TestDeprecatedProductionFlow(unittest.TestCase):
    def test_legacy_code_inputs_emit_deprecation_warning(self) -> None:
        with self.assertWarns(DeprecationWarning):
            result = _compare_code({"classes.dex"}, {"classes2.dex"}, code_ged_score=None)
        self.assertIn(result["status"], {"canonical_unavailable", "v4_unavailable"})


class TestCodeViewSunsetPlanDraft(unittest.TestCase):
    def test_sunset_plan_draft_records_system_contract_target(self) -> None:
        path = _PROJECT_ROOT / "docs" / "phd-drafts" / "code-view-sunset-plan-v1.md"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("system/code-view-sunset-plan-v1.md", text)
        self.assertIn("@deprecated", text)
        self.assertIn("@canonical", text)
        self.assertIn("code_view_v4_shingled", text)


if __name__ == "__main__":
    unittest.main()

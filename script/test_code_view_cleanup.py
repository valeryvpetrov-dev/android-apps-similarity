#!/usr/bin/env python3
"""REPR-28 cleanup contract for removed legacy code views."""
from __future__ import annotations

import re
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LEGACY_CODE_VIEW_FILES = tuple(f"code_view{suffix}.py" for suffix in ("", "_v2", "_v3"))
LEGACY_CODE_VIEW_TESTS = tuple(
    f"test_code_view{suffix}.py" for suffix in ("_v1", "_v2", "_v3")
)


def test_legacy_code_view_files_are_physically_removed() -> None:
    for filename in LEGACY_CODE_VIEW_FILES:
        assert not (SCRIPT_DIR / filename).is_file()


def test_m_static_views_imports_only_canonical_code_views() -> None:
    imports = [
        line.strip()
        for line in (SCRIPT_DIR / "m_static_views.py").read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith(("import ", "from "))
    ]
    deprecated_import = re.compile(
        r"\b(?:from|import)\s+(?:script\.)?code_view(?:_v[23])?\b"
    )
    canonical = ("code_view_v4", "code_view_v4_shingled")

    assert any(name in line for line in imports for name in canonical)
    assert not any(deprecated_import.search(line) for line in imports)


def test_legacy_code_view_tests_are_removed_or_skipped() -> None:
    for filename in LEGACY_CODE_VIEW_TESTS:
        path = SCRIPT_DIR / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "@pytest.mark.skip" in text or "pytestmark = pytest.mark.skip" in text

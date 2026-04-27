# Draft: code-view-sunset-plan-v1

Target canonical contract: `system/code-view-sunset-plan-v1.md`.

Status after REPR-26-CODE-VIEW-SUNSET-PLAN:

- `code_view_v4_shingled.py` is `@canonical` and preferred for production code comparison.
- `code_view_v4.py` is `@canonical` and remains the fallback canonical method-level fuzzy representation.
- `code_view_v3.py` is `@deprecated`; allowed for historical experiments and regression tests only.
- `code_view_v2.py` is `@deprecated`; allowed for historical experiments and regression tests only.
- `code_view_v1` is the historical DEX-name Jaccard path inside the old `code` set layer; it is deprecated and must not be selected by production comparison.

Production rule:

`m_static_views._compare_code` must compare only canonical code features:

1. Use `code_view_v4_shingled` when present.
2. Use `code_view_v4` when shingled features are absent.
3. Ignore GED, v2 hashes, v3 opcode sets, and DEX-name sets whenever canonical features are present; callers that provide those legacy inputs receive `DeprecationWarning`.
4. If an old replay/unit-test fixture has no canonical features at all, allow only the deprecated DEX-name compatibility fallback and mark it with `DeprecationWarning`.

Sunset phases:

1. Phase 1, current: mark every existing `script/code_view*.py` module with `@deprecated` or `@canonical`.
2. Phase 2: keep deprecated modules importable for old experiment replay, but exclude them from production aggregation and new contracts.
3. Phase 3: after archived experiments are migrated or pinned, move deprecated variants out of the production script surface.

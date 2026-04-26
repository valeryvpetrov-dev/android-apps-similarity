# NOISE-24-MASK-CONTRACT discovery

- `script/noise_normalizer.py` still owns `detect_library_like()` as v1 prefix-match and switches to v2 only through `USE_LIBRARY_V2` / `build_payload(use_library_v2=True)`, so the same file path can be classified by different rules depending on env/process state.
- `script/library_view_v2.py` owns `detect_tpl_in_packages()` and `detect_library_like_v2()` as v2 package-coverage detection; `script/code_view_v2.py` calls this path directly in `app_only` library subtraction.
- `script/m_static_views.py::compare_all()` computes `library_reduced_score` by dropping the whole `library` layer, but it does not apply the same package mask to `code`, `component`, or `resource` features; that leaves room for both missed library code and later double exclusion.
- The required contract is one explicit, cascade-config-selectable library mask source: `get_library_mask(app_record) -> set[str]`, with no `USE_LIBRARY_V2` dependency. Callers may keep diagnostic library features, but exclusion/subtraction must remove only elements matched by this mask once.

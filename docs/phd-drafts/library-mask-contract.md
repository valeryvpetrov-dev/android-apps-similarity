# Library Mask Contract Draft

Scope: NOISE-24-MASK-CONTRACT.

`get_library_mask(app_record) -> set[str]` is the single source of truth for
packages treated as third-party library/TPL elements.

Rules:

- Algorithm selection is explicit in cascade-config:
  `cascade_config.library_mask.algorithm = prefix_v1 | jaccard_v2`.
- `USE_LIBRARY_V2` must not affect the contract. Environment state is not part
  of the mask key or the detector choice.
- `prefix_v1` marks packages that match the legacy known-library prefixes.
- `jaccard_v2` marks only packages from detected `library_view_v2` TPL hits,
  using the configured `threshold` and `min_matches`.
- `noise_normalizer`, `library_view_v2`, `code_view_v2`, `m_static_views`, and
  `library_reduced_score` consume the same package-prefix mask.
- The diagnostic `library` layer may remain visible, but exclusion/subtraction
  removes only elements matched by the unified mask and only once.

Operational contract:

- `noise_normalizer.detect_library_like(...)` classifies a path by checking its
  package against `get_library_mask(app_record)`.
- `library_view_v2.detect_tpl_in_packages(...)` uses the same v2 coverage rule
  exposed by the mask module.
- `code_view_v2` app-only subtraction uses `get_library_mask(...)` rather than
  a private TPL interpretation.
- `m_static_views.compare_all(...)` computes `library_reduced_score` by dropping
  the diagnostic `library` layer and masking code/component/resource set
  features with the unified package mask.

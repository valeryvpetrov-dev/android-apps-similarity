#!/usr/bin/env python3
"""Build the manifest of the similarity profile implemented at runtime."""
from __future__ import annotations

from typing import Any

try:
    from script import feature_extractors
    from script import m_static_views
    from script import pairwise_runner
except Exception:
    import feature_extractors  # type: ignore[no-redef]
    import m_static_views  # type: ignore[no-redef]
    import pairwise_runner  # type: ignore[no-redef]


def _zip_light_view_schema_versions() -> dict[str, str]:
    schema_prefix, schema_version = (
        feature_extractors.ZIP_LIGHT_EXTRACTOR_VERSION.rsplit("-", 1)
    )
    return {
        view: "{}-{}-{}".format(schema_prefix, view, schema_version)
        for view in feature_extractors.ZIP_LIGHT_SUPPORTED_VIEWS
    }


def build_runtime_profile_manifest() -> dict[str, Any]:
    """Return a JSON-serializable manifest assembled from runtime constants."""
    layers = list(m_static_views.ALL_LAYERS)
    return {
        "light_views": list(feature_extractors.ZIP_LIGHT_SUPPORTED_VIEWS),
        "light_view_schema_versions": _zip_light_view_schema_versions(),
        "active_measures": list(pairwise_runner.ACTIVE_SIMILARITY_MEASURES),
        "default_layers": layers,
        "available_layers": list(m_static_views.ALL_LAYERS),
        "public_pair_check_ids": sorted(pairwise_runner.PUBLIC_PAIR_CHECK_IDS),
        "conditional_pair_check_ids": sorted(
            pairwise_runner.CONDITIONAL_PAIR_CHECK_IDS
        ),
        "guarded_score_policy_ids": sorted(
            pairwise_runner.GUARDED_SCORE_POLICY_IDS
        ),
        "similarity_score_sources": sorted(
            pairwise_runner.SIMILARITY_SCORE_SOURCES
        ),
        "evidence_only_policy_ids": sorted(
            pairwise_runner.EVIDENCE_ONLY_POLICY_IDS
        ),
        "aggregation_policy": {
            "policy_id": pairwise_runner.DEEP_M2_SCORE_DECISION_POLICY_ID,
            "strategy": pairwise_runner.DEEP_M2_SCORE_DECISION_STRATEGY,
            "selected_score_field": (
                pairwise_runner.DEEP_M2_SELECTED_SCORE_FIELD
            ),
            "limitations": list(
                pairwise_runner.DEEP_M2_SCORE_DECISION_LIMITATIONS
            ),
        },
    }

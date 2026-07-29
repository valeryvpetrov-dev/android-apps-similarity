#!/usr/bin/env python3
"""Build the manifest of the similarity profile implemented at runtime."""
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import Any


def _load_runtime_modules(
    package_context: str | None,
    *,
    module_importer: Callable[[str], ModuleType] = import_module,
) -> tuple[ModuleType, ModuleType, ModuleType]:
    prefix = "{}.".format(package_context) if package_context else ""
    return (
        module_importer("{}feature_extractors".format(prefix)),
        module_importer("{}m_static_views".format(prefix)),
        module_importer("{}pairwise_runner".format(prefix)),
    )


feature_extractors, m_static_views, pairwise_runner = _load_runtime_modules(
    __package__
)


def build_runtime_profile_manifest() -> dict[str, Any]:
    """Return a JSON-serializable manifest assembled from runtime constants."""
    layers = list(m_static_views.ALL_LAYERS)
    return {
        "light_views": list(feature_extractors.ZIP_LIGHT_SUPPORTED_VIEWS),
        "light_view_schema_versions": dict(
            feature_extractors.ZIP_LIGHT_VIEW_SCHEMA_VERSIONS
        ),
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

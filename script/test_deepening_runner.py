#!/usr/bin/env python3
"""Tests for deepening_runner enhanced non-code integration."""

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


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestDeepeningRunnerEnhanced(unittest.TestCase):
    def test_run_deepening_enriches_pairwise_only_non_code_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            candidates_path = root / "candidates.json"

            write_text(
                config_path,
                """
stages:
  screening:
    features: [code]
  deepening:
    features: [code]
  pairwise:
    features: [code, component, resource, library]
""".strip(),
            )
            candidates_path.write_text(
                json.dumps(
                    [
                        {
                            "app_a": {"app_id": "A", "apk_path": str(root / "a.apk")},
                            "app_b": {"app_id": "B", "apk_path": str(root / "b.apk")},
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                deepening_runner,
                "resolve_or_materialize_decoded_dir",
                side_effect=["/tmp/decoded-a", "/tmp/decoded-b"],
            ) as decode_mock, mock.patch.object(
                deepening_runner,
                "load_enhanced_features",
                return_value={
                    "mode": "enhanced",
                    "component": {"activities": [], "services": [], "receivers": [], "providers": [], "permissions": set(), "features": set()},
                    "resource": {"resource_digests": set()},
                    "library": {"libraries": {}},
                    "code": set(),
                    "metadata": set(),
                },
            ), mock.patch.object(
                deepening_runner,
                "build_code_layer",
                side_effect=[(5, False), (7, False)],
            ):
                payload = deepening_runner.run_deepening(config_path, candidates_path)

        item = payload["enriched_candidates"][0]
        self.assertEqual(item["app_a"]["decoded_dir"], "/tmp/decoded-a")
        self.assertEqual(item["app_b"]["decoded_dir"], "/tmp/decoded-b")
        self.assertEqual(decode_mock.call_count, 2)

        statuses = {entry["view_id"]: entry["view_status"] for entry in item["enriched_views"]}
        self.assertEqual(statuses["component"], "success")
        self.assertEqual(statuses["resource"], "success")
        self.assertEqual(statuses["library"], "success")
        self.assertNotIn("not_implemented", json.dumps(item))

    def test_run_deepening_keeps_decoded_resource_enrichment_even_if_resource_is_in_screening(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            candidates_path = root / "candidates.json"

            write_text(
                config_path,
                """
stages:
  screening:
    features: [metadata, resource]
  deepening:
    features: [code, resource]
  pairwise:
    features: [code, resource]
""".strip(),
            )
            candidates_path.write_text(
                json.dumps(
                    [
                        {
                            "app_a": {"app_id": "A", "apk_path": str(root / "a.apk")},
                            "app_b": {"app_id": "B", "apk_path": str(root / "b.apk")},
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                deepening_runner,
                "resolve_or_materialize_decoded_dir",
                side_effect=["/tmp/decoded-a", "/tmp/decoded-b"],
            ), mock.patch.object(
                deepening_runner,
                "load_enhanced_features",
                return_value={
                    "mode": "enhanced",
                    "component": {"activities": [], "services": [], "receivers": [], "providers": [], "permissions": set(), "features": set()},
                    "resource": {"resource_digests": set()},
                    "library": {"libraries": {}},
                    "code": set(),
                    "metadata": set(),
                },
            ), mock.patch.object(
                deepening_runner,
                "build_code_layer",
                side_effect=[(5, False), (7, False)],
            ):
                payload = deepening_runner.run_deepening(config_path, candidates_path)

        statuses = {
            entry["view_id"]: entry["view_status"]
            for entry in payload["enriched_candidates"][0]["enriched_views"]
        }
        self.assertEqual(statuses["resource"], "success")


if __name__ == "__main__":
    unittest.main()

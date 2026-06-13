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


def touch_apk(path: Path) -> None:
    path.write_bytes(b"fake_apk")


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

    def test_build_decode_cache_dir_uses_shared_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_path = root / "app.alpha.apk"
            touch_apk(apk_path)

            with mock.patch.dict("os.environ", {"PHD_SHARED_DATA_ROOT": str(root / "shared")}):
                cache_dir = deepening_runner.build_decode_cache_dir(str(apk_path))

        self.assertIn(str((root / "shared").resolve()), str(cache_dir))
        self.assertIn("decoded-cache", str(cache_dir))
        self.assertIn("app.alpha", cache_dir.name)

    def test_materialize_decoded_dir_passes_output_option_before_apk_input(self) -> None:
        """apktool должен получать `-o <dir>` до позиционного APK-входа.

        Иначе часть версий apktool трактует `.partial` каталог как входной
        APK и падает с NoSuchFileException по decoded-cache/*.partial.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            apk_path = root / "app.apk"
            touch_apk(apk_path)
            captured: dict[str, list[str]] = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = list(cmd)
                out_dir = Path(cmd[cmd.index("-o") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "AndroidManifest.xml").write_text(
                    "<manifest />",
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.dict(
                "os.environ",
                {"PHD_SHARED_DATA_ROOT": str(root / "shared")},
            ), mock.patch.object(
                deepening_runner,
                "resolve_apktool_command",
                return_value=["apktool"],
            ), mock.patch.object(
                deepening_runner.subprocess,
                "run",
                side_effect=fake_run,
            ):
                decoded_dir = deepening_runner.materialize_decoded_dir(str(apk_path))

            cmd = captured["cmd"]
            self.assertEqual(cmd[:3], ["apktool", "d", "-f"])
            self.assertLess(cmd.index("-o"), cmd.index(str(apk_path.resolve())))
            self.assertEqual(cmd[-1], str(apk_path.resolve()))
            self.assertTrue(deepening_runner.looks_like_decoded_dir(Path(decoded_dir)))


if __name__ == "__main__":
    unittest.main()

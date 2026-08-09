#!/usr/bin/env python3
"""Regression tests for the standalone batch configuration runner."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import batch_config_runner


class TestBatchConfigRunnerInlineFallback(unittest.TestCase):
    def test_inline_fallback_excludes_rejected_active_tokens_without_screening_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = Path(tmpdir) / "renamed.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("classes.dex", b"dex\\n035\\0")
                archive.writestr("AndroidManifest.xml", b"<manifest />")

            with mock.patch.object(batch_config_runner, "SCREENING_RUNNER", None):
                layers = batch_config_runner.extract_layers_from_apk_inline(apk_path)

        for layer, prefixes in {
            "code": ("method_namespace:", "method_namespace_segment:"),
            "metadata": ("apk_name:",),
        }.items():
            for prefix in prefixes:
                with self.subTest(layer=layer, prefix=prefix):
                    self.assertFalse(
                        any(token.startswith(prefix) for token in layers[layer])
                    )


if __name__ == "__main__":
    unittest.main()

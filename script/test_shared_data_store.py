#!/usr/bin/env python3
"""Tests for shared_data_store.py."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import shared_data_store


class TestSharedDataStore(unittest.TestCase):
    def test_get_shared_data_root_prefers_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original = os.environ.get(shared_data_store.SHARED_DATA_ROOT_ENV)
            os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = tmpdir
            try:
                root = shared_data_store.get_shared_data_root()
            finally:
                if original is None:
                    os.environ.pop(shared_data_store.SHARED_DATA_ROOT_ENV, None)
                else:
                    os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = original

        self.assertEqual(root, Path(tmpdir).expanduser().resolve())

    def test_resolve_shared_ref_maps_to_shared_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original = os.environ.get(shared_data_store.SHARED_DATA_ROOT_ENV)
            os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = tmpdir
            try:
                resolved = shared_data_store.resolve_path_ref(
                    "shared://datasets/fdroid-corpus-v2-apks/sample.apk"
                )
            finally:
                if original is None:
                    os.environ.pop(shared_data_store.SHARED_DATA_ROOT_ENV, None)
                else:
                    os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = original

        expected = (
            Path(tmpdir).resolve() / "datasets" / "fdroid-corpus-v2-apks" / "sample.apk"
        )
        self.assertEqual(resolved, str(expected))

    def test_build_shared_apktool_cache_root_uses_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original = os.environ.get(shared_data_store.SHARED_DATA_ROOT_ENV)
            os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = tmpdir
            try:
                root = shared_data_store.shared_apktool_cache_root("apktool-2_9_3")
            finally:
                if original is None:
                    os.environ.pop(shared_data_store.SHARED_DATA_ROOT_ENV, None)
                else:
                    os.environ[shared_data_store.SHARED_DATA_ROOT_ENV] = original

        expected = Path(tmpdir).resolve() / "decoded-cache" / "apktool-2_9_3"
        self.assertEqual(root, expected)


if __name__ == "__main__":
    unittest.main()

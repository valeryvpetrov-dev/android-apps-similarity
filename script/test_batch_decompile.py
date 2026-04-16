#!/usr/bin/env python3
"""test_batch_decompile.py — unit tests for batch_decompile.py.

Uses mock subprocess to avoid actual apktool calls.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from script.batch_decompile import (
        collect_apk_paths,
        decoded_dir_for,
        decompile_apk,
        load_target_stems,
        parse_args,
        run_batch,
    )
except Exception:
    from batch_decompile import (  # type: ignore[no-redef]
        collect_apk_paths,
        decoded_dir_for,
        decompile_apk,
        load_target_stems,
        parse_args,
        run_batch,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_apk(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"PK")  # fake APK content
    return p


def _make_pairs_json(tmp_path: Path, pairs: list, filename: str = "pairs.json") -> Path:
    p = tmp_path / filename
    p.write_text(json.dumps(pairs), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_required_args(self, tmp_path):
        args = parse_args([
            "--apk-dir", str(tmp_path),
            "--output-dir", str(tmp_path / "out"),
        ])
        assert args.apk_dir == str(tmp_path)
        assert args.output_dir == str(tmp_path / "out")
        assert args.pairs is None
        assert args.apktool == "apktool"
        assert args.force is False

    def test_optional_args(self, tmp_path):
        pairs_path = tmp_path / "p.json"
        args = parse_args([
            "--apk-dir", str(tmp_path),
            "--output-dir", str(tmp_path / "out"),
            "--pairs", str(pairs_path),
            "--apktool", "/opt/homebrew/bin/apktool",
            "--force",
        ])
        assert args.pairs == str(pairs_path)
        assert args.apktool == "/opt/homebrew/bin/apktool"
        assert args.force is True


# ---------------------------------------------------------------------------
# collect_apk_paths
# ---------------------------------------------------------------------------

class TestCollectApkPaths:
    def test_finds_apks(self, tmp_path):
        _make_apk(tmp_path, "app_a.apk")
        _make_apk(tmp_path, "app_b.apk")
        (tmp_path / "readme.txt").write_text("x")
        result = collect_apk_paths(tmp_path)
        assert len(result) == 2
        stems = {p.stem for p in result}
        assert stems == {"app_a", "app_b"}

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_apk(sub, "app_c.apk")
        result = collect_apk_paths(tmp_path)
        assert len(result) == 1
        assert result[0].stem == "app_c"

    def test_empty_dir(self, tmp_path):
        result = collect_apk_paths(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# decoded_dir_for
# ---------------------------------------------------------------------------

class TestDecodedDirFor:
    def test_returns_correct_path(self, tmp_path):
        apk = tmp_path / "com.example.app.apk"
        output = tmp_path / "decoded"
        result = decoded_dir_for(apk, output)
        assert result == output / "com.example.app"

    def test_uses_stem(self, tmp_path):
        apk = Path("/some/path/my.app.apk")
        result = decoded_dir_for(apk, tmp_path)
        assert result == tmp_path / "my.app"


# ---------------------------------------------------------------------------
# load_target_stems
# ---------------------------------------------------------------------------

class TestLoadTargetStems:
    def test_list_of_pairs_with_nested_apps(self, tmp_path):
        pairs = [
            {
                "app_a": {"apk_path": "/data/apks/com.foo.apk"},
                "app_b": {"apk_path": "/data/apks/com.bar.apk"},
            },
        ]
        p = _make_pairs_json(tmp_path, pairs)
        stems = load_target_stems(p)
        assert stems == {"com.foo", "com.bar"}

    def test_list_with_direct_side_keys(self, tmp_path):
        pairs = [
            {"apk_1": "/data/apks/alpha.apk", "apk_2": "/data/apks/beta.apk"},
        ]
        p = _make_pairs_json(tmp_path, pairs)
        stems = load_target_stems(p)
        assert stems == {"alpha", "beta"}

    def test_dict_with_shortlist_key(self, tmp_path):
        data = {
            "shortlist": [
                {"app_a_apk_path": "/apks/app1.apk", "app_b_apk_path": "/apks/app2.apk"},
            ]
        }
        p = tmp_path / "sl.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        stems = load_target_stems(p)
        assert stems == {"app1", "app2"}

    def test_apps_nested_dict(self, tmp_path):
        pairs = [
            {
                "apps": {
                    "app_a": {"apk_path": "/data/x.apk"},
                    "app_b": {"apk_path": "/data/y.apk"},
                }
            }
        ]
        p = _make_pairs_json(tmp_path, pairs)
        stems = load_target_stems(p)
        assert stems == {"x", "y"}

    def test_empty_list(self, tmp_path):
        p = _make_pairs_json(tmp_path, [])
        stems = load_target_stems(p)
        assert stems == set()

    def test_no_apk_refs(self, tmp_path):
        pairs = [{"pair_id": "P-001", "score": 0.9}]
        p = _make_pairs_json(tmp_path, pairs)
        stems = load_target_stems(p)
        assert stems == set()

    def test_dedup_same_apk_multiple_pairs(self, tmp_path):
        pairs = [
            {"app_a": {"apk_path": "/apks/com.foo.apk"}, "app_b": {"apk_path": "/apks/com.bar.apk"}},
            {"app_a": {"apk_path": "/apks/com.foo.apk"}, "app_b": {"apk_path": "/apks/com.baz.apk"}},
        ]
        p = _make_pairs_json(tmp_path, pairs)
        stems = load_target_stems(p)
        assert stems == {"com.foo", "com.bar", "com.baz"}


# ---------------------------------------------------------------------------
# decompile_apk
# ---------------------------------------------------------------------------

class TestDecompileApk:
    def test_success(self, tmp_path):
        apk = _make_apk(tmp_path, "test.apk")
        dest = tmp_path / "test"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            success, msg = decompile_apk(apk, dest, "apktool")

        assert success is True
        assert msg == "ok"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["apktool", "d", "-f", "-o", str(dest), str(apk)]

    def test_failure_nonzero_exit(self, tmp_path):
        apk = _make_apk(tmp_path, "bad.apk")
        dest = tmp_path / "bad"

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "brut.androlib.AndrolibException: Could not decode"

        with patch("subprocess.run", return_value=mock_result):
            success, msg = decompile_apk(apk, dest, "apktool")

        assert success is False
        assert "exit 1" in msg
        assert "Could not decode" in msg

    def test_apktool_not_found(self, tmp_path):
        apk = _make_apk(tmp_path, "missing.apk")
        dest = tmp_path / "missing"

        with patch("subprocess.run", side_effect=FileNotFoundError):
            success, msg = decompile_apk(apk, dest, "/nonexistent/apktool")

        assert success is False
        assert "not found" in msg

    def test_timeout(self, tmp_path):
        apk = _make_apk(tmp_path, "slow.apk")
        dest = tmp_path / "slow"

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="apktool", timeout=300)):
            success, msg = decompile_apk(apk, dest, "apktool")

        assert success is False
        assert "timed out" in msg

    def test_uses_custom_apktool_path(self, tmp_path):
        apk = _make_apk(tmp_path, "app.apk")
        dest = tmp_path / "app"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            decompile_apk(apk, dest, "/opt/homebrew/bin/apktool")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/opt/homebrew/bin/apktool"


# ---------------------------------------------------------------------------
# run_batch
# ---------------------------------------------------------------------------

class TestRunBatch:
    def _mock_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        return mock_result

    def _mock_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        return mock_result

    def test_decodes_all_when_no_filter(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        _make_apk(apk_dir, "a.apk")
        _make_apk(apk_dir, "b.apk")

        with patch("subprocess.run", return_value=self._mock_success()):
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=None)

        assert summary["total"] == 2
        assert summary["decoded"] == 2
        assert summary["skipped"] == 0
        assert summary["failed"] == 0

    def test_skips_existing_decoded_dirs(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        _make_apk(apk_dir, "a.apk")
        # Pre-create decoded dir for 'a'
        (out_dir / "a").mkdir()

        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=None)

        assert summary["total"] == 1
        assert summary["decoded"] == 0
        assert summary["skipped"] == 1
        mock_run.assert_not_called()

    def test_force_decodes_existing(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        _make_apk(apk_dir, "a.apk")
        (out_dir / "a").mkdir()

        with patch("subprocess.run", return_value=self._mock_success()):
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=None, force=True)

        assert summary["decoded"] == 1
        assert summary["skipped"] == 0

    def test_filter_by_target_stems(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        _make_apk(apk_dir, "com.foo.apk")
        _make_apk(apk_dir, "com.bar.apk")
        _make_apk(apk_dir, "com.baz.apk")

        with patch("subprocess.run", return_value=self._mock_success()):
            summary = run_batch(
                apk_dir, out_dir, "apktool",
                target_stems={"com.foo", "com.bar"},
            )

        assert summary["total"] == 2
        assert summary["decoded"] == 2

    def test_failed_apk_does_not_block_rest(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        _make_apk(apk_dir, "good.apk")
        _make_apk(apk_dir, "bad.apk")

        responses = [self._mock_failure(), self._mock_success()]

        with patch("subprocess.run", side_effect=responses):
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=None)

        assert summary["total"] == 2
        assert summary["failed"] == 1
        assert summary["decoded"] == 1
        assert len(summary["errors"]) == 1

    def test_empty_apk_dir(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"

        with patch("subprocess.run"):
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=None)

        assert summary["total"] == 0
        assert summary["decoded"] == 0

    def test_creates_output_dir(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "does_not_exist" / "nested"

        run_batch(apk_dir, out_dir, "apktool", target_stems=None)
        assert out_dir.exists()

    def test_filter_empty_stems_decodes_nothing(self, tmp_path):
        apk_dir = tmp_path / "apks"
        apk_dir.mkdir()
        out_dir = tmp_path / "out"
        _make_apk(apk_dir, "com.foo.apk")

        with patch("subprocess.run") as mock_run:
            summary = run_batch(apk_dir, out_dir, "apktool", target_stems=set())

        assert summary["total"] == 0
        mock_run.assert_not_called()

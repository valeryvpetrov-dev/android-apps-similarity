from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest import mock

from script import run_noise_cache_recall as recall


def _make_corpus(root: Path, n_apks: int = 5) -> Path:
    corpus_dir = root / "apks"
    corpus_dir.mkdir()
    for index in range(n_apks):
        (corpus_dir / f"app_{index}.apk").write_bytes(f"apk-{index}".encode("utf-8"))
    return corpus_dir


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fake_apply_libloom_detection(*, apk_path: str, cache=None, **_kwargs) -> dict:
    apk_sha256 = _sha256_file(apk_path)
    if cache is not None:
        cached = cache.get(apk_sha256)
        if cached is not None:
            return cached

    time.sleep(0.002)
    envelope = {
        "schema_version": "nc-v1",
        "status": "success",
        "libloom_status": "ok",
        "libloom_libraries": [{"name": "synthetic"}],
    }
    if cache is not None:
        cache.put(apk_sha256, envelope)
    return envelope


def test_cli_writes_report_with_zero_first_pass_hits_and_full_second_pass_hits(
    tmp_path: Path,
) -> None:
    corpus_dir = _make_corpus(tmp_path)
    out_path = tmp_path / "report.json"

    with mock.patch.object(
        recall.noise_profile_envelope,
        "apply_libloom_detection",
        side_effect=_fake_apply_libloom_detection,
    ):
        exit_code = recall.main(
            [
                "--corpus_dir",
                str(corpus_dir),
                "--out",
                str(out_path),
                "--n_iterations",
                "2",
            ]
        )

    assert exit_code == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["corpus_size"] == 5
    assert report["n_iterations"] == 2
    assert report["pass_1"]["cache_hits"] == 0
    assert report["pass_2"]["cache_hits"] == 5
    assert report["cache_hit_ratio"] == 1.0


def test_speedup_factor_is_greater_than_one_on_synthetic_corpus(tmp_path: Path) -> None:
    corpus_dir = _make_corpus(tmp_path)

    with mock.patch.object(
        recall.noise_profile_envelope,
        "apply_libloom_detection",
        side_effect=_fake_apply_libloom_detection,
    ):
        report = recall.run_recall(
            corpus_dir=corpus_dir,
            output_path=tmp_path / "report.json",
            n_iterations=2,
        )

    assert report["speedup_factor"] > 1.0
    assert report["avg_first_pass_s"] == report["pass_1"]["avg_time_s"]
    assert report["avg_second_pass_s"] == report["pass_2"]["avg_time_s"]


def test_missing_corpus_falls_back_to_mini_corpus_with_warning(tmp_path: Path) -> None:
    missing_corpus = tmp_path / "missing-fdroid-v2"

    with mock.patch.object(
        recall.noise_profile_envelope,
        "apply_libloom_detection",
        side_effect=_fake_apply_libloom_detection,
    ):
        report = recall.run_recall(
            corpus_dir=missing_corpus,
            output_path=tmp_path / "report.json",
            n_iterations=2,
        )

    assert report["corpus_size"] == 5
    assert report["corpus_source"] == "mini_corpus"
    assert "fallback_mini_corpus_used" in report["warnings"]
    assert report["pass_2"]["cache_hits"] == 5

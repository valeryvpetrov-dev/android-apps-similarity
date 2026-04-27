"""Tests for REPR-27 wHash collision smoke runner."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from PIL import Image  # type: ignore
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    _PILLOW_AVAILABLE = False

from script import run_whash_collision_smoke as smoke


_PILLOW_REQUIRED = "Pillow не установлен; synthetic APK icon tests skipped"


def _make_icon_png(kind: str, size: int = 48) -> bytes:
    if not _PILLOW_AVAILABLE:
        raise RuntimeError(_PILLOW_REQUIRED)
    img = Image.new("L", (size, size))
    pixels: list[int] = []
    for y in range(size):
        for x in range(size):
            if kind == "diag":
                value = int(((x + y) / ((size - 1) * 2)) * 255)
            elif kind == "anti":
                value = int((((size - 1 - x) + y) / ((size - 1) * 2)) * 255)
            elif kind == "vertical":
                value = int((x / (size - 1)) * 255)
            elif kind == "checker":
                value = 255 if ((x // 6) + (y // 6)) % 2 else 0
            else:
                raise ValueError(kind)
            pixels.append(value)
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write_apk(corpus_dir: Path, apk_name: str, icon_png: bytes) -> Path:
    apk_path = corpus_dir / apk_name
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("res/mipmap-mdpi/ic_launcher.png", icon_png)
    return apk_path


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
class SyntheticCollisionSmokeTests(unittest.TestCase):
    def test_distinct_synthetic_icons_have_zero_collision_rate(self) -> None:
        """Three APKs with distinct package names and icons do not collide."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out = root / "out"
            corpus.mkdir()
            _write_apk(corpus, "com.example.one_1.apk", _make_icon_png("diag"))
            _write_apk(corpus, "com.example.two_1.apk", _make_icon_png("anti"))
            _write_apk(corpus, "com.example.three_1.apk", _make_icon_png("vertical"))

            report = smoke.run_collision_smoke(corpus, out)

            self.assertEqual(report["n_apks"], 3)
            self.assertEqual(report["n_pairs"], 3)
            self.assertEqual(report["n_collisions"], 0)
            self.assertEqual(report["collision_rate"], 0.0)
            self.assertGreater(report["mean_hamming"], 0.0)
            self.assertTrue((out / "report.json").is_file())
            self.assertTrue((out / "histogram.json").is_file())

    def test_identical_icons_with_different_packages_count_one_collision(self) -> None:
        """Two identical icons under different package names count as one collision."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out = root / "out"
            corpus.mkdir()
            icon = _make_icon_png("checker")
            _write_apk(corpus, "com.example.alpha_1.apk", icon)
            _write_apk(corpus, "com.example.beta_1.apk", icon)

            report = smoke.run_collision_smoke(corpus, out)

            self.assertEqual(report["n_apks"], 2)
            self.assertEqual(report["n_pairs"], 1)
            self.assertEqual(report["n_collisions"], 1)
            self.assertEqual(report["collision_rate"], 1.0)
            self.assertEqual(report["min_hamming"], 0)
            self.assertEqual(report["max_hamming"], 0)

    def test_missing_corpus_falls_back_to_mini_corpus(self) -> None:
        """Unavailable requested corpus is replaced by the repository mini-corpus."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requested = root / "missing"
            mini = root / "mini"
            out = root / "out"
            mini.mkdir()
            _write_apk(mini, "com.example.mini_1.apk", _make_icon_png("diag"))
            _write_apk(mini, "com.example.tiny_1.apk", _make_icon_png("anti"))

            report = smoke.run_collision_smoke(
                requested,
                out,
                fallback_corpus_dir=mini,
            )

            self.assertEqual(report["n_apks"], 2)
            self.assertTrue(report["source"]["used_fallback"])
            self.assertEqual(Path(report["source"]["requested_corpus_dir"]), requested)
            self.assertEqual(Path(report["source"]["effective_corpus_dir"]), mini)
            written = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(written["source"]["used_fallback"])


if __name__ == "__main__":
    unittest.main()

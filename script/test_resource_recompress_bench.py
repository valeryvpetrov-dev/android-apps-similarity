"""TDD tests for REPR-31 JPEG quality recompress benchmark."""
from __future__ import annotations

import importlib
import importlib.util
import unittest
from typing import List

try:
    from PIL import Image, ImageDraw  # type: ignore

    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    _PILLOW_AVAILABLE = False


_PILLOW_REQUIRED = "Pillow не установлен; JPEG recompress tests skipped"
_MODULE_NAME = "script.run_resource_recompress_bench"


def _load_bench():
    spec = importlib.util.find_spec(_MODULE_NAME)
    if spec is None:
        raise AssertionError("expected CLI module {}".format(_MODULE_NAME))
    return importlib.import_module(_MODULE_NAME)


def _synthetic_icon(size: int = 128):
    if not _PILLOW_AVAILABLE:
        raise RuntimeError(_PILLOW_REQUIRED)
    img = Image.new("RGBA", (size, size), (246, 247, 241, 255))
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            base = int((x * 0.52 + y * 0.48) / (size - 1) * 170) + 35
            ripple = 18 if ((x // 7) + (y // 9)) % 2 else -12
            pixels[x, y] = (
                max(0, min(255, base + ripple)),
                max(0, min(255, 230 - base // 2)),
                max(0, min(255, 80 + base // 3 + ripple)),
                255,
            )
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((18, 18, size - 18, size - 18), radius=18, outline=(25, 36, 72, 255), width=5)
    draw.ellipse((38, 28, 90, 80), fill=(226, 70, 67, 255))
    draw.rectangle((52, 66, 106, 92), fill=(38, 132, 98, 255))
    draw.line((20, 108, 108, 24), fill=(245, 217, 92, 255), width=7)
    return img


def _baseline_icons() -> List:
    if not _PILLOW_AVAILABLE:
        raise RuntimeError(_PILLOW_REQUIRED)
    icons = [_synthetic_icon()]
    for color in ((24, 40, 84), (238, 238, 232), (186, 40, 68), (35, 143, 108)):
        img = Image.new("RGBA", (128, 128), color + (255,))
        draw = ImageDraw.Draw(img)
        draw.polygon([(64, 10), (118, 64), (64, 118), (10, 64)], fill=tuple(reversed(color)) + (255,))
        draw.rectangle((28, 52, 100, 76), fill=(250, 210, 70, 255))
        icons.append(img)
    return icons


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
class ResourceRecompressBenchTests(unittest.TestCase):
    def test_q90_recompress_keeps_whash_hamming_at_most_three(self) -> None:
        bench = _load_bench()
        icon = _synthetic_icon()

        distance = bench.recompress_hamming_distance(icon, quality=90)

        self.assertLessEqual(distance, 3)

    def test_q30_recompress_has_larger_hamming_than_q90(self) -> None:
        bench = _load_bench()
        icon = _synthetic_icon()

        q30 = bench.recompress_hamming_distance(icon, quality=30)
        q90 = bench.recompress_hamming_distance(icon, quality=90)

        self.assertGreater(q30, q90)

    def test_unrelated_baseline_mean_exceeds_q90_mean(self) -> None:
        bench = _load_bench()

        summary = bench.summarize_recompress_distances(
            _baseline_icons(),
            baseline_pairs=8,
            random_seed=31,
        )

        self.assertGreater(
            summary["unrelated_pairs_baseline"]["mean_hamming"],
            summary["q90"]["mean_hamming"],
            summary,
        )


if __name__ == "__main__":
    unittest.main()

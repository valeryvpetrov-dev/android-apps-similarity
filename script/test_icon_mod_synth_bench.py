"""TDD tests for REPR-30 synthetic icon modification benchmark."""
from __future__ import annotations

import importlib
import importlib.util
import unittest
from typing import Iterable, List

try:
    from PIL import Image, ImageDraw  # type: ignore
    _PILLOW_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    _PILLOW_AVAILABLE = False


_PILLOW_REQUIRED = "Pillow не установлен; synthetic icon mod tests skipped"
_MODULE_NAME = "script.run_icon_mod_synth_bench"


def _load_bench():
    spec = importlib.util.find_spec(_MODULE_NAME)
    if spec is None:
        raise AssertionError("expected CLI module {}".format(_MODULE_NAME))
    return importlib.import_module(_MODULE_NAME)


def _gradient_icon(kind: str, size: int = 96):
    if not _PILLOW_AVAILABLE:
        raise RuntimeError(_PILLOW_REQUIRED)
    img = Image.new("RGB", (size, size))
    pixels = []
    for y in range(size):
        for x in range(size):
            if kind == "horizontal":
                value = int(x / (size - 1) * 255)
            elif kind == "horizontal_inverse":
                value = int((size - 1 - x) / (size - 1) * 255)
            elif kind == "vertical":
                value = int(y / (size - 1) * 255)
            elif kind == "diagonal":
                value = int((x + y) / ((size - 1) * 2) * 255)
            elif kind == "anti_diagonal":
                value = int(((size - 1 - x) + y) / ((size - 1) * 2) * 255)
            else:
                raise ValueError(kind)
            pixels.append((value, value, value))
    img.putdata(pixels)
    return img


def _shape_icon(kind: str, size: int = 96):
    if not _PILLOW_AVAILABLE:
        raise RuntimeError(_PILLOW_REQUIRED)
    img = Image.new("RGBA", (size, size), (18, 24, 35, 255))
    draw = ImageDraw.Draw(img)
    if kind == "circle":
        draw.ellipse((20, 20, size - 20, size - 20), fill=(232, 79, 65, 255))
        draw.rectangle((42, 10, 54, size - 10), fill=(245, 210, 92, 255))
    elif kind == "diamond":
        draw.polygon(
            [(size // 2, 10), (size - 10, size // 2), (size // 2, size - 10), (10, size // 2)],
            fill=(54, 168, 126, 255),
        )
        draw.rectangle((18, 44, size - 18, 52), fill=(247, 247, 247, 255))
    elif kind == "stripes":
        for index, x in enumerate(range(0, size, 12)):
            color = (66, 135, 245, 255) if index % 2 else (245, 245, 245, 255)
            draw.rectangle((x, 0, x + 8, size), fill=color)
    else:
        raise ValueError(kind)
    return img


def _synthetic_icons() -> List:
    return [
        _gradient_icon("horizontal"),
        _gradient_icon("horizontal_inverse"),
        _gradient_icon("vertical"),
        _gradient_icon("diagonal"),
        _gradient_icon("anti_diagonal"),
        _shape_icon("circle"),
        _shape_icon("diamond"),
        _shape_icon("stripes"),
    ]


def _mod_distances(bench, images: Iterable, mod_type: str) -> List[int]:
    distances: List[int] = []
    for image in images:
        original_hash = bench.compute_image_whash(image)
        modified_hash = bench.compute_image_whash(bench.make_icon_modifications(image)[mod_type])
        distances.append(bench.hamming_distance(original_hash, modified_hash))
    return distances


@unittest.skipIf(not _PILLOW_AVAILABLE, _PILLOW_REQUIRED)
class IconModSynthBenchTests(unittest.TestCase):
    def test_brightness_13_keeps_whash_hamming_low_for_majority(self) -> None:
        """Brightness +30% should keep most synthetic wHash distances <= 5."""
        bench = _load_bench()

        distances = _mod_distances(bench, _synthetic_icons(), "brightness")
        n_low = sum(distance <= 5 for distance in distances)

        self.assertGreaterEqual(n_low, 5, distances)

    def test_radically_different_synthetic_icons_have_large_hamming(self) -> None:
        """Opposite gradients should not look near-duplicate to wHash."""
        bench = _load_bench()

        left = bench.compute_image_whash(_gradient_icon("horizontal"))
        right = bench.compute_image_whash(_gradient_icon("horizontal_inverse"))

        self.assertGreater(bench.hamming_distance(left, right), 15)

    def test_unrelated_baseline_mean_exceeds_each_icon_mod_mean(self) -> None:
        """Random unrelated pairs should be farther apart than raw icon mods."""
        bench = _load_bench()

        summary = bench.summarize_icon_mod_distances(
            _synthetic_icons(),
            baseline_pairs=12,
            random_seed=7,
        )
        baseline_mean = summary["unrelated_pairs_baseline"]["mean_hamming"]

        for mod_type in bench.MOD_TYPES:
            self.assertGreater(
                baseline_mean,
                summary[mod_type]["mean_hamming"],
                "{}: {}".format(mod_type, summary),
            )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""LSH banding geometry helpers for screening diagnostics."""

from __future__ import annotations


def rows_per_band(num_perm: int, bands: int) -> int:
    """Return rows per LSH band for a valid ``num_perm``/``bands`` geometry."""
    num_perm_int = int(num_perm)
    bands_int = int(bands)
    if num_perm_int <= 0:
        raise ValueError("num_perm must be positive")
    if bands_int <= 0:
        raise ValueError("bands must be positive")
    if num_perm_int % bands_int != 0:
        raise ValueError(
            "bands ({}) must divide num_perm ({}) without remainder".format(
                bands_int,
                num_perm_int,
            )
        )
    return num_perm_int // bands_int


def expected_hit_probability(j: float, num_perm: int, bands: int) -> float:
    """Return MinHash LSH hit probability: ``1 - (1 - j^r)^b``."""
    j_float = float(j)
    if j_float < 0.0 or j_float > 1.0:
        raise ValueError("j must be in [0, 1]")
    rows = rows_per_band(num_perm, bands)
    return 1.0 - (1.0 - (j_float**rows)) ** int(bands)

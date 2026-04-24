#!/usr/bin/env python3
"""Set-overlap similarity helpers for asymmetric library matching."""

from __future__ import annotations

from typing import AbstractSet, Hashable


def tversky_index(
    A: AbstractSet[Hashable],
    B: AbstractSet[Hashable],
    alpha: float = 0.5,
    beta: float = 0.5,
) -> float:
    """Return the Tversky index for two sets."""
    set_a = set(A)
    set_b = set(B)
    if not set_a and not set_b:
        return 1.0

    shared = len(set_a & set_b)
    only_a = len(set_a - set_b)
    only_b = len(set_b - set_a)
    denominator = shared + alpha * only_a + beta * only_b
    if denominator <= 0.0:
        return 1.0 if set_a == set_b else 0.0
    score = shared / denominator
    return max(0.0, min(1.0, score))


def szymkiewicz_simpson_overlap(
    A: AbstractSet[Hashable],
    B: AbstractSet[Hashable],
) -> float:
    """Return the symmetric overlap coefficient for two sets."""
    set_a = set(A)
    set_b = set(B)
    if not set_a and not set_b:
        return 1.0

    denominator = min(len(set_a), len(set_b))
    if denominator == 0:
        return 0.0
    score = len(set_a & set_b) / denominator
    return max(0.0, min(1.0, score))

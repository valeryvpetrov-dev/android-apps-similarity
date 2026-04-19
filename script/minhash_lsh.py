#!/usr/bin/env python3
"""MinHash/LSH pure-Python implementation for EXEC-084.

Minimalistic stdlib-only (hashlib, struct) implementation used by
``screening_runner`` to build an approximate Jaccard candidate index
and skip the exact O(n^2) pairwise pass when the cascade-config
declares ``stages.screening.candidate_index.type = minhash_lsh``.

The public API is intentionally tiny:

* ``MinHashSignature`` — fixed-size vector of ``num_perm`` uint64
  permutation minima, updated feature-by-feature or built from a set.
* ``LSHIndex`` — band-based locality-sensitive index mapping keys
  (app_id) to signatures; ``query`` returns candidate keys that share
  at least one band bucket with the query.

Deterministic with a fixed ``seed``; identical seeds yield identical
signatures across runs and Python versions (``hashlib.blake2b`` +
``struct`` big-endian unpack).
"""
from __future__ import annotations

import hashlib
import struct
from typing import Iterable


_UINT64_MAX = (1 << 64) - 1


def _feature_hash(feature: str, slot: int, seed: int) -> int:
    """Deterministic per-slot hash over ``feature``.

    Uses ``blake2b`` with a 16-byte personalization derived from
    ``(seed, slot)``. ``digest_size=8`` gives exactly 8 bytes which we
    unpack as a big-endian unsigned 64-bit integer.
    """
    personal = struct.pack(">QQ", seed & _UINT64_MAX, slot & _UINT64_MAX)
    digest = hashlib.blake2b(
        feature.encode("utf-8"),
        digest_size=8,
        person=personal,
    ).digest()
    return struct.unpack(">Q", digest)[0]


class MinHashSignature:
    """Fixed-size MinHash signature of ``num_perm`` uint64 slots.

    Each slot ``i`` stores ``min(h_i(x))`` over all features ``x`` added
    via ``update``. Two signatures approximate the Jaccard similarity of
    their underlying sets as the fraction of matching slots.
    """

    __slots__ = ("num_perm", "seed", "slots")

    def __init__(self, num_perm: int = 128, seed: int = 42) -> None:
        if num_perm <= 0:
            raise ValueError("num_perm must be positive")
        if num_perm & (num_perm - 1) != 0 and num_perm % 1 != 0:
            # num_perm does not have to be a power of two; we only
            # enforce positivity. Bands divisibility is checked in LSH.
            pass
        self.num_perm = int(num_perm)
        self.seed = int(seed)
        self.slots: list[int] = [_UINT64_MAX] * self.num_perm

    def update(self, feature: str) -> None:
        """Fold ``feature`` into every permutation slot."""
        feature_str = str(feature)
        slots = self.slots
        seed = self.seed
        for slot_index in range(self.num_perm):
            hashed = _feature_hash(feature_str, slot_index, seed)
            if hashed < slots[slot_index]:
                slots[slot_index] = hashed

    @classmethod
    def from_features(
        cls,
        features: Iterable[str],
        num_perm: int = 128,
        seed: int = 42,
    ) -> "MinHashSignature":
        """Build a signature from an iterable of features."""
        signature = cls(num_perm=num_perm, seed=seed)
        for feature in features:
            signature.update(feature)
        return signature

    def jaccard(self, other: "MinHashSignature") -> float:
        """Approximate Jaccard as fraction of matching permutation slots."""
        if self.num_perm != other.num_perm:
            raise ValueError("MinHashSignature.num_perm mismatch")
        if self.seed != other.seed:
            raise ValueError("MinHashSignature.seed mismatch")
        if self.num_perm == 0:
            return 0.0
        matches = 0
        left = self.slots
        right = other.slots
        for index in range(self.num_perm):
            if left[index] == right[index]:
                matches += 1
        return matches / self.num_perm

    def band_keys(self, bands: int) -> list[bytes]:
        """Serialize the signature into ``bands`` deterministic band keys."""
        if bands <= 0:
            raise ValueError("bands must be positive")
        if self.num_perm % bands != 0:
            raise ValueError(
                "bands ({}) must divide num_perm ({}) without remainder".format(
                    bands, self.num_perm
                )
            )
        rows_per_band = self.num_perm // bands
        keys: list[bytes] = []
        for band_index in range(bands):
            start = band_index * rows_per_band
            end = start + rows_per_band
            packed = struct.pack(
                ">{}Q".format(rows_per_band), *self.slots[start:end]
            )
            digest = hashlib.blake2b(
                packed,
                digest_size=16,
                person=struct.pack(">QQ", band_index & _UINT64_MAX, rows_per_band & _UINT64_MAX),
            ).digest()
            keys.append(digest)
        return keys


class LSHIndex:
    """Band-based LSH index mapping signatures to keys.

    Two keys are returned as candidates for a query signature when at
    least one of their ``bands`` buckets collides with the query.
    ``rows_per_band = num_perm // bands``.
    """

    __slots__ = ("num_perm", "bands", "rows_per_band", "_buckets", "_key_order")

    def __init__(self, num_perm: int = 128, bands: int = 32) -> None:
        if num_perm <= 0:
            raise ValueError("num_perm must be positive")
        if bands <= 0:
            raise ValueError("bands must be positive")
        if num_perm % bands != 0:
            raise ValueError(
                "bands ({}) must divide num_perm ({}) without remainder".format(
                    bands, num_perm
                )
            )
        self.num_perm = int(num_perm)
        self.bands = int(bands)
        self.rows_per_band = self.num_perm // self.bands
        self._buckets: list[dict[bytes, list[str]]] = [dict() for _ in range(self.bands)]
        self._key_order: list[str] = []

    def __len__(self) -> int:
        return len(self._key_order)

    @property
    def keys(self) -> list[str]:
        return list(self._key_order)

    def add(self, key: str, signature: MinHashSignature) -> None:
        """Register ``signature`` under ``key`` in every band bucket.

        Raises ``ValueError`` if signature geometry does not match.
        """
        if signature.num_perm != self.num_perm:
            raise ValueError(
                "Signature num_perm ({}) does not match index num_perm ({})".format(
                    signature.num_perm, self.num_perm
                )
            )
        if key in self._key_order:
            raise ValueError("Duplicate LSH key: {!r}".format(key))
        self._key_order.append(key)
        band_keys = signature.band_keys(self.bands)
        for band_index, band_key in enumerate(band_keys):
            bucket = self._buckets[band_index].setdefault(band_key, [])
            bucket.append(key)

    def query(self, signature: MinHashSignature) -> set[str]:
        """Return the set of candidate keys that collide with ``signature``."""
        if signature.num_perm != self.num_perm:
            raise ValueError(
                "Signature num_perm ({}) does not match index num_perm ({})".format(
                    signature.num_perm, self.num_perm
                )
            )
        band_keys = signature.band_keys(self.bands)
        candidates: set[str] = set()
        for band_index, band_key in enumerate(band_keys):
            bucket = self._buckets[band_index].get(band_key)
            if bucket:
                candidates.update(bucket)
        return candidates


def build_candidate_index(
    corpus_features: dict[str, set[str]],
    num_perm: int = 128,
    bands: int = 32,
    seed: int = 42,
) -> LSHIndex:
    """Build an ``LSHIndex`` from a ``{app_id: feature_set}`` mapping.

    Each feature set is reduced to a ``MinHashSignature`` with the given
    ``num_perm`` and ``seed`` and inserted into a fresh ``LSHIndex``
    with ``bands`` bands. Duplicate ``app_id`` keys raise ``ValueError``.
    """
    index = LSHIndex(num_perm=num_perm, bands=bands)
    for app_id, features in corpus_features.items():
        signature = MinHashSignature.from_features(
            features, num_perm=num_perm, seed=seed
        )
        index.add(app_id, signature)
    return index


def query_candidate_index(
    index: LSHIndex,
    query_features: set[str],
    num_perm: int = 128,
    seed: int = 42,
) -> set[str]:
    """Build a query signature and return LSH candidate keys."""
    if num_perm != index.num_perm:
        raise ValueError(
            "num_perm ({}) does not match index num_perm ({})".format(
                num_perm, index.num_perm
            )
        )
    signature = MinHashSignature.from_features(
        query_features, num_perm=num_perm, seed=seed
    )
    return index.query(signature)

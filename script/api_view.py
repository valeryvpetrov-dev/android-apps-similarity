#!/usr/bin/env python3
"""
R_api layer: API call Markov chain similarity.
Approach inspired by MaMaDroid (NDSS 2017, ~600 citations).

Key idea: extract API call sequences per method, abstract to package family,
build transition probability matrix (Markov chain), compare via cosine similarity.
Invariant to programming language (Java vs Kotlin) — both call same Android APIs.
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional
import math

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency guards
# ---------------------------------------------------------------------------

try:
    from androguard.misc import AnalyzeAPK
    _ANDROGUARD_AVAILABLE = True
except ImportError:
    _ANDROGUARD_AVAILABLE = False
    logger.warning(
        "androguard is not installed. "
        "Install with: pip install androguard\n"
        "api_view will return fallback scores."
    )


# ── API abstraction ──────────────────────────────────────────────────────────

def _get_api_family(api_call: str) -> str:
    """
    Abstract API call to package family level.
    'android.telephony.TelephonyManager.getDeviceId()' -> 'android.telephony'
    'java.util.ArrayList' -> 'java.util'
    'Landroid/content/Intent;' -> 'android.content'  (Dalvik format)
    """
    # Нормализация Dalvik-формата: Landroid/content/Intent; → android.content.Intent
    if api_call.startswith('L') and api_call.endswith(';'):
        api_call = api_call[1:-1].replace('/', '.')
    # Берём всё до последней точки (убираем class name)
    parts = api_call.split('.')
    if len(parts) >= 2:
        return '.'.join(parts[:2])  # первые 2 компонента = family
    return api_call


# ── Feature extraction ────────────────────────────────────────────────────────

def _extract_api_sequences(apk_path: Path) -> list[list[str]]:
    """
    Extract per-method API call sequences (abstracted to family level).
    Returns list of lists, one inner list per method.
    Only external API calls (to Android/Java framework).
    """
    if not _ANDROGUARD_AVAILABLE:
        logger.error("androguard is not available; cannot extract API sequences")
        return []

    try:
        _, _, dx = AnalyzeAPK(str(apk_path))
    except Exception as exc:
        logger.error("AnalyzeAPK failed for %s: %s", apk_path, exc)
        return []

    method_api_seqs = []
    for method in dx.get_methods():
        if method.is_external():
            continue
        calls = []
        try:
            for _, call, _ in method.get_xref_to():
                if call.is_external():
                    # Только вызовы во внешние (framework) методы
                    class_name = call.get_method().get_class_name()
                    family = _get_api_family(class_name)
                    # Фильтруем только android.*, java.*, javax.*, kotlin.*
                    if family.startswith(('android', 'java', 'javax', 'kotlin')):
                        calls.append(family)
        except Exception:
            continue
        if calls:
            method_api_seqs.append(calls)
    return method_api_seqs


def build_markov_chain(apk_path: Path) -> Optional[dict]:
    """
    Build Markov chain transition matrix from API call sequences.
    Returns dict: {(from_family, to_family): probability} or None.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    sequences = _extract_api_sequences(apk_path)
    if not sequences:
        logger.warning("No API sequences found in %s", apk_path)
        return None

    # Считаем переходы
    transitions: dict[tuple[str, str], int] = defaultdict(int)
    from_counts: dict[str, int] = defaultdict(int)

    for seq in sequences:
        for i in range(len(seq) - 1):
            fr, to = seq[i], seq[i + 1]
            transitions[(fr, to)] += 1
            from_counts[fr] += 1

    if not transitions:
        return None

    # Нормализуем → вероятности
    matrix: dict[tuple[str, str], float] = {}
    for (fr, to), count in transitions.items():
        matrix[(fr, to)] = count / from_counts[fr]

    return matrix


def extract_api_bigram_vector(apk_path: Path) -> Optional[dict]:
    """
    Alternative: API call bigram frequency vector (simpler than full Markov chain).
    Returns dict: {(family_a, family_b): count} or None.
    Used as fallback if Markov chain is too sparse.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists() or not apk_path.is_file():
        logger.warning("APK path does not exist or is not a file: %s", apk_path)
        return None

    sequences = _extract_api_sequences(apk_path)
    if not sequences:
        return None
    bigrams: dict[tuple[str, str], int] = defaultdict(int)
    for seq in sequences:
        for i in range(len(seq) - 1):
            bigrams[(seq[i], seq[i + 1])] += 1
    return dict(bigrams) if bigrams else None


# ── Similarity ────────────────────────────────────────────────────────────────

def _cosine_similarity(vec_a: dict, vec_b: dict) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0
    keys = set(vec_a) | set(vec_b)
    dot = sum(vec_a.get(k, 0.0) * vec_b.get(k, 0.0) for k in keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compare_api(
    chain_a: Optional[dict],
    chain_b: Optional[dict],
) -> dict:
    """
    Compare two API Markov chains via cosine similarity.
    Returns: {"score": float [0..1], "status": str}
    """
    if chain_a is None and chain_b is None:
        return {"score": 1.0, "status": "both_empty"}
    if chain_a is None or chain_b is None:
        return {"score": 0.0, "status": "one_empty"}

    score = _cosine_similarity(chain_a, chain_b)
    return {"score": round(score, 6), "status": "markov_cosine"}


# ── CLI helper ───────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="api_view",
        description=(
            "R_api: compare two APKs using API call Markov chain cosine similarity "
            "(inspired by MaMaDroid, NDSS 2017)."
        ),
    )
    parser.add_argument("apk_a", help="Path to first APK")
    parser.add_argument("apk_b", help="Path to second APK")
    args = parser.parse_args()

    chain_a = build_markov_chain(Path(args.apk_a))
    chain_b = build_markov_chain(Path(args.apk_b))
    result = compare_api(chain_a, chain_b)
    result["transitions_a"] = len(chain_a) if chain_a else 0
    result["transitions_b"] = len(chain_b) if chain_b else 0
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()

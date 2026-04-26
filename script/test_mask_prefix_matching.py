#!/usr/bin/env python3
"""DEEP-25-MASK-PREFIX-MATCHING: package-prefix mask режим в library_reduced_score_canonical.

Контекст
--------
В волне 24 (DEEP-24-LIBRARY-REDUCED-UNIFY) каноническая формула
``library_reduced_score = |(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|`` принимала
mask в **token-level** namespacing (``{"library:okhttp3", ...}``).
В NOISE-24-MASK-CONTRACT функция ``library_mask.get_library_mask`` возвращает
**package-prefix** формат (``{"okhttp3", ...}``).

При интеграции возникла несовместимость: для матчинга prefix против
code-токенов (``okhttp3.Client``) нужно prefix-matching, а не token equality.
Это привело к skip-теста ``test_m_static_library_reduced_score_masks_only_unified_tpl_packages``
с reason указывающим на DEEP-25-MASK-PREFIX-MATCHING.

Этот модуль фиксирует TDD-семантику нового параметра ``mask_format``:

- ``"token_level"`` (default, обратная совместимость с DEEP-24);
- ``"package_prefix"`` (новый — для интеграции с NOISE-24);
- ``"auto"`` (различает по содержимому: если хотя бы один элемент содержит
  ``:`` — token-level, иначе package-prefix).

Семантика prefix-matching
-------------------------
Для каждого токена ``t`` в union/intersection вычисляется token-часть
(всё после ``:``, либо сам ``t`` если ``:`` нет). Если эта часть удовлетворяет
``find_matching_library_prefix(token_part, mask) is not None`` — токен
исключается. Это означает либо точное равенство пакетов, либо
``token_part.startswith(prefix + ".")`` (package-boundary, а не голый
``startswith``).

Дополнительно: при ``mask_format="package_prefix"`` слой ``library`` НЕ входит
в F_A/F_B canonical-формулы. Логика: NOISE-24 уже выдал mask явно из
содержимого APK (``packages``/``paths``), и library-слой как источник mask
больше не нужен. Это делает значение score сопоставимым с прежним
«non-library Жаккар» при том же mask, не зависящем от внутренней структуры
library-слоя.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _features(
    code=None, component=None, resource=None, library=None, metadata=None,
) -> dict:
    return {
        "mode": "quick",
        "code": set(code) if code is not None else set(),
        "component": set(component) if component is not None else set(),
        "resource": set(resource) if resource is not None else set(),
        "library": set(library) if library is not None else set(),
        "metadata": set(metadata) if metadata is not None else set(),
    }


# ---------------------------------------------------------------------------
# (a) mask_format="package_prefix" — прямая prefix-семантика.
# ---------------------------------------------------------------------------


def test_canonical_package_prefix_excludes_code_tokens_starting_with_prefix():
    """Mask=``{okhttp3}``, code-токен ``okhttp3.Client`` должен исчезнуть.

    Семантика: для каждого ``t`` в union/intersection берём token-часть
    (после ``:``) и проверяем prefix-match (``token == prefix`` или
    ``token.startswith(prefix + ".")``).
    """
    import m_static_views

    features_a = _features(
        code={"okhttp3.Client", "legacy.lib.Shared", "com.example.Left"},
        library={"okhttp3", "legacy.lib"},
    )
    features_b = _features(
        code={"okhttp3.Client", "legacy.lib.Shared", "com.example.Right"},
        library={"okhttp3", "legacy.lib"},
    )
    layers = ["code", "library"]

    score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3"},
        mask_format="package_prefix",
    )

    # При package_prefix library-слой исключается из F_A/F_B (NOISE дал mask
    # явно — library-слой как источник mask не нужен).
    # F_A_code = {okhttp3.Client, legacy.lib.Shared, com.example.Left}.
    # F_B_code = {okhttp3.Client, legacy.lib.Shared, com.example.Right}.
    # Mask {okhttp3} prefix-исключает code:okhttp3.Client.
    # Union after mask = {legacy.lib.Shared, com.example.Left, com.example.Right} = 3.
    # Intersection after mask = {legacy.lib.Shared} = 1.
    # Score = 1/3.
    assert score == pytest.approx(1.0 / 3.0)


def test_canonical_package_prefix_does_not_match_unrelated_packages():
    """Префикс ``okhttp3`` НЕ исключает ``okhttp3foo.X`` (нет ``.``-границы).

    Это ровно то, что отличает prefix-matching от голого ``startswith``:
    package-boundary через ``find_matching_library_prefix``.
    """
    import m_static_views

    features_a = _features(
        code={"okhttp3foo.X", "okhttp3.Client"},
    )
    features_b = _features(
        code={"okhttp3foo.X", "okhttp3.Client"},
    )
    layers = ["code"]

    score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3"},
        mask_format="package_prefix",
    )

    # okhttp3.Client исключается (boundary = okhttp3.).
    # okhttp3foo.X — НЕ исключается (нет точки между okhttp3 и foo).
    # F_A = F_B = {okhttp3foo.X} → ∩ = ∪ = 1 → 1.0.
    assert score == pytest.approx(1.0)


def test_canonical_package_prefix_handles_multiple_prefixes():
    """Несколько prefix-ов в маске — token исключается, если совпадает хотя бы с одним."""
    import m_static_views

    features_a = _features(
        code={
            "okhttp3.Client",
            "androidx.core.View",
            "com.example.Left",
            "kotlin.Pair",
        },
    )
    features_b = _features(
        code={
            "okhttp3.Client",
            "androidx.core.View",
            "com.example.Right",
            "kotlin.Pair",
        },
    )
    layers = ["code"]

    score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3", "androidx", "kotlin"},
        mask_format="package_prefix",
    )

    # Mask отрезает okhttp3.Client, androidx.core.View, kotlin.Pair.
    # F_A_after = {com.example.Left}.
    # F_B_after = {com.example.Right}.
    # ∩ = ∅, ∪ = 2.
    # Если ∪ != ∅ → score = 0/2 = 0.0.
    assert score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# (b) mask_format="token_level" — обратная совместимость с DEEP-24.
# ---------------------------------------------------------------------------


def test_canonical_token_level_default_keeps_old_set_difference():
    """Default ``mask_format`` остаётся ``token_level``: чистый set difference.

    Поведение DEEP-24 не должно сломаться: mask в namespacing
    ``{"library:foo"}`` — token equality, никакого prefix-matching.
    """
    import m_static_views

    features_a = _features(
        code={"x", "y", "z"},
        library={"androidx"},
    )
    features_b = _features(
        code={"x", "y", "w"},
        library={"androidx", "kotlin"},
    )
    layers = ["code", "library"]

    # Без указания mask_format — должен сработать default token_level.
    score_default = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"library:androidx", "library:kotlin"},
    )
    score_explicit = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"library:androidx", "library:kotlin"},
        mask_format="token_level",
    )

    # Контракт DEEP-24: F_A ∩ F_B \\ L и F_A ∪ F_B \\ L через set difference.
    # F_A = {code:x, code:y, code:z, library:androidx}.
    # F_B = {code:x, code:y, code:w, library:androidx, library:kotlin}.
    # L = {library:androidx, library:kotlin}.
    # ∩ \\ L = {code:x, code:y}, ∪ \\ L = {code:x, code:y, code:z, code:w} → 2/4.
    assert score_default == pytest.approx(0.5)
    assert score_explicit == pytest.approx(0.5)


def test_canonical_token_level_does_not_strip_layer_prefix_for_match():
    """Token-level НЕ интерпретирует mask как prefix против code-токенов.

    Если mask=``{"okhttp3"}`` (без namespacing) и mask_format=token_level —
    code-токены НЕ исключаются (нет точного равенства ``code:okhttp3.Client``
    с ``okhttp3``). Это ровно та проблема, которую DEEP-25 закрывает через
    package_prefix mode.
    """
    import m_static_views

    features_a = _features(
        code={"okhttp3.Client", "com.example.Left"},
    )
    features_b = _features(
        code={"okhttp3.Client", "com.example.Right"},
    )
    layers = ["code"]

    score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3"},
        mask_format="token_level",
    )

    # Никакого prefix-matching: F_A ∪ F_B = {code:okhttp3.Client,
    # code:com.example.Left, code:com.example.Right} = 3 (mask не пересекает
    # ни один токен). ∩ = {code:okhttp3.Client} = 1 → 1/3.
    assert score == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# (c) mask_format="auto" — различает по содержимому.
# ---------------------------------------------------------------------------


def test_canonical_auto_detects_token_level_when_colon_present():
    """В ``auto`` режиме — если хоть один элемент содержит ``:``, mask считается token-level."""
    import m_static_views

    features_a = _features(
        code={"okhttp3.Client", "com.example.Left"},
        library={"androidx"},
    )
    features_b = _features(
        code={"okhttp3.Client", "com.example.Right"},
        library={"androidx"},
    )
    layers = ["code", "library"]

    # Mask содержит элемент с ``:`` → token-level семантика.
    auto_score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"library:androidx"},
        mask_format="auto",
    )
    explicit_token_score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"library:androidx"},
        mask_format="token_level",
    )

    assert auto_score == pytest.approx(explicit_token_score)


def test_canonical_auto_detects_package_prefix_when_no_colon():
    """В ``auto`` режиме — если ни один элемент не содержит ``:``, mask считается package-prefix."""
    import m_static_views

    features_a = _features(
        code={"okhttp3.Client", "legacy.lib.Shared", "com.example.Left"},
        library={"okhttp3", "legacy.lib"},
    )
    features_b = _features(
        code={"okhttp3.Client", "legacy.lib.Shared", "com.example.Right"},
        library={"okhttp3", "legacy.lib"},
    )
    layers = ["code", "library"]

    auto_score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3"},
        mask_format="auto",
    )
    explicit_prefix_score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask={"okhttp3"},
        mask_format="package_prefix",
    )

    assert auto_score == pytest.approx(explicit_prefix_score)
    assert auto_score == pytest.approx(1.0 / 3.0)


def test_canonical_auto_with_empty_mask_is_token_level_neutral():
    """Пустой mask в ``auto`` режиме — нейтральный (как пустой token-level)."""
    import m_static_views

    features_a = _features(code={"a", "b"})
    features_b = _features(code={"a", "c"})
    layers = ["code"]

    auto_score = m_static_views.library_reduced_score_canonical(
        features_a, features_b, layers,
        library_mask=set(),
        mask_format="auto",
    )
    # ∩ = {a}, ∪ = {a, b, c} → 1/3.
    assert auto_score == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# Параметр mask_format существует и принимает три значения.
# ---------------------------------------------------------------------------


def test_canonical_signature_has_mask_format_parameter():
    """Сигнатура ``library_reduced_score_canonical`` содержит ``mask_format``."""
    import inspect

    import m_static_views

    sig = inspect.signature(m_static_views.library_reduced_score_canonical)
    assert "mask_format" in sig.parameters

    # Default — token_level (обратная совместимость с DEEP-24 callsites).
    default = sig.parameters["mask_format"].default
    assert default == "token_level"


def test_canonical_rejects_unknown_mask_format():
    """Неизвестное значение ``mask_format`` — явная ошибка, не молчаливый fallback."""
    import m_static_views

    with pytest.raises(ValueError):
        m_static_views.library_reduced_score_canonical(
            _features(code={"a"}), _features(code={"a"}),
            ["code"],
            library_mask={"a"},
            mask_format="unsupported_format",
        )

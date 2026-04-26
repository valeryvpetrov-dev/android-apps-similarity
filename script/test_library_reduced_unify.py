#!/usr/bin/env python3
"""DEEP-24-LIBRARY-REDUCED-UNIFY: единая каноническая формула library_reduced_score.

Контекст: критик DEEP волны 23 (`inbox/critics/deep-verification-2026-04-26.md`,
коммит 707b4bf, пункт 1) зафиксировал три разные формулы под одним именем
``library_reduced_score`` в трёх точках кода:

1. ``m_static_views.compare_all`` (строки 1047–1063) — weighted-average per-layer
   score-ов по всем слоям, кроме ``library``.
2. ``pairwise_runner.calculate_set_scores`` (строки 1109–1115) — set-метрика
   (Жаккар/косинус/...) на ``aggregate_features`` без library-слоя.
3. GED-путь ``result_contract.calculate_library_reduced_score`` (строка 1076)
   — ``sum(pair_sim) / max(non_lib_count_a, non_lib_count_b)``.

Контракт ``system/deep-verification-contract-v1.md`` раздел 4.4 определяет
единую каноническую формулу:

    library_reduced_score(A, B) = |(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|

где ``F_A``, ``F_B`` — полные множества признаков по всем активным слоям
``M_static``; ``L`` — единый library-mask (объединение TPL-меток с обеих
сторон).

Данный модуль — TDD-фиксация: тесты написаны до правки и фиксируют
расхождение трёх формул и единую целевую семантику.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import m_static_views


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _features(
    code: set[str] | None = None,
    component: set[str] | None = None,
    resource: set[str] | None = None,
    library: set[str] | None = None,
    metadata: set[str] | None = None,
) -> dict:
    """Quick feature bundle with optional layer sets."""
    return {
        "mode": "quick",
        "code": set(code) if code is not None else set(),
        "component": set(component) if component is not None else set(),
        "resource": set(resource) if resource is not None else set(),
        "library": set(library) if library is not None else set(),
        "metadata": set(metadata) if metadata is not None else set(),
    }


def _aggregate_with_layer_prefix(
    features: dict, selected_layers: list[str],
) -> set[str]:
    """Replicate pairwise_runner.aggregate_features convention `{layer}:{token}`."""
    out: set[str] = set()
    for layer in selected_layers:
        for token in features.get(layer, set()):
            out.add(f"{layer}:{token}")
    return out


def _canonical_jaccard(features_a: dict, features_b: dict, layers: list[str]) -> float:
    """Inline implementation of canonical formula for cross-checking.

    library_reduced_score = |(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|
    """
    f_a = _aggregate_with_layer_prefix(features_a, layers)
    f_b = _aggregate_with_layer_prefix(features_b, layers)
    library_a = features_a.get("library", set())
    library_b = features_b.get("library", set())
    library_mask = {f"library:{tok}" for tok in (library_a | library_b)}
    intersection = (f_a & f_b) - library_mask
    union = (f_a | f_b) - library_mask
    if not union:
        return 0.0
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# 1. Каноническая публичная функция существует и реализует формулу 4.4.
# ---------------------------------------------------------------------------

class TestCanonicalFunctionExists(unittest.TestCase):
    """Каноническая публичная функция доступна и проверяема."""

    def test_canonical_function_is_importable(self):
        """`library_reduced_score_canonical` существует в m_static_views."""
        self.assertTrue(
            hasattr(m_static_views, "library_reduced_score_canonical"),
            "m_static_views.library_reduced_score_canonical missing — "
            "DEEP-24 not yet implemented.",
        )

    def test_canonical_matches_jaccard_formula(self):
        """Формула: |(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|.

        Конкретный пример:
        - code: {x, y, z} vs {x, y, w}, library: {androidx} vs {androidx, kotlin}.
        - F_A = {code:x, code:y, code:z, library:androidx}.
        - F_B = {code:x, code:y, code:w, library:androidx, library:kotlin}.
        - L = {library:androidx, library:kotlin}.
        - F_A ∩ F_B = {code:x, code:y, library:androidx}.
        - (F_A ∩ F_B) \\ L = {code:x, code:y} → |·| = 2.
        - F_A ∪ F_B = {code:x, code:y, code:z, code:w, library:androidx, library:kotlin}.
        - (F_A ∪ F_B) \\ L = {code:x, code:y, code:z, code:w} → |·| = 4.
        - score = 2/4 = 0.5.
        """
        features_a = _features(code={"x", "y", "z"}, library={"androidx"})
        features_b = _features(code={"x", "y", "w"}, library={"androidx", "kotlin"})
        layers = ["code", "library"]
        score = m_static_views.library_reduced_score_canonical(
            features_a, features_b, layers,
        )
        self.assertAlmostEqual(score, 0.5, places=9)
        # Контроль через inline-replica.
        self.assertAlmostEqual(score, _canonical_jaccard(features_a, features_b, layers), places=9)

    def test_canonical_independent_of_metric_name(self):
        """Функция не принимает аргумент `metric` и не зависит от него.

        Контракт v1 раздел 4.4 пункт 1: оператор один и тот же независимо
        от значения `stages.pairwise.metric`.
        """
        import inspect
        sig = inspect.signature(m_static_views.library_reduced_score_canonical)
        self.assertNotIn(
            "metric", sig.parameters,
            "library_reduced_score_canonical должна быть инвариантна к metric: "
            "контракт v1 раздел 4.4 пункт 1",
        )


# ---------------------------------------------------------------------------
# 2. Идентичность значений в трёх точках вызова.
# ---------------------------------------------------------------------------

class TestThreeCallsitesAgree(unittest.TestCase):
    """Три callsite-а возвращают одно и то же значение для одной пары входов."""

    def _build_inputs(self):
        """Синтетическая пара с пересечением в коде и общей библиотекой."""
        features_a = _features(
            code={"alpha", "beta", "gamma"},
            component={"act-x", "act-y"},
            library={"androidx", "okhttp"},
        )
        features_b = _features(
            code={"alpha", "beta", "delta"},
            component={"act-x", "act-z"},
            library={"androidx"},
        )
        return features_a, features_b

    def test_compare_all_uses_canonical(self):
        """`compare_all` вызывает каноническую формулу, а не свой weighted-avg."""
        features_a, features_b = self._build_inputs()
        layers = ["code", "component", "library"]
        result = m_static_views.compare_all(
            features_a=features_a, features_b=features_b, layers=layers,
        )
        expected = m_static_views.library_reduced_score_canonical(
            features_a, features_b, layers,
        )
        self.assertAlmostEqual(
            result["library_reduced_score"], expected, places=9,
            msg=(
                "compare_all возвращает не каноническое значение: ожидается "
                f"{expected}, получено {result['library_reduced_score']}"
            ),
        )

    def test_pairwise_calculate_set_scores_uses_canonical(self):
        """`pairwise_runner.calculate_set_scores` тоже зовёт canonical.

        После правки — независимо от значения metric (jaccard|cosine|dice|...)
        возвращаемый library_reduced_score одинаков и совпадает с canonical.

        Тест мокирует ``load_layers_for_pairwise`` чтобы изолировать формулу
        от настоящих APK-файлов.
        """
        from unittest import mock
        import pairwise_runner

        features_a, features_b = self._build_inputs()
        layers = ["code", "component", "library"]

        layers_a = {
            layer: set(features_a.get(layer, set())) for layer in layers
        }
        layers_b = {
            layer: set(features_b.get(layer, set())) for layer in layers
        }

        def fake_load(apk_path, decoded_dir, selected_layers, layer_cache, feature_cache=None):
            return layers_a if apk_path.endswith("a.apk") else layers_b

        scores_per_metric = []
        with mock.patch.object(
            pairwise_runner, "load_layers_for_pairwise", side_effect=fake_load,
        ):
            for metric in ("jaccard", "cosine", "dice"):
                layer_cache: dict = {}
                _full, reduced = pairwise_runner.calculate_set_scores(
                    apk_a="path/to/a.apk", apk_b="path/to/b.apk",
                    decoded_a=None, decoded_b=None,
                    selected_layers=layers, metric=metric,
                    layer_cache=layer_cache,
                    feature_cache=None,
                )
                scores_per_metric.append(reduced)

        # Все значения попарно равны и совпадают с canonical.
        expected = m_static_views.library_reduced_score_canonical(
            features_a, features_b, layers,
        )
        for score in scores_per_metric:
            self.assertAlmostEqual(score, expected, places=9,
                msg=(
                    f"calculate_set_scores: ожидалось canonical={expected}, "
                    f"получено {score}. Контракт v1 раздел 4.4 пункт 1: "
                    "library_reduced_score инвариантен к metric."
                ),
            )
        # Попарное равенство — отдельная страховка.
        self.assertAlmostEqual(scores_per_metric[0], scores_per_metric[1], places=9)
        self.assertAlmostEqual(scores_per_metric[1], scores_per_metric[2], places=9)

    def test_ged_path_uses_canonical_via_adapter(self):
        """GED-путь (`result_contract.calculate_library_reduced_score`) считает
        каноническую формулу через адаптер.

        Адаптер строит F_A, F_B, L по ``dots_1``, ``dots_2`` (используя
        ``is_library_like_graph`` как single source of TPL-метки) и вызывает
        canonical. Таким образом GED-путь даёт то же значение, что и Жаккар по
        non-library частям множеств dot-имён.

        Семантика теста выбрана так, чтобы старая GED-формула
        ``sum(sim) / max(non_lib_count_a, non_lib_count_b)`` и каноническая
        Жаккар давали РАЗНЫЕ значения. Если они численно совпадут — тест
        не различает реализации. Используем перекос: app_a имеет 4 non-library
        dot-а, app_b — 2 non-library dot-а, общая пара одна (sim=1.0).
        """
        from calculate_apks_similarity import result_contract

        class _Dot:
            def __init__(self, name: str):
                self.name = name

        # extract_class_name() для "P/Class;->method" даёт "Class;->method"
        # без проверки префикса по KNOWN_LIBRARY_PREFIXES (выкидывается P/).
        # А для имён без `/` префикс распознаётся напрямую. Используем
        # имена в формате "package.Class.method" — это works для
        # is_library_like_dot напрямую.
        dots_a = [
            _Dot("com.app.Foo.m"),
            _Dot("com.app.Bar.m"),
            _Dot("com.app.Baz.m"),
            _Dot("com.app.Qux.m"),
            _Dot("androidx.Lib.m"),  # library — отбрасывается
        ]
        dots_b = [
            _Dot("com.app.Foo.m"),
            _Dot("com.app.Other.m"),
            _Dot("kotlin.Helper.m"),  # library — отбрасывается
        ]

        # Один матч — non-library пара (Foo↔Foo).
        pair_records = [
            {"first": "com.app.Foo.m", "second": "com.app.Foo.m",
             "first_i": 0, "second_i": 0, "similarity": 1.0},
        ]

        score = result_contract.calculate_library_reduced_score(
            pair_records, dots_a, dots_b,
        )

        # Старая формула: sum(sim)/max(non_lib_count_a, non_lib_count_b)
        # = 1.0 / max(4, 2) = 0.25.
        # Каноническая Жаккар:
        # F_A_non_lib = {Foo, Bar, Baz, Qux}, F_B_non_lib = {Foo, Other}.
        # ∩ = {Foo} = 1, ∪ = {Foo, Bar, Baz, Qux, Other} = 5 → 1/5 = 0.20.
        # После DEEP-24: возвращается 0.20, не 0.25.
        self.assertAlmostEqual(score, 1.0 / 5.0, places=9,
            msg=(
                "GED-путь должен давать каноническое Жаккар-значение "
                "(0.20), а не старое sum/max (0.25). Получено: "
                f"{score}"
            ),
        )


# ---------------------------------------------------------------------------
# 3. Both-empty семантика согласована с DEEP-20-BOTH-EMPTY-AUDIT.
# ---------------------------------------------------------------------------

class TestBothEmptySemantics(unittest.TestCase):
    """При обоих пустых non-library наборах canonical возвращает 0.0 + флаг.

    Контракт v1 раздел 4.4 пункт 3: «Если (F_A ∪ F_B) \\ L пусто — score = null,
    analysis_status = analysis_failed». В рамках текущей итерации мы возвращаем
    ``0.0`` со status='both_empty', флаг ``both_empty=True`` (выравниваем со
    всеми остальными per-layer контрактами по DEEP-20). Преобразование в
    `analysis_failed` остаётся ответственностью верхнеуровневого pipeline
    (`pairwise_runner._build_summary_row`). Здесь фиксируем ровно поведение
    функции: 0.0 + явный флаг.
    """

    def test_both_empty_layers_returns_zero_with_flag(self):
        """Обе стороны без признаков по всем слоям — возвращается dict-форма.

        Кроме скаляра (для обратной совместимости с numeric callsites)
        canonical обязана отдавать структуру ``{score, status, both_empty}``,
        как и все остальные per-layer сравнения после DEEP-20.
        """
        empty_a = _features()
        empty_b = _features()
        layers = ["code", "component", "library"]
        detail = m_static_views.library_reduced_score_canonical(
            empty_a, empty_b, layers, return_detail=True,
        )
        self.assertEqual(detail.get("score"), 0.0)
        self.assertEqual(detail.get("status"), "both_empty")
        self.assertIs(detail.get("both_empty"), True)

    def test_only_library_features_returns_zero_with_flag(self):
        """Если все признаки обеих сторон — library, non-library пусто."""
        features_a = _features(library={"androidx", "kotlin"})
        features_b = _features(library={"androidx", "kotlin"})
        layers = ["code", "library"]
        detail = m_static_views.library_reduced_score_canonical(
            features_a, features_b, layers, return_detail=True,
        )
        self.assertEqual(detail.get("score"), 0.0)
        self.assertEqual(detail.get("status"), "both_empty")
        self.assertIs(detail.get("both_empty"), True)


# ---------------------------------------------------------------------------
# 4. Callsites больше не реализуют формулу самостоятельно.
# ---------------------------------------------------------------------------

class TestCallsitesDelegateToCanonical(unittest.TestCase):
    """Старые формулы исчезли — компоненты делегируют canonical.

    Косвенный, но дешёвый источник: исходный код m_static_views.compare_all
    после правки не содержит локального ``reduced_sum``/``reduced_total``,
    а pairwise_runner.calculate_set_scores не вызывает ``calculate_set_metric``
    с ``reduced_left``/``reduced_right`` сам по себе.

    Это слабая структурная проверка, но она ловит регресс «кто-то вернул
    старую формулу обратно».
    """

    def test_compare_all_does_not_define_local_reduced_sum(self):
        import inspect
        source = inspect.getsource(m_static_views.compare_all)
        self.assertNotIn(
            "reduced_sum = 0.0", source,
            "compare_all снова считает library_reduced_score локально вместо "
            "вызова library_reduced_score_canonical (DEEP-24-LIBRARY-REDUCED-UNIFY).",
        )

    def test_pairwise_calculate_set_scores_does_not_inline_formula(self):
        import inspect
        import pairwise_runner
        source = inspect.getsource(pairwise_runner.calculate_set_scores)
        # Ловим оба возможных следа старой формулы:
        self.assertNotIn(
            'reduced_left = aggregate_features', source,
            "pairwise_runner.calculate_set_scores снова реализует library-reduced "
            "set-метрику локально вместо canonical.",
        )


if __name__ == "__main__":
    unittest.main()

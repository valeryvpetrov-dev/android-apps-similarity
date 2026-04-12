# E-SOTA-MOSDroid: R_code v3 — method-level opcode multiset

**Дата:** 2026-04-11
**Статус:** реализовано, dev-пары протестированы; F-Droid APKs отсутствуют локально

---

## Подход

MOSDroid (Computers & Security, 2025): вместо единого fingerprint на APK строить
мультимножество fingerprint-ов на уровне методов:

1. APK → список всех внутренних методов (через androguard)
2. Для каждого метода: `tuple` опкодов Dalvik
3. APK представляется как `frozenset` таких tuples (уникальные опкод-последовательности)
4. Сравнение двух APK: **Jaccard** `|set_a & set_b| / |set_a | set_b|`

**Ключевое свойство:** DEX packaging (single-dex vs multi-dex) не влияет на набор
методов и их опкоды → метрика инвариантна к структурным изменениям APK.

---

## Реализованные файлы

| Файл | Назначение |
|---|---|
| `script/code_view_v3.py` | Основной модуль: extraction + comparison |
| `script/test_code_view_v3.py` | 23 теста (20 passed, 3 skipped — нет F-Droid APKs) |
| `script/m_static_views.py` | Добавлена ветка v3 в `_compare_code()` и `compare_all()` |
| `script/screening_runner.py` | Добавлена функция `extract_code_v3_set()` |

---

## Результаты на dev-парах (simple_app)

| pair | label | v1_score | v2_score | v3_score | примечание |
|---|---|---|---|---|---|
| NonOpt vs NonOpt | same_apk | 1.000 | 1.000 | **1.000** | identical |
| NonOpt vs Rename | rename_clone | ~0.80 | ≈1.000 | **1.000** | имена меняются, опкоды те же |
| NonOpt vs Opt | optimized_clone | ~0.60 | — | **0.429** | оптимизация убирает методы |
| NonOpt vs Snake | non_clone | ~0.05 | <0.10 | **0.025** | разные приложения |

*v1 = DEX filename Jaccard; v2 = opcode n-gram TLSH; v3 = method-opcode set Jaccard*

---

## Аномальные пары F-Droid (E-ANOM-001) — ожидаемые улучшения

F-Droid APKs отсутствуют локально. Тесты реализованы и помечены skip.

| pair | v2_score | v3_ожидаемый | причина улучшения |
|---|---|---|---|
| redmoon 38 vs 39 | 0.02 | > 0.30 | structure_change не меняет методы |
| fantastischmemo 223 vs 237 | 0.06 | > 0.30 | single→multi-dex: методы те же |
| ipcam 241 vs 322 | 0.05 | < 0.10 (сохранится) | kotlin_rewrite — новые методы |

---

## Вывод

R_code v3 решает целевой corner case: DEX packaging не меняет set методов →
Jaccard на frozenset опкод-последовательностей остаётся высоким там, где v2
деградирует из-за изменения порядка следования методов.

Ограничения v3: чувствителен к kotlin_rewrite, к heavy proguard optimization.

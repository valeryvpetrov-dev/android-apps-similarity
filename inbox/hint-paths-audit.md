# EXEC-HINT-24-EVIDENCE-CONTRACT-AUDIT — discovery hint-путей

Команда HINT, волна 24. Задача: пройти по всем местам построения hint в
`script/`, разделить канонический путь Evidence → hint и legacy/shadow-пути,
зафиксировать инварианты для тестов и рефакторинга.

## Найденные точки построения hint

1. **Canonical (string)** — `script/evidence_formatter.py::format_hint_from_evidence(evidence_list)`.
   - Источник правды: `Evidence` (`source_stage`, `signal_type`, `magnitude`, `ref`).
   - Возвращает строку формата `code=0.81; component=0.42; apk_signature=1.00 (подпись)`.
   - Безопасный дефолт: пустая строка для `None`/не-list/только невалидных записей.
   - Помечен в коде комментарием `EXEC-HINT-20-EVIDENCE-CANON`.
   - Это **единственный публичный путь Evidence → hint-строка**.

2. **Canonical (list[dict])** — `script/pairwise_explainer.py::_hints_from_evidence(evidence_list)`.
   - Внутренний хелпер для `build_output_rows`, превращает Evidence в список
     hint-объектов `{type, signal, entity, score}` для JSON-вывода.
   - Чтит инвариант «факты в hint ⊆ факты в Evidence» по построению.
   - Не публичный API, deprecated не нужен.

3. **Legacy fallback** — `script/pairwise_explainer.py::build_explanation_hints(pair)` +
   8 функций `build_<type>_hint(pair)` (`build_library_impact_hint`,
   `build_new_method_call_hint`, `build_component_change_hint`,
   `build_resource_change_hint`, `build_permission_change_hint`,
   `build_native_lib_change_hint`, `build_certificate_mismatch_hint`,
   `build_code_removal_hint`).
   - Используется только как fallback в `build_output_rows`, когда `pair_row.evidence`
     отсутствует/пустой (старые pair_row до EXEC-088-WRITERS).
   - Уже логирует `WARNING "evidence empty, falling back to legacy hint construction"`.
   - **Зазор**: warning не помечен стабильным маркером `legacy_hint_path`,
     а pair_row не получает metadata `hint_metadata.source = 'legacy'/'canonical'`.
     Без маркера на больших прогонах не отличить «всё canonical» от «частично legacy».

4. **Legacy API-shim** — `script/pairwise_explainer.py::generate_hint(pair_row)` (волна 17).
   - Уже корректно делегирует в `evidence_formatter.collect_evidence_from_pairwise →
     format_hint_from_evidence` (см. EXEC-HINT-20-EVIDENCE-CANON).
   - Доктрингой помечен как deprecated.
   - **Не строит hint независимо**, инвариант сохраняется. Реальный shadow-path
     отсутствует.

5. **`screening_explainer.py`** — строит структурированные `signals` (метрики
   первичного отбора), а не human-readable hint. Слово `hint` в файле не
   встречается. Канонически отдельная сущность, на путь Evidence → hint не влияет.

## Реальные shadow-paths

В текущем коде **независимых от Evidence** путей построения hint нет:
- `format_hint_from_evidence` — единственный canonical;
- `_hints_from_evidence` — внутренний derive из той же Evidence;
- `build_explanation_hints` — legacy fallback, явно идёт по своей ветке только
  при отсутствии Evidence;
- `generate_hint` — делегирует в canonical.

Канон выдержан, но контракт «два режима, без third path» сейчас держится
**на ревью**, а не на тестах: нет теста, который запрещает добавление третьего
пути или независимого построения hint в обход Evidence.

## Что нужно ужесточить (план волны 24)

1. Добавить тест: единственный публичный путь — `format_hint_from_evidence`,
   `generate_hint` обязан делегировать в него (а не строить hint независимо).
2. Добавить тест: legacy fallback на пустом Evidence пишет в pair_row
   `hint_metadata = {"source": "legacy", "reason": "evidence_empty"}` и логирует
   стабильный маркер `legacy_hint_path` (по нему легко искать в логах больших прогонов).
3. Добавить тест: при canonical пути pair_row получает
   `hint_metadata = {"source": "canonical"}` — режим явно различим в выводе.
4. В `build_output_rows` — добавить запись `hint_metadata` и заменить текст
   warning на `"legacy_hint_path: evidence_empty pair_id=<id>"`.
5. В документации (`system/result-interpretation-contract-v1.md` раздел 6,
   draft в `docs/phd-drafts/result-interpretation-update.md`): зафиксировать,
   что hint-taxonomy P1 (типизация по 9 классам DeYoung) — будущий слой
   **поверх** Evidence (читает Evidence-записи и присваивает им класс),
   не вместо canonical-пути.

## Артефакт

- Discovery: `inbox/hint-paths-audit.md` (этот файл).
- Тесты: `script/test_hint_paths_audit.py` (новый).
- Реализация: правки `script/pairwise_explainer.py::build_output_rows`
  (warning marker + hint_metadata).
- Documentation draft: `docs/phd-drafts/result-interpretation-update.md`.

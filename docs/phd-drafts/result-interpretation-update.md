# Draft: result-interpretation-contract-v1 update (EXEC-HINT-24-EVIDENCE-CONTRACT-AUDIT)

Цель draft'а: обновить раздел 6 канонического документа
`system/result-interpretation-contract-v1.md` (phd repo) после волны 24,
команда HINT. Оркестратор перенесёт изменения в phd.

Источник: третья рекомендация критика HINT волны 23
(`inbox/critics/interpretation-2026-04-26.md`, коммит `8700145`):

> «пройти по всем местам построения hints и явно разделить два режима —
> канонический путь Evidence → hint и резервный путь для старых артефактов.
> В главе 3 либо понизить типизированную taxonomy до будущего слоя, либо
> показать, как она строится строго поверх Evidence.»

## Что добавить в раздел 6.2 (инварианты)

К существующим четырём инвариантам добавить пятый:

5. **Два режима построения hint явно различимы в выводе.** Каждая строка
   `build_output_rows` несёт `hint_metadata` со значением `source` равным
   `"canonical"` или `"legacy"`. В canonical-режиме hint-список построен из
   непустого `pair_row.evidence` через `_hints_from_evidence`. В legacy-режиме
   (старые артефакты до `EXEC-088-WRITERS`, у которых нет Evidence в pair_row)
   hint-список построен из `build_explanation_hints(pair)` и одновременно в
   логи пишется WARNING со стабильным маркером `legacy_hint_path: …
   pair_id=<id>`. Маркер позволяет считать долю legacy-pair_row в больших
   прогонах и затем выводить её из обращения.

   Свидетельство: тест
   `script/test_hint_paths_audit.py::test_legacy_fallback_emits_legacy_hint_path_warning_and_metadata`
   в подмодуле `android-apps-similarity` (ветка `wave24/hint-evidence-audit`).

## Что добавить в раздел 6.4 (что выбрано и что отложено)

Уточнить статус Варианта 2 (типизированные hints по 9 классам DeYoung
ACL 2020):

> Вариант 2 остаётся в `research-backlog.md` с приоритетом `P1`. После
> EXEC-HINT-24-EVIDENCE-CONTRACT-AUDIT его роль уточнена явно:
> **типизированная taxonomy — это будущий слой ПОВЕРХ `Evidence`, не
> вместо `format_hint_from_evidence`**. Будущий writer таксономии будет
> читать `Evidence` (поля `signal_type`, `ref`, `magnitude`) и присваивать
> ему один из 9 классов; canonical-путь Evidence → hint при этом не
> подменяется и не дублируется. Это сохраняет инвариант 1
> (факты в hint ⊆ факты в Evidence) и инвариант 5 (различимость режимов).

## Что добавить в раздел 6.5 (как это закрывает пробел)

После пункта 4 добавить пункт 5:

5. После EXEC-HINT-24-EVIDENCE-CONTRACT-AUDIT в коде нет shadow-paths:
   единственная публичная функция формирования hint —
   `evidence_formatter.format_hint_from_evidence`; `pairwise_explainer.generate_hint`
   делегирует через `collect_evidence_from_pairwise → format_hint_from_evidence`;
   legacy fallback `build_explanation_hints` явно помечен в выводе
   (`hint_metadata.source = "legacy"`) и в логах (`legacy_hint_path`).
   Отсутствие третьего пути зафиксировано тестом
   `script/test_hint_paths_audit.py::test_only_canonical_public_hint_function_is_format_hint_from_evidence`,
   который проверяет, что в `evidence_formatter` нет других публичных
   функций с именем `*format_hint*` или `*hint_from*`.

## Discovery-отчёт

`inbox/hint-paths-audit.md` (этот же подмодуль) фиксирует:

- canonical (string): `evidence_formatter.format_hint_from_evidence`;
- canonical (list[dict]): `pairwise_explainer._hints_from_evidence` (внутренний derive);
- legacy fallback: `pairwise_explainer.build_explanation_hints` + 8 функций
  `build_<type>_hint(pair)`;
- legacy API-shim: `pairwise_explainer.generate_hint` (делегирует);
- `screening_explainer.py` строит `signals`, не `hints`, на путь Evidence → hint
  не влияет.

Реальных независимых от Evidence путей построения hint в текущем коде нет.

# SCREENING-19-CONTRACT-ALIGN WIP

Статус: `blocked-by-sandbox-for-phd-write`
Дата: `2026-04-24`
Рабочая ветка submodule: `wave19/d-screening-contract`

## Что было найдено

### Реально используемые форматы candidate row до фикса

1. `screening_runner.py`
   Писал одновременно `query_app_id`/`candidate_app_id` и deprecated `app_a`/`app_b`.
   Дополнительно писал `app_a_apk_path`/`app_b_apk_path`, `retrieval_score`, `retrieval_rank`, `retrieval_features_used`, `features_used`, `screening_warnings`, `screening_explanation`, `per_view_scores`, `evidence`, `signature_match`, `shortcut_*`.

2. `screening_writer.py`
   Считал canonical-полями `query_app_id`/`candidate_app_id`, но всегда добавлял `app_a`/`app_b` и сам же поднимал `DeprecationWarning`.

3. `screening_reader.py`
   Читал canonical-формат, legacy-формат (`app_a`/`app_b`) и смешанный формат, не валидируя рассинхрон между canonical и legacy.

4. `run_screening.py`
   Декларировал canonical-ключи как primary source, но пропускал через reader результат runner-а, где legacy-ключи всё ещё присутствовали.

5. `screening_explainer.py`
   Имел собственную логику чтения candidate row с fallback на `app_a`/`app_b`, то есть жил отдельным пониманием того же контракта.

### Поля по факту после фикса

Обязательные:

- `query_app_id: str`
- `candidate_app_id: str`
- `screening_status: preliminary_positive | preliminary_negative`

Для shortlist-строк screening-слоя:

- `retrieval_score: float`
- `retrieval_rank: int`
- `retrieval_features_used: list[str]`
- `screening_cost_ms: int`
- `screening_warnings: list[str]`

Опциональные:

- `screening_explanation: dict | null`
- `per_view_scores: dict[str, float]`
- `evidence: list[dict]`
- `signature_match: dict`
- `shortcut_applied: bool`
- `shortcut_reason: str | null`
- `shortcut_status: str | null`
- `app_a_apk_path: str | null`
- `app_b_apk_path: str | null`
- `features_used: list[str]`

Deprecated / removed from canonical output:

- `app_a`
- `app_b`

## Итог выполнения по submodule

- Writer переведён на canonical-only output.
- Reader мигрирует legacy `app_a`/`app_b` в canonical row с `DeprecationWarning`.
- Рассинхрон canonical и legacy полей теперь даёт явный `ValueError`.
- Runner и explainer переведены на единый контракт.
- Добавлены TDD-тесты на roundtrip, mismatch error и legacy migration.
- Полный `pytest` в submodule: `783 passed, 7 skipped`.

## Блокер

Файлы в `phd`-репозитории не writable из текущего sandbox:

- `/Users/valeryvpetrov/phd/inbox/wip/SCREENING-19-CONTRACT-ALIGN-wip.md`
- `/Users/valeryvpetrov/phd/system/screening-contract-v1.md`

Этот файл подготовлен как переносимый черновик содержимого для ручного применения в `phd`.

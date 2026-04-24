# Screening Contract v1

Статус: `prepared-draft`
Дата: `2026-04-24`
Назначение: единый контракт candidate row на границе слоя D → E.

## 1. Канонический ключ пары

Единственный source of truth:

1. `query_app_id`
2. `candidate_app_id`

Поля `app_a` и `app_b` не входят в canonical output. Legacy-артефакты с этими ключами допустимы только на чтении через явную миграцию с warning.

## 2. Минимальный row-контракт

```yaml
query_app_id: APP-QUERY-001
candidate_app_id: APP-CAND-017
screening_status: preliminary_positive
retrieval_score: 0.8462
retrieval_rank: 1
retrieval_features_used:
  - code
  - metadata
screening_cost_ms: 7
screening_warnings: []
screening_explanation: null
```

### Required

1. `query_app_id: str`
2. `candidate_app_id: str`
3. `screening_status: str`

### Required for shortlist rows emitted by layer D

1. `retrieval_score: float`
2. `retrieval_rank: int`
3. `retrieval_features_used: list[str]`
4. `screening_cost_ms: int`
5. `screening_warnings: list[str]`

### Optional diagnostics

1. `screening_explanation: dict | null`
2. `per_view_scores: dict[str, float]`
3. `evidence: list[dict]`
4. `signature_match: dict`
5. `shortcut_applied: bool`
6. `shortcut_reason: str | null`
7. `shortcut_status: str | null`
8. `app_a_apk_path: str | null`
9. `app_b_apk_path: str | null`
10. `features_used: list[str]`

## 3. Семантика `screening_status`

Допустимые значения:

1. `preliminary_positive`
   Пара прошла threshold policy screening-слоя и попала в `candidate_list`.
2. `preliminary_negative`
   Пара была оценена screening-слоем, но не прошла threshold policy. Такое значение допустимо в диагностических/внутренних артефактах, но не должно появляться в production `candidate_list`, который по контракту содержит только shortlist.

## 4. Инварианты

1. `query_app_id` и `candidate_app_id` непустые.
2. `query_app_id != candidate_app_id`.
3. Если входная запись содержит legacy `app_a`/`app_b`, их значения обязаны совпадать с canonical key; иначе это contract error.
4. Canonical output writer-а и runner-а не содержит `app_a`/`app_b`.

## 5. Взаимодействие с `THRESH-002`

1. `THRESH-002` остаётся единственной threshold policy screening-слоя.
2. `screening_status=preliminary_positive` означает, что `retrieval_score` удовлетворил operating point `THRESH-002`.
3. `screening_status=preliminary_negative` означает, что score был ниже operating point `THRESH-002`.
4. Layer E не пересчитывает threshold и не выводит screening verdict заново; он только читает уже сформированный shortlist / diagnostics.

## 6. Миграция legacy

1. Reader может читать legacy row с `app_a`/`app_b`.
2. При такой миграции reader обязан поднять `DeprecationWarning`.
3. Результат миграции — canonical-only row.
4. Writer, runner и explainer не должны заново записывать `app_a`/`app_b`.

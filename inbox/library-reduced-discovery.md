# DEEP-24-LIBRARY-REDUCED-UNIFY — discovery: три формулы `library_reduced_score`

Дата: 2026-04-26.
Источник: рекомендация критика DEEP волны 23 (`/Users/valeryvpetrov/phd/inbox/critics/deep-verification-2026-04-26.md`, коммит `707b4bf`), пункт 1.
Закрывает: `WEAK-2026-04-22-11`.

## Каноническая формула (контракт v1, раздел 4.4)

```
library_reduced_score(A, B) = |(F_A ∩ F_B) \ L| / |(F_A ∪ F_B) \ L|
```

где:

- `F_A`, `F_B` — полные множества признаков по всем активным слоям `M_static`;
- `L` — единый library-mask, объединение признаков, помеченных как TPL на этапе noise cleanup;
- `|...|` — мощность множества;
- результат ∈ `[0, 1]`.

Отдельно зафиксировано в контракте:

1. оператор не зависит от `stages.pairwise.metric` (это управляет только `full_similarity_score`);
2. при пустом `L` формула вырождается в чистый Жаккар по `F_A ∪ F_B`;
3. при `(F_A ∪ F_B) \ L = ∅` — `library_reduced_score = null`, `analysis_status = analysis_failed`, `failure_reason = view_build_failed`;
4. отдельного порога для `library_reduced_score` нет, общий `pairwise.threshold` остаётся единственным.

## Три формулы в текущем коде

### Формула 1. `m_static_views.compare_all` (строки 1047–1063)

```python
reduced_sum = 0.0
reduced_total = 0.0
for layer in selected:
    if layer == "library":
        continue
    weight = LAYER_WEIGHTS.get(layer)
    ...
    layer_score = layer_result.get("score", 0.0)
    reduced_sum += weight * layer_score
    reduced_total += weight

library_reduced_score = reduced_sum / reduced_total if reduced_total > 0.0 else 0.0
```

Семантика: weighted-average per-layer score-ов по всем слоям, кроме `library`. Не Жаккар, не работает с library-mask, использует калиброванные веса `LAYER_WEIGHTS`.

### Формула 2. `pairwise_runner.calculate_set_scores` (строки 1109–1115)

```python
reduced_layers = [layer for layer in selected_layers if layer != "library"]
if reduced_layers:
    reduced_left = aggregate_features(layers_a, reduced_layers)
    reduced_right = aggregate_features(layers_b, reduced_layers)
    library_reduced_score = calculate_set_metric(metric, reduced_left, reduced_right)
else:
    library_reduced_score = 0.0
```

Семантика: set-метрика (Жаккар/косинус/dice/...) на агрегированных признаках без library-слоя. Работает на ходу через `aggregate_features` — но «exclude library» означает «выбросить весь слой `library`», а не «выбросить TPL-маркированные признаки». Метрика зависит от `stages.pairwise.metric`, что прямо нарушает контракт пункт 1.

### Формула 3. GED-путь — `pairwise_runner.calculate_ged_scores` строка 1076 → `result_contract.calculate_library_reduced_score`

```python
def calculate_library_reduced_score(pair_records, dots_1, dots_2):
    non_library_count_1 = sum(1 for dot in dots_1 if not is_library_like_graph(dot))
    non_library_count_2 = sum(1 for dot in dots_2 if not is_library_like_graph(dot))
    denominator = max(non_library_count_1, non_library_count_2)
    if denominator == 0:
        return 0.0
    library_like_first = {dot.name for dot in dots_1 if is_library_like_graph(dot)}
    library_like_second = {dot.name for dot in dots_2 if is_library_like_graph(dot)}
    similarity_sum = 0.0
    for record in pair_records:
        if record["first"] in library_like_first or record["second"] in library_like_second:
            continue
        similarity_sum += record["similarity"]
    return similarity_sum / denominator
```

Семантика: сумма pairwise GED-сходств по non-library парам деленная на `max(non_library_count_a, non_library_count_b)`. Не Жаккар, шкала [0, ∞), не нормирована на пересечение/объединение. В контракте v1 это явно отменено: «отменено 1 — ветвь GED-пути».

## Шкалы и несопоставимость

| Точка | Шкала | Тип | Зависит от metric? |
|---|---|---|---|
| `compare_all` | weighted-avg per-layer score-ов, [0, 1] | numeric | косвенно (через score-ы слоёв) |
| `calculate_set_scores` | set-метрика на агрегированных признаках, [0, 1] | set | да, прямо |
| GED `result_contract` | `sum(sim) / max(count)`, [0, ~1] | pairwise | нет, но другая шкала |

Численно три формулы дают разные значения на одних и тех же входах, и это ровно то, что тесты ниже зафиксируют до правки.

## План правки (TDD)

1. Создать каноническую функцию `library_reduced_score_canonical(features_a, features_b, library_mask)` в `m_static_views.py`. Сигнатура работает с двумя dict-ами признаков по слоям и библиотечной маской `L: set[str]` (объединение TPL-меток с обеих сторон).
2. `compare_all` (m_static_views.py:1047–1063) — заменить на вызов canonical, library-mask собрать из `library`-слоя обеих сторон.
3. `pairwise_runner.calculate_set_scores` (1109–1115) — заменить на вызов canonical, без зависимости от `metric`.
4. GED-путь — каноническая формула не имеет GED-семантики, поэтому `result_contract.calculate_library_reduced_score` оборачиваем в адаптер: вход (pair_records, dots_1, dots_2) → строим `F_A`, `F_B`, `L` (по `is_library_like_graph`) → вызываем canonical. Старая формула (sum/max) сохраняется как диагностическое поле `ged_non_library_mean`, но больше не подписывается именем `library_reduced_score`.

## Координация с NOISE-24-MASK-CONTRACT

NOISE может ввести явный mask-контракт. На момент написания canonical использует своё локальное определение `L` через признаки `library`-слоя. Если NOISE сделает интерфейс mask первее — переключим canonical на тот контракт через адаптер, не меняя Жаккаровой формулы.

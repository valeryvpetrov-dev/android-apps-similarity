# SCREENING-20-LSH-DIAGNOSTIC

Диагностический прогон для связки:

- screening features: `code + metadata`
- screening metric: `jaccard`
- threshold (`THRESH-002`): `0.28`
- LSH geometry: `num_perm=128`, `bands=32`, `seed=42`

Конфиг прогона: [mini-corpus-cascade.yaml](/tmp/wave20-D-submodule/experiments/artifacts/SCREENING-20-LSH-DIAGNOSTIC/mini-corpus-cascade.yaml)

Артефакт отчёта: [report.json](/tmp/wave20-D-submodule/experiments/artifacts/SCREENING-20-LSH-DIAGNOSTIC/report.json)

## Методика

Для каждого `C(n,2)` по корпусу:

1. Считается `full_score` как точный screening-score через `screening_runner.calculate_pair_score` на выбранных screening-layer'ах.
2. Считаются `per_view_scores` по слоям через `m_static_views.compare_m_static_layer`.
   Для `library/resource/component` это позволяет использовать enhanced-comparator, если входной payload это поддерживает; для mini-corpus APK-извлечения здесь фактически используется quick/Jaccard path по set-признакам.
3. Отдельно строится LSH-shortlist через `MinHashSignature + LSHIndex` по `candidate_index.features`.
4. Для каждой пары пишутся флаги:
   `in_shortlist`, `passed_thresh`, `full_score`, `selected_similarity_score`.

Семантика `selected_similarity_score`:

- если пара попала в shortlist, это её exact screening-score;
- если пара не попала в shortlist, здесь пишется `0.0`, потому что в реальном fast-path пара не доходит до threshold-фильтра.

## Агрегации

В `report.json.summary`:

- `total_pairs`: число всех `C(n,2)` пар.
- `shortlist_size`: сколько пар отдал LSH до threshold.
- `candidate_list_size`: сколько shortlist-пар прошло `THRESH-002`.
- `recall_at_shortlist`: доля пар с `full_score >= THRESH-002`, которые вообще попали в shortlist.
- `false_negative_rate`: `1 - recall_at_shortlist`.
- `shortlist_false_positive_count`: сколько shortlist-пар имели `full_score < THRESH-002`.
- `avg_per_view_score_in_candidates`: среднее по `mean(per_view_scores.values())` только для финальных candidate-пар (`in_shortlist && passed_thresh`).

## Результат mini-corpus

Корпус: `apk/` из текущего worktree, всего `5` APK:

- `simple_app-empty`
- `simple_app-releaseNonOptimized`
- `simple_app-releaseOptimized`
- `simple_app-releaseRename`
- `snake`

Итоговые числа:

- `total_pairs = 10`
- `shortlist_size = 6`
- `candidate_list_size = 6`
- `positive_pairs_above_threshold = 9`
- `recall_at_shortlist = 0.6666666666666666`
- `false_negative_rate = 0.33333333333333337`
- `shortlist_false_positive_count = 0`
- `avg_per_view_score_in_candidates = 0.7840909090909091`

Явные ложные отрицания на этом мини-корпусе:

- `simple_app-empty` vs `simple_app-releaseNonOptimized`
- `simple_app-empty` vs `simple_app-releaseOptimized`
- `simple_app-empty` vs `simple_app-releaseRename`

У всех трёх `full_score = 0.35714285714285715 >= 0.28`, но `in_shortlist = false`.

## Ограничения

- Это mini-corpus на `5` APK. Он пригоден для диагностики wiring и для обнаружения явных ложных отрицаний, но не для статистически устойчивой калибровки `THRESH-002`.
- `extract_layers_from_apk` строит упрощённые set-признаки по ZIP/manifest-содержимому. Для этого корпуса `per_view_scores` не эквивалентны full enhanced static-analysis pipeline.
- Значения чувствительны к выбранным screening features и LSH-геометрии. Для следующего шага нужна отдельная выборка больше `5` APK или комбинация mini-corpus + synthetic app_records с заранее известными edge-case парами.

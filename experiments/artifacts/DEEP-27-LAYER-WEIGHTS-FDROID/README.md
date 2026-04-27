# DEEP-27-LAYER-WEIGHTS-FDROID-CALIBRATE: реальная калибровка LAYER_WEIGHTS

## Что сделано

`LAYER_WEIGHTS` в `script/m_static_views.py` ранее (волна 22, DEEP-22) был вынесен
в JSON-артефакт, но численно совпадал с hard-coded fallback (нормировка волны 19,
ID DEEP-19). Реальная калибровка по корпусу была заблокирована EXEC-080 (нужен
AndroZoo train/test split).

В волне 27 реализован обходной путь: грид-поиск по симплексу весов на labelled-парах
F-Droid v2 (350 APK). Результат — реально откалиброванные веса с метаданными
(`train_F1`, `test_F1`, `n_train_pairs`, `n_test_pairs`, `threshold`,
`calibration_method`).

## Корпус и ground truth

- **Корпус**: F-Droid v2, 350 APK в
  `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks/` (175 пакетов
  по 2 версии каждого: `<package>_<versionCode>.apk`).
- **Clone-пары** (label=1, 175 пар): два APK с одинаковым package, разные
  versionCode. Берётся пара (min, max) по `app_id`.
- **Non-clone-пары** (label=0, 175 пар): случайные пары APK с разными package
  (без префиксного совпадения). Сэмплинг с фиксированным seed=42.
- **Сбалансированный датасет**: 175 / 175 (50% / 50%).

Ground truth — слабая, но честная: clone-определение по совпадению package_name
не учитывает форки и обфускацию. Для F-Droid v2 (без обфускации, моноритейн) это
корректный proxy.

## Метод калибровки

- **Grid-search** по симплексу 4 активных слоёв (`code`, `component`, `resource`,
  `library`) с шагом 0.05 (1771 точка). Условие: `sum(w) == 1.0`, `w_l >= 0`.
- **Слой `api`** в калибровку не входит: `extract_api_markov` требует библиотеку
  `androguard`, которая не установлена в текущем окружении. В JSON `api=0.0` с
  явной отметкой в `note`. После установки `androguard` калибровку можно
  повторить с `--layers code,component,resource,library,api`.
- **Слои `metadata`, `code_v4`, `code_v4_shingled`, `resource_v2`** имеют
  `weight=0.0`: `metadata` — tiebreaker (не входит в weighted score по
  архитектурному решению); v4/v2 — не активированы до отдельной калибровки.
- **Train/test split**: 70/30, стратифицированный по label, seed=42.
  Train = 246 пар (123 clone + 123 non-clone), test = 104 пары.
- **Per-pair score**: `sum_l(w_l * J_l(a, b)) / sum_l w_l`, где `J_l` —
  Jaccard на слое `l`, сумма по слоям с `w_l > 0` и непустыми feature-сетами.
- **Метрика**: F1 при оптимальном threshold (выбирается на train, оценивается
  на test). Кандидаты в threshold — уникальные значения train scores +
  midpoints + {0.0, 0.5, 1.0}.

## Результат

| Слой       | Старые веса (DEEP-19) | Новые веса (DEEP-27) | Разница |
|:-----------|----------------------:|---------------------:|--------:|
| code       | 0.391                 | 0.05                 | -0.34   |
| component  | 0.217                 | 0.60                 | +0.38   |
| resource   | 0.174                 | 0.00                 | -0.17   |
| library    | 0.087                 | 0.35                 | +0.26   |
| api        | 0.130                 | 0.00                 | -0.13   |
| **sum**    | **1.000**             | **1.000**            | -       |

**Метрики:**

| Split   | Pairs | F1     | Precision | Recall |
|:--------|------:|-------:|----------:|-------:|
| Train   | 246   | 0.980  | 0.984     | 0.976  |
| Test    | 104   | 0.943  | 0.926     | 0.962  |

**Optimal threshold**: 0.330 (full_similarity_score >= 0.330 ⇒ clone).

## Интерпретация

- `component` (0.60) — **доминирующий** сигнал на F-Droid v2. Это объясняется
  тем, что `component_view` извлекает имена компонентов (`activity`, `service`,
  `receiver`, `provider`) и их permissions из `AndroidManifest.xml`. Между
  версиями одного приложения список компонентов почти не меняется, между
  разными приложениями — сильно различается. На F-Droid v2 (без обфускации
  manifest-а) это очень дискриминативный признак.
- `library` (0.35) — **сильный** вторичный сигнал. На F-Droid v2 авторские
  библиотеки (org.fdroid.*, app-specific packages) отличают приложения друг
  от друга, при этом сохраняясь между версиями.
- `code` (0.05) — **низкий** вес, потому что quick-mode `code` извлекает
  лишь string-set имён классов из APK ZIP без учёта структуры (нет TLSH/v4-
  фингерпринта в этой калибровке). На F-Droid v2 без обфускации Jaccard
  по именам классов между разными приложениями довольно высокий из-за
  AndroidX/Kotlin runtime — слабо разделяет clone от non-clone.
- `resource` (0.00) — выпал из активных слоёв: на F-Droid v2 ресурсы
  (например, `res/values/strings.xml` ключи) сильно пересекаются между
  разными приложениями (общие framework-ключи) и не дают дискриминации
  на уровне "package vs package".
- `api` (0.00) — не входил в калибровку (нет `androguard`). Это **технический
  пробел**, не data-driven результат.

## Ограничения и риски

1. **Слабая ground truth**: clone-определение через совпадение `package_name`
   не учитывает рекомпиляцию, форки, R8-обфускацию (которой на F-Droid v2 нет
   массово). На AndroZoo с обфусцированными приложениями калибровка даст
   другие веса.
2. **Quick-mode features**: используется `extract_layers_from_apk` (string-set
   слои из APK ZIP) без enhanced view (`code_v4`, `resource_v2`, etc.).
   Реальный production-`compare_all` с enhanced features может вести себя
   иначе.
3. **Nonparticipation `api`**: без `androguard` слой `api` не калибруется.
   Это известно и зафиксировано в `note` JSON-артефакта.
4. **Grid-step=0.05**: даёт 1771 точку — приемлемо. С шагом 0.01 сетка =
   176 851 точек, train_F1 может вырасти на 1-2%, но overfitting-риск растёт.
5. **Test set маленький** (104 пары). Confidence interval для F1 широкий.

Эти ограничения — основания для **повторной калибровки на AndroZoo** после
EXEC-080. Текущий результат — "лучше, чем было" (data-driven вместо
интуитивной нормировки), но не финальный.

## Воспроизведение

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/calibrate_layer_weights_fdroid.py \
    --corpus_dir /Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
    --out experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json \
    --grid_step 0.05 \
    --seed 42 \
    --test_size 0.3
```

Время прогона: ~10 секунд (350 APK extraction + 1771 grid-точка × 246 пар train).

## Тесты

`script/test_layer_weights_fdroid_calibrate.py` (6 тестов):
- контракт `calibrate_layer_weights_grid` (ключи результата, сумма весов = 1);
- `iter_grid_weights` — корректные точки симплекса;
- synthetic-сценарий с известным решением (code-слой разделяет clone/non-clone);
- детерминированность по фиксированному seed;
- helper `score_pair_with_weights` — identity и disjoint граничные случаи.

Все 6 тестов зелёные. Полный pytest-прогон по `script/`: 946 passed, 1 failed
(`test_e2e_smoke_fails_fast_without_required_dep` — pre-existing, не связан с
DEEP-27).

## Связанные артефакты

- `experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json` —
  откалиброванные веса с метаданными.
- `experiments/artifacts/DEEP-22-LAYER-WEIGHTS-EXTERNALIZED/calibrated_weights.json` —
  canonical путь (`m_static_views.CALIBRATED_WEIGHTS_PATH`), куда скопировано
  содержимое DEEP-27. Обе ссылки указывают на одни и те же значения.
- `script/calibrate_layer_weights_fdroid.py` — CLI для повторной калибровки.
- `script/test_layer_weights_fdroid_calibrate.py` — тесты.

## Изменённые тесты

После реальной калибровки `LAYER_WEIGHTS != _LAYER_WEIGHTS_FALLBACK`. Тесты,
которые ранее зашивали конкретные числа DEEP-19-нормировки, обновлены:

- `test_layer_weights.py::test_active_weights_relative_proportions_preserved` →
  `test_active_weights_in_unit_interval` (вместо ratio code/library = 4.5
  проверяется только инвариант `w_i in [0, 1]`).
- `test_layer_weights.py::TestAggregateRegression::test_calibrated_aggregate_matches_manual` →
  `test_aggregate_formula_is_weighted_average` (фиксирует формулу, а не
  конкретные числа).
- `test_layer_weights_propagate.py::test_loaded_weights_match_fallback_after_externalisation` →
  `test_loaded_weights_are_valid_distribution`.
- `test_m_static_views_scoring.py::TestCompareAllApiAggregation::*` (3 теста):
  expected full_similarity_score теперь рассчитывается динамически через
  `LAYER_WEIGHTS`, а не зашит как `0.8 / 1.15`.
- `test_both_empty_semantics.py::test_resource_both_empty_excluded_weights_renormalized`:
  expected рассчитывается динамически.

LAYER_WEIGHTS — single source of truth. Тесты не дублируют его содержимое.

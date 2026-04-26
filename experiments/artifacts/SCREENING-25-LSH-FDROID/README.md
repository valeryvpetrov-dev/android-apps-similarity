# SCREENING-25-LSH-FDROID

Артефакт для `SCREENING-25-LSH-CALIBRATE-FDROID`.

Цель: проверить калибровку MinHash LSH `num_perm` / `bands` на полном
корпусе F-Droid v2 вместо mini-corpus волны 24.

## Корпус

Источник:

```bash
/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks
```

Размер:

- APK: `350`
- всего пар: `61075`
- synthetic clone pairs: `4526`, где `full_score > 0.50`
- screening threshold `THRESH-002`: `0.28`
- пар выше `THRESH-002`: `40254`

## Методика

Команда:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/calibrate_lsh_recall_fdroid.py
```

Сетка:

- `num_perm = 64,128,256`
- `bands = 16,32,64`
- слои: `code + metadata`
- metric: `jaccard`
- target recall: `0.85`
- shortlist budget: не больше `30%` от всех пар корпуса

Для каждой пары считается exact `full_score`. Ground truth для recall в этом
артефакте синтетический: clone, если `full_score > 0.50`. LSH shortlist
сравнивается с этим множеством clone pairs.

## Результаты

| num_perm | bands | recall_at_shortlist | shortlist_size | shortlist_pair_ratio |
| --- | --- | --- | --- | --- |
| 64 | 16 | 0.8110914714980115 | 11223 | 0.18375767498976667 |
| 64 | 32 | 1.0 | 54951 | 0.8997298403602129 |
| 64 | 64 | 1.0 | 61075 | 1.0 |
| 128 | 16 | 0.15311533362792754 | 844 | 0.013819074907900122 |
| 128 | 32 | 0.929297392841361 | 15795 | 0.25861645517805976 |
| 128 | 64 | 1.0 | 60431 | 0.9894555873925501 |
| 256 | 16 | 0.018338488731771983 | 83 | 0.0013589848546868605 |
| 256 | 32 | 0.37406098099867435 | 2466 | 0.04037658616455178 |
| 256 | 64 | 0.9982324348210341 | 30790 | 0.5041342611543185 |

Balanced optimum:

```json
{
  "num_perm": 128,
  "bands": 32,
  "recall_at_shortlist": 0.929297392841361,
  "shortlist_size": 15795,
  "shortlist_pair_ratio": 0.25861645517805976
}
```

Baseline prod-default `(num_perm=128, bands=32)` дает тот же результат:

```json
{
  "recall_at_shortlist": 0.929297392841361,
  "shortlist_size": 15795,
  "shortlist_pair_ratio": 0.25861645517805976
}
```

## Интерпретация

На полном F-Droid v2 корпусе `128/32` уже проходит целевой recall `>= 0.85`
и остается в бюджете shortlist `<= 30%` от всех пар. Это production-значение:
`15795/61075` пар, а не полный перебор как в mini-corpus волны 24.

Конфигурации с `bands=64` дают почти полный recall, но shortlist становится
слишком дорогим: `128/64` покрывает `98.95%` всех пар, `64/64` покрывает
`100%`. Конфигурации `128/16`, `256/16`, `256/32` слишком агрессивно режут
shortlist и теряют recall.

## Рекомендация

Production default не менять. Текущий baseline `num_perm=128`, `bands=32`
является balanced optimum в этой сетке:

- target recall достигнут: `0.9293 >= 0.85`
- shortlist меньше бюджета: `25.86% <= 30%`
- улучшения относительно baseline нет: `recall_delta_vs_baseline = 0.0`

Отдельный `{fix}`-коммит для production default не нужен.

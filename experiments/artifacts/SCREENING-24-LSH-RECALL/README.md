# SCREENING-24-LSH-RECALL

Артефакт для `SCREENING-24-LSH-RECALL-IMPROVE`.

Цель: ответить на замечание критика SCRN волны 23 по `recall_at_shortlist=0.67`
из волны 20 и проверить, можно ли поднять recall LSH-shortlist до `>= 0.85`
на mini-corpus через сетку `num_perm` / `bands` при `THRESH-002 = 0.28`.

## Методика

CLI:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/calibrate_lsh_recall.py \
  --corpus_dir apk \
  --out experiments/artifacts/SCREENING-24-LSH-RECALL/report.json \
  --num_perm_grid 64,128,256 \
  --bands_grid 16,32,64 \
  --thresh 0.28
```

Для каждого `C(n,2)` по mini-corpus:

1. Exact `full_score` считается через публичный API
   `screening_runner.calculate_pair_score`.
2. Положительная пара определяется как `full_score >= 0.28`.
3. Для каждой конфигурации строится MinHash LSH shortlist по слоям
   `code + metadata`.
4. `recall_at_shortlist` считается как доля положительных пар, попавших в
   shortlist.
5. `optimal_config` выбирается по правилу: сначала
   `recall_at_shortlist >= 0.85`, затем минимальный `shortlist_size`.

## Результат

Mini-corpus: `5` APK, `10` пар, из них `9` пар выше `THRESH-002`.

| num_perm | bands | recall_at_shortlist | shortlist_size | false_negative_rate |
| --- | --- | --- | --- | --- |
| 64 | 16 | 0.6666666666666666 | 6 | 0.33333333333333337 |
| 64 | 32 | 1.0 | 10 | 0.0 |
| 64 | 64 | 1.0 | 10 | 0.0 |
| 128 | 16 | 0.3333333333333333 | 3 | 0.6666666666666667 |
| 128 | 32 | 0.6666666666666666 | 6 | 0.33333333333333337 |
| 128 | 64 | 1.0 | 10 | 0.0 |
| 256 | 16 | 0.0 | 0 | 1.0 |
| 256 | 32 | 0.3333333333333333 | 3 | 0.6666666666666667 |
| 256 | 64 | 1.0 | 10 | 0.0 |

Найденный `optimal_config` на mini-corpus:

```json
{
  "num_perm": 64,
  "bands": 32,
  "recall_at_shortlist": 1.0,
  "shortlist_size": 10,
  "false_negative_rate": 0.0
}
```

Текущий baseline волны 20, `num_perm=128`, `bands=32`, воспроизведен:
`recall_at_shortlist = 0.6666666666666666`, `false_negative_rate = 0.33333333333333337`.

## Вывод

На mini-corpus recall можно поднять с `0.67` до `1.0`, но цена - shortlist
становится полным перебором `10/10` пар. Это полезно как диагностический
сигнал: пары около `THRESH-002` действительно теряются при текущей геометрии
`128/32`, а увеличение числа bands повышает recall.

Продовый default `num_perm=128`, `bands=32` не обновлялся. Корпус из `5` APK
слишком мал для статистически значимого trade-off решения. Для production
нужна та же сетка на реальном корпусе F-Droid v2 с оценкой recall,
shortlist_size и downstream cost.

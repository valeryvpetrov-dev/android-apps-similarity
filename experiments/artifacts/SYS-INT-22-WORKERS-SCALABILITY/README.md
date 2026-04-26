# SYS-INT-22-WORKERS-SCALABILITY

## Методика

Smoke измеряет масштабируемость pairwise на фиксированном mini-corpus:

- corpus: `apk/`
- APK: 5
- пары: `C(5, 2) = 10`
- сетка workers: `1,2,4,8`
- timeout пары: `30` секунд

Команда:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_scalability_smoke.py \
  --corpus_dir apk/ \
  --out experiments/artifacts/SYS-INT-22-WORKERS-SCALABILITY/report.json \
  --workers_grid 1,2,4,8 \
  --pair_timeout_sec 30
```

Для каждого `W` CLI последовательно запускает один и тот же набор пар через
`pairwise_runner.run_pairwise(..., workers=W)` и считает:

- `total_time_s` — wall-clock время полного прогона;
- `throughput` — `n_pairs / total_time_s`;
- `speedup` — `total_time_s[workers=1] / total_time_s[workers=W]`.

`optimal_workers` выбирается как последний `W`, где `speedup` вырос минимум на
10% относительно предыдущего значения сетки.

## Результат mini-corpus

По `report.json`:

| workers | total_time_s | throughput | speedup |
|---:|---:|---:|---:|
| 1 | 0.846373 | 11.815115 | 1.000000 |
| 2 | 0.075605 | 132.266530 | 11.194689 |
| 4 | 0.077422 | 129.162879 | 10.932004 |
| 8 | 0.081710 | 122.384104 | 10.358266 |

`optimal_workers = 2`.

## Ограничения

Mini-corpus содержит только 5 APK и 10 пар, поэтому точка насыщения на полном
корпусе может быть сдвинута.

В текущем окружении `androguard` не установлен, поэтому часть view работает с
fallback-оценками. Это достаточно для smoke замера накладных расходов runner,
но не заменяет полноценный benchmark на production-окружении и полном корпусе.

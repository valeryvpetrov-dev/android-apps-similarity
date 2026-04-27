# SYS-INT-26-SCALABILITY-METHOD-FIX

## Методика

Цель: перепроверить вывод волны 22 `optimal_workers=2` на mini-corpus без
одиночного замера.

Команда:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_scalability_smoke.py \
  --corpus_dir apk/ \
  --out experiments/artifacts/SYS-INT-26-SCALABILITY-METHOD-FIX/report.json \
  --workers_grid 1,2,4,8 \
  --pair_timeout_sec 30 \
  --n_repeats 5 \
  --randomize_order \
  --cold_runs 1 \
  --warm_runs 4
```

Параметры:

- corpus: `apk/`
- APK: 5
- пары: `C(5, 2) = 10`
- workers grid: `1,2,4,8`
- repeats: `N=5` на каждую конфигурацию
- порядок: randomized по workers внутри каждого repeat-раунда
- cache: первый repeat каждой конфигурации `cold`, следующие 4 `warm`
- p95: nearest-rank percentile по 5 значениям

Метрики:

- `median_time_s`, `p95_time_s`, `min_time_s`, `max_time_s`
- `cold_time_s`
- `mean_warm_time_s`
- `speedup_median = median_time_s[workers=1] / median_time_s[workers=W]`
- `speedup_p95 = p95_time_s[workers=1] / p95_time_s[workers=W]`

## Результат

По `report.json`:

| workers | median_time_s | p95_time_s | cold_time_s | mean_warm_time_s | speedup_median | speedup_p95 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.630503 | 0.719393 | 0.719393 | 0.626783 | 1.000000 | 1.000000 |
| 2 | 0.076943 | 0.128020 | 0.128020 | 0.076182 | 8.194417 | 5.619380 |
| 4 | 0.079901 | 0.174638 | 0.174638 | 0.079977 | 7.891053 | 4.119338 |
| 8 | 0.084117 | 0.117435 | 0.117435 | 0.083780 | 7.495548 | 6.125882 |

`optimal_workers = 2` по median speedup на этой сетке.

## Сравнение с волной 22

Волна 22, single-run:

| workers | total_time_s | speedup |
|---:|---:|---:|
| 1 | 0.846373 | 1.000000 |
| 2 | 0.075605 | 11.194689 |
| 4 | 0.077422 | 10.932004 |
| 8 | 0.081710 | 10.358266 |

Вывод:

- `workers=2` как лучшая точка mini-corpus подтверждена новой методикой.
- Величина `speedup=11.2x` не воспроизведена: v2 даёт `8.19x` по median и
  `5.62x` по p95 для `workers=2`.
- Поэтому `optimal_workers=2` остаётся корректным observation для mini-corpus,
  но не production-параметром без замера на полном корпусе и production
  окружении.

## Ограничения

В окружении замера `androguard` не установлен, поэтому часть view работает с
fallback-оценками. Это достаточно для проверки методики runner-level smoke, но
не заменяет production benchmark.

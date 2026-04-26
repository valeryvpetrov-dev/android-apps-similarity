# SYS-25-E2E-RUNNER-CONTRACT

Артефакт закрывает первую рекомендацию критика SYS волны 23: один e2e-прогон
`pairwise_runner` одновременно включает параллельные workers, hard timeout,
SQLite feature cache, shortcut-пары и две конфигурации каскада.

## Как воспроизвести

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_e2e_smoke_v2.py \
  --out experiments/artifacts/SYS-25-E2E-RUNNER/report.json \
  --workers 4 \
  --pair-timeout-sec 10
```

`SIMILARITY_SKIP_REQ_CHECK=1` нужен потому, что smoke изолирует контракт
runner'а от внешних APK-инструментов. Сам вызов `pairwise_runner.run_pairwise`
реальный: параметры `workers`, `pair_timeout_sec` и `feature_cache_path`
передаются в API одновременно.

## Методика

Smoke строит временный synthetic corpus из 12 APK-shaped zip-файлов и 6 пар:

- 3 обычные пары, которые в baseline-конфиге проходят как `success`.
- 2 shortcut-пары с `shortcut_applied=True`, `signature_match.status=match`.
- 1 controlled hanging-пара, которая один раз превышает `pair_timeout_sec=10`.

Конфиги:

- `baseline`: `features: [code]`, `metric: jaccard`, `threshold: 0.90`.
- `multi_view`: `features: [code, metadata]`, `metric: jaccard`, `threshold: 0.90`.

Для стабильности CI тяжёлое feature extraction заменено детерминированным
extractor'ом внутри smoke harness. Это не подменяет runner: timeout, shortcut,
parallel scheduling, result ordering и SQLite cache проходят через
`pairwise_runner.run_pairwise`.

## Поля отчёта

`report.json` содержит:

- `corpus_size`, `n_pairs`, `workers`, `pair_timeout_sec`, `total_time_s`.
- `per_pair_status`: baseline-статусы всех 6 пар.
- `cache_hit_rate` и `cache_trace`: холодный и тёплый прогон на том же
  `feature_cache_path`.
- `timeout_count`: число baseline timeout-инцидентов.
- `configs_compared`: baseline и multi-view с per-pair итогами.

Ожидаемый контракт:

- baseline: 3 `success`, 2 `success_shortcut`, 1 `analysis_failed` с
  `analysis_failed_reason=budget_exceeded`.
- warm cache probe: меньше вызовов extractor'а, чем в cold run.
- shortcut-пары возвращают материализованные pair rows, не `None`.
- multi-view меняет итог хотя бы одной пары: `SYS25-NORMAL-002` становится
  `low_similarity`.

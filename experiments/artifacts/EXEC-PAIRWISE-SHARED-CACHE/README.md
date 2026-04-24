# EXEC-PAIRWISE-SHARED-CACHE

Синтетический replay для shared SQLite-cache между worker-процессами `ProcessPoolExecutor`.

## Что меряется

- `time_before`: параллельный прогон без shared cache.
- `time_after`: тот же прогон, но каждый worker читает/пишет общий `FeatureCacheSqlite`.
- `speedup_ratio = time_before / time_after`.

## Корпус

- `5` уникальных synthetic APK-файлов.
- `20` pairwise-задач.
- Паттерн пар: `(0,1) (2,3) (4,0) (1,2) (3,4)` и `4` повтора.
- `2` worker-процесса.
- Synthetic `extract_all_features` спит `0.12s` на уникальный APK и возвращает детерминированный feature bundle по SHA-256 файла.

Такой стенд специально изолирует эффект shared-cache от внешних зависимостей. В текущем sandbox отсутствует `androguard`, поэтому замер сделан на synthetic extractor, что соответствует требованию синтетического прогона.

## Replay

```bash
python3 experiments/artifacts/EXEC-PAIRWISE-SHARED-CACHE/measure_benchmark.py
```

Скрипт перезапишет [benchmark-report.json](/tmp/wave17-I-submodule/experiments/artifacts/EXEC-PAIRWISE-SHARED-CACHE/benchmark-report.json).

## Последний замер

- `time_before_seconds = 2.574022`
- `time_after_seconds = 0.477043`
- `speedup_ratio = 5.395783`
- `cache_rows_after_run = 5`

Требование `>= 2x` выполнено.

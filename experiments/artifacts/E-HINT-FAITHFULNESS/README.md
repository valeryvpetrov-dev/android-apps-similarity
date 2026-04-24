# E-HINT-FAITHFULNESS

Артефакт для автоматической оценки качества `hint`-объяснений без ручной разметки.

## Что считается

- `faithfulness`: корреляция между важностью признака в hint и `|Δscore|` после его маскировки.
- `sufficiency`: retained-score метрика для `hint_only_features`; при нормализованном `score_fn` значение `1.0` означает, что hint сам по себе сохраняет полный score.
- `comprehensiveness`: `score_full - score_without_hint`.

Реализация: [`script/hint_faithfulness.py`](/tmp/wave17-F-submodule/script/hint_faithfulness.py).

## Как воспроизвести

Из корня `submodule`:

```bash
python3 script/hint_faithfulness.py
```

Скрипт пытается прочитать:

```text
experiments/artifacts/E-HINT-004/deep-184-annotated.csv
```

Если файл найден, ожидаются колонки:

- `hint_id` или `id`
- `pair_features` / `pair_features_json` / `full_features`
- `hint_features` / `hint_features_json`
- опционально `hint_only_features`

`pair_features` и `hint_features` должны быть JSON-объектами вида `{"feature": weight}` либо списками/строками имён признаков.

## Текущий прогон

- Источник: synthetic fallback
- Причина: `experiments/artifacts/E-HINT-004/deep-184-annotated.csv` отсутствовал в ветке на момент прогона
- JSON-отчёт: [`report.json`](/tmp/wave17-F-submodule/experiments/artifacts/E-HINT-FAITHFULNESS/report.json)

## Пример synthetic-результатов

- `SYN-HINT-001`: faithfulness `1.0`, sufficiency `1.0`, comprehensiveness `1.0`
- `SYN-HINT-002`: faithfulness `1.0`, sufficiency `0.7`, comprehensiveness `0.7`
- `SYN-HINT-004`: faithfulness `-1.0`, sufficiency `1.0`, comprehensiveness `1.0`

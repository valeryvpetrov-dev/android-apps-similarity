# SCREENING-31-INDEX-RECALIBRATE-MIXED-CORPUS

## Что это

Замер `recall_at_shortlist` для MinHash LSH на смешанном корпусе:
F-Droid v2 + SCRN-30 namespace-shift + DEEP-30 code-injection +
HINT-30 R8 mock. Это расширяет SCRN-25/30: вместо одного агрегата отчёт
показывает recall отдельно по классам модификации `1/2/4/5/6`.

## Источники

| Класс | Источник | n пар |
|---|---|---:|
| `class_1` | F-Droid v2, соседние версии одного package | 177 |
| `class_2` | F-Droid v2, версии одного package через один релиз | 2 |
| `class_4` | `SCREENING-30-PACKAGE-RENAME/report.json` | 20 |
| `class_5` | `DEEP-30-CODE-INJECT/report.json` clone rows | 35 |
| `class_6` | `EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json` mock rows | 10 |

Всего: `488` records, `244` expected pairs, LSH geometry `num_perm=128`,
`bands=32`, `seed=42`.

## Recall per class

| Класс | Hits / n | Recall | Интерпретация |
|---|---:|---:|---|
| `class_1` | 161 / 177 | 0.9096 | Хорошо ловит обычные F-Droid version-clones. |
| `class_2` | 2 / 2 | 1.0000 | Хорошо на малой выборке version-drift, но n=2. |
| `class_4` | 8 / 20 | 0.4000 | Namespace-shift ловится хуже: apktool rebuild меняет resource/component/signing tokens. |
| `class_5` | 35 / 35 | 1.0000 | Code-injection ловится стабильно; DEEP-30 даёт F1=1.0. |
| `class_6` | 0 / 10 | 0.0000 | R8 mock ломает raw MinHash-signature сильнее всего. |

## Вывод

LSH хорошо держит классы с высоким сохранением screening-signature:
`class_1`, `class_2`, `class_5`. Слабые зоны: `class_4` и особенно
`class_6`, где преобразование меняет namespace/method-name surface и
кандидатная пара не попадает в shortlist.

`current_thresh_002 = 0.70`, `proposed_thresh_002 = 0.70`. Этот прогон не
предлагает менять THRESH-002: провалы `class_4/class_6` здесь являются
проблемой candidate-index/feature surface, а не доказанным optimum для
score-threshold.

## Воспроизведение

```bash
python3 -m pytest script/test_lsh_recalibrate_mixed.py -q
python3 script/run_lsh_recalibrate_mixed.py \
  --fdroid-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --scrn30-path experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json \
  --deep30-path experiments/artifacts/DEEP-30-CODE-INJECT/report.json \
  --hint30-path experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json \
  --out experiments/artifacts/SCREENING-31-MIXED-CORPUS/report.json
```

Основной артефакт: [`report.json`](report.json).

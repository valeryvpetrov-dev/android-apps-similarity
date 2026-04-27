# SCREENING-27-THRESH-CALIBRATE

Артефакт для `SCREENING-27-THRESH-CALIBRATE-TRAIN-TEST`.

Цель: заменить эвристику `THRESH-002 = 0.28` на калибровку по
фиксированной train/test методике на F-Droid v2.

## Корпус

Источник:

```bash
/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks
```

Размер:

- APK: `350`
- train/test split: `245 / 105` APK (`70/30`)
- fixed seed: `2`
- train pairs: `29890`, clone: `76`, non-clone: `29814`
- test pairs: `5460`, clone: `15`, non-clone: `5445`

Ground truth строится внутри каждого split:

- `clone`: одинаковый `package_name` и одинаковая подпись APK;
- `non_clone`: другой package или другая подпись;
- package берется из metadata token `package_name:*`, fallback — stem F-Droid APK до финального `_version`;
- подпись берется как SHA-256 certificate chain через `signing_view.extract_signing_chain`, fallback — metadata `signing_prefix:*`.

## Методика

Команда:

```bash
python3 script/run_thresh_calibrate_train_test.py
```

Параметры:

- features: `code + metadata`
- metric: `jaccard`
- threshold grid: `[0.10, 0.90]` с шагом `0.05`
- operating point выбирается только на train по max F1;
- выбранный fixed threshold затем оценивается на test;
- текущий `0.28` оценивается отдельно для сравнения, но не участвует в выборе.

## Результаты

Выбранный train operating point:

| threshold | precision | recall | F1 | FPR |
| --- | ---: | ---: | ---: | ---: |
| `0.70` | `0.771429` | `0.710526` | `0.739726` | `0.000537` |

Оценка на test при fixed `0.70`:

| threshold | precision | recall | F1 | FPR |
| --- | ---: | ---: | ---: | ---: |
| `0.70` | `0.875000` | `0.466667` | `0.608696` | `0.000184` |

Проверка overfit-margin:

- `test_f1 - train_f1 = -0.131030`
- test F1 не лучше train F1 с margin `0.02`: `true`

## Сравнение с текущим 0.28

| split | threshold | precision | recall | F1 | FPR |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | `0.28` | `0.003945` | `1.000000` | `0.007859` | `0.643624` |
| train | `0.70` | `0.771429` | `0.710526` | `0.739726` | `0.000537` |
| test | `0.28` | `0.003940` | `1.000000` | `0.007849` | `0.696419` |
| test | `0.70` | `0.875000` | `0.466667` | `0.608696` | `0.000184` |

## Интерпретация

`0.28` не подтвержден как calibrated threshold для этой ground truth методики:
он дает почти полный recall, но очень высокий FPR и практически нулевой F1 из-за
массовых false positives.

Train ROC по F1 выбирает `0.70`. На test этот fixed threshold сохраняет высокую
precision и резко снижает FPR, но recall падает до `0.466667`. Поэтому результат
следует читать как сдвиг `THRESH-002` с `0.28` до `0.70` для режима,
где clone определяется через `package_name + signing certificate`.

Машиночитаемый отчет: `report.json`.

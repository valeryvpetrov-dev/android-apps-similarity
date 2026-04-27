# DEEP-28-LAYER-WEIGHTS-PROD-APPLY: sanity replay новых LAYER_WEIGHTS на HINT-27

## Что сделано

Применение находки DEEP-27 в production:

1. Подтверждено, что canonical-файл
   `experiments/artifacts/DEEP-22-LAYER-WEIGHTS-EXTERNALIZED/calibrated_weights.json`
   (куда смотрит `m_static_views.CALIBRATED_WEIGHTS_PATH`) совпадает по
   значениям и `snapshot_id` с
   `experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json`.
   Перенос фактически выполнен в коммите DEEP-27 (волна 27, `1a6b11b`); в
   волне 28 — только зафиксирован контракт совпадения тестом
   `script/test_layer_weights_prod_apply.py::test_canonical_weights_file_matches_deep27_artifact`.

2. Прогнан sanity replay по 30 парам корпуса `EXEC-HINT-27-CHANNEL-COVERAGE`
   с пересчётом `full_similarity_score` со старыми весами (DEEP-19,
   `_LAYER_WEIGHTS_FALLBACK`) и новыми (DEEP-27, `LAYER_WEIGHTS`).

## Почему именно HINT-27 corpus

Корпус EXEC-HINT-27-CHANNEL-COVERAGE собран с балансом
`8 clone / 8 repackage / 8 similar / 6 different` и покрывает все 5
evidence-каналов (`code`, `component`, `library`, `resource`, `signing`)
на 30/30 пар. Для каждой пары в `channel_dataset.json` уже сохранены
`compare_result.per_layer.<layer>.score` — это значит, что replay
**не требует повторной APK-обработки**, а пересчитывает агрегацию по
формуле `compare_all`:

```
full_similarity_score = sum_l(w_l * score_l) / sum_l(w_l)
```

по слоям, у которых задан вес и `status != "both_empty"` (`metadata`
исключён по архитектуре).

## Веса

| Слой       | Старые (DEEP-19, `_LAYER_WEIGHTS_FALLBACK`) | Новые (DEEP-27, `LAYER_WEIGHTS`) | Дельта |
|:-----------|--------------------------------------------:|---------------------------------:|-------:|
| code       | 0.391                                       | 0.05                             | -0.34  |
| component  | 0.217                                       | 0.60                             | +0.38  |
| resource   | 0.174                                       | 0.00                             | -0.17  |
| library    | 0.087                                       | 0.35                             | +0.26  |
| api        | 0.130                                       | 0.00                             | -0.13  |
| **сумма**  | **1.000**                                   | **1.000**                        | -      |

`code_v4`, `code_v4_shingled`, `resource_v2` — нули в обеих наборах
(не активированы до отдельной калибровки).

## Результат replay (30 пар)

Overall (по всем 30 парам):

| Метрика                                   | Старые DEEP-19 | Новые DEEP-27 |
|:------------------------------------------|---------------:|--------------:|
| `full_similarity_score` mean              | 0.728          | 0.609         |
| `full_similarity_score` median            | 0.945          | 0.875         |
| `full_similarity_score` std (population)  | 0.327          | 0.421         |

`delta = new - old`: mean **-0.119**, в 13 из 30 пар (43.3%)
`|delta| >= 0.10`.

Per-ground_truth:

| Группа    | n | old mean | new mean | delta mean |
|:----------|--:|---------:|---------:|-----------:|
| clone     | 8 | 0.977    | 0.943    | -0.034     |
| repackage | 8 | 0.953    | 0.970    | +0.017     |
| similar   | 8 | 0.538    | 0.270    | -0.268     |
| different | 6 | 0.349    | 0.137    | -0.213     |

## Интерпретация

DEEP-27 калибровка переносит вес с `code` (string-set имён классов из APK
ZIP, плохо разделяющий на F-Droid v2 без обфускации) на `component`
(имена и permissions компонентов в манифесте) и `library` (TPL-сигналы).

Replay показывает, что после переноса:

- **clone-пары почти не страдают** (-0.034 в среднем): у них совпадают
  все слои, переразвешивание принципиально не меняет картину.
- **repackage-пары даже немного укрепились** (+0.017): они опираются на
  совпадение component/library (один package, разные sha), что новый набор
  весов как раз и поощряет.
- **similar-пары сильно ослаблены** (-0.268): они держались на code-Jaccard
  (общий runtime), и снижение веса code их ожидаемо понизило.
- **different-пары тоже сильно ослаблены** (-0.213): у разных приложений
  code-Jaccard хоть и заметный (общий AndroidX), но component/library
  различаются — поэтому при новых весах их score уезжает к нулю.

В сумме это сдвиг в правильную сторону: дискриминация между clone и
non-clone усиливается (clone стоит на месте, similar/different падают).

## Ограничения

1. Корпус — **F-Droid v2**, без R8-обфускации. На AndroZoo с обфусцированными
   APK поведение может быть иным: code-сигнал там слабее не из-за «общего
   runtime», а из-за renaming, и относительный вклад component/library может
   снова поменяться. Это известный риск, зафиксирован в DEEP-27-README пункт 1.
2. **Quick-mode**: per-layer scores в HINT-27 dataset посчитаны по
   `extract_layers_from_apk` (string-set из APK ZIP) без enhanced
   `code_v4` / `resource_v2`. Production-`compare_all` с enhanced features
   может вести себя иначе.
3. **Малый размер** (30 пар, 6 different): для оценки точности классификации
   маловато. Replay — sanity-проверка эффекта, а не полноценная re-evaluation.
4. **Ground truth heuristic**: `similar`/`different` в HINT-27 определены
   через library-overlap пороги, а не через ручную разметку. Группа
   `repackage` определена по совпадению package_name + разные sha256 без
   проверки реального переупаковывания.

## Как воспроизвести

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_layer_weights_prod_replay.py \
    --dataset experiments/artifacts/EXEC-HINT-27-CHANNEL-COVERAGE/channel_dataset.json \
    --out experiments/artifacts/DEEP-28-WEIGHTS-PROD-REPLAY/report.json
```

Время прогона: < 1 секунды (без повторной APK-обработки).

## Тесты

`script/test_layer_weights_prod_apply.py` (4 теста):

- `test_component_weight_is_dominant_after_deep27` — `LAYER_WEIGHTS["component"] >= 0.5`;
- `test_code_weight_is_low_after_deep27` — `LAYER_WEIGHTS["code"] <= 0.10`;
- `test_synthetic_replay_old_vs_new_weights` — на синтетической паре
  `code=1.0, component=0.0` старый full_score >= 0.30, новый <= 0.10,
  и новый по крайней мере на 0.20 ниже старого;
- `test_canonical_weights_file_matches_deep27_artifact` — canonical
  `CALIBRATED_WEIGHTS_PATH` совпадает по `weights` и `snapshot_id` с
  DEEP-27 artifact.

Все тесты зелёные. Сценарий регрессии (canonical случайно вернётся на
старые веса DEEP-19) проверен вручную: при подмене файла все 4 теста
падают, что подтверждает, что контракт реально работает.

## Связанные артефакты

- `experiments/artifacts/DEEP-22-LAYER-WEIGHTS-EXTERNALIZED/calibrated_weights.json` —
  canonical путь весов в production (`m_static_views.CALIBRATED_WEIGHTS_PATH`).
- `experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/calibrated_weights.json` —
  источник калибровки DEEP-27 (волна 27).
- `experiments/artifacts/EXEC-HINT-27-CHANNEL-COVERAGE/channel_dataset.json` —
  корпус 30 пар с per-layer scores.
- `script/run_layer_weights_prod_replay.py` — replay-скрипт.
- `script/test_layer_weights_prod_apply.py` — тесты.
- `experiments/artifacts/DEEP-28-WEIGHTS-PROD-REPLAY/report.json` —
  собственно результат replay.

# REPR-21-TLSH-SHINGLE-ROC

Дата: `2026-04-25`

## Что сделано

Для `code_view_v4_shingled` выполнена ROC-калибровка по сетке:

- `TLSH_DIFF_MAX ∈ {100, 150, 200, 250, 300}`
- `shingle_size ∈ {3, 4, 5, 6}`

Артефакт с результатами: [report.json](./report.json)

## Корпус и разметка

Запуск выполнен на mini-корпусе `apk/` из текущего worktree:

- `simple_app-releaseNonOptimized.apk`
- `simple_app-releaseOptimized.apk`
- `simple_app-releaseRename.apk`
- `simple_app-empty.apk`
- `snake.apk`

Так как внешний `ground_truth_csv` не задавался, использована эвристика
`apk_groups`:

- `simple_app-release*` -> группа `simple_app` -> clone
- `simple_app-empty.apk` -> отдельная группа -> non-clone к release-вариантам
- `snake.apk` -> отдельная группа

Итоговая разметка пар:

- `corpus_size = 5`
- `pairs_clone = 3`
- `pairs_non_clone = 7`

## Методика

1. Для каждого `shingle_size` один раз извлекаются признаки
   `extract_code_view_v4_shingled`.
2. Для каждой пары и каждого `TLSH_DIFF_MAX` считается score через
   `compute_code_v4_shingled(...)`.
3. По score строится ROC-сweep по всем наблюдаемым порогам.
4. Для каждой комбинации параметров сохраняется operating point с максимумом
   `F1`, а при tie — с максимумом `Youden's J`.
5. Глобальный оптимум выбирается по `F1`, затем по `Youden's J`, затем по
   близости к текущим runtime defaults.

## Результат

На mini-корпусе все 20 комбинаций дали одинаковую сепарацию:

- `precision = 1.0`
- `recall = 1.0`
- `F1 = 1.0`
- `FPR = 0.0`
- `TPR = 1.0`
- `Youden's J = 1.0`
- `decision_threshold = 0.117647`

Из-за полного tie сохранены текущие runtime defaults:

- `TLSH_DIFF_MAX = 300`
- `DEFAULT_SHINGLE_SIZE = 4`

Это совпадает с `optimal` в [report.json](./report.json).

## Воспроизведение

```bash
python3 script/calibrate_tlsh_roc.py \
  --corpus_dir apk \
  --out experiments/artifacts/REPR-21-TLSH-SHINGLE-ROC/report.json
```

## Контракт

Целевой файл контракта находится вне writable-root текущей песочницы:
`/Users/valeryvpetrov/phd/system/representation-light-contract-v1.md`.

Для ручной синхронизации туда нужно добавить, например, такой фрагмент:

```md
## 9. ROC-калибровка code_view_v4_shingled

- Дата калибровки: `2026-04-25`
- Артефакт: `prototypes/submodules/android-apps-similarity/experiments/artifacts/REPR-21-TLSH-SHINGLE-ROC/report.json`
- Корпус: mini `apk/` текущего worktree, `5` APK, `3` clone-пары, `7` non-clone-пар
- Зафиксированные значения runtime:
  - `TLSH_DIFF_MAX = 300`
  - `DEFAULT_SHINGLE_SIZE = 4`
- Критерий выбора: максимум `F1`, tie-break по `Youden's J`, затем по близости к текущим defaults
- Результат mini-корпуса: `F1 = 1.0`, `Youden's J = 1.0`, `decision_threshold = 0.117647`
```

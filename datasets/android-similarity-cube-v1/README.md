# Android Similarity Cube v1

Дата release-пакета: 2026-06-12.

Это публичный release датасетов для экспериментов с
`android-apps-similarity`.

Пакет фиксирует координаты куба:

`класс изменения APK x сценарий проверки x зрелость набора`

Для реальных APK из внешних источников в репозиторий включены только SHA-256,
пары, метки, split, источник разметки и ограничения использования.
Для controlled synthetic-наборов маленькие сгенерированные APK включены
прямо в git, потому что они созданы локально для этого benchmark-пакета.

## Состав

| Координата | Смысл | Статус | Строк |
|---|---|---|---:|
| `M0 full cube` | все рабочие координаты `class x scenario x M0` | готовый канонический M0 | 92 строки: 46 координат по 2 строки |
| `DC-C01-S01-M1` | `repack`, попарное сходство | готовый M1 diagnostic set | 40 пар |
| `DC-C02-S01-M1` | `library_injection`, попарное сходство | готовый controlled synthetic M1 diagnostic set | 40 пар |
| `DC-C03-S01-M1` | `resource_change`, попарное сходство | готовый controlled synthetic M1 diagnostic set | 40 пар |
| `DC-C05-S01-M1` | `code_injection`, попарное сходство | готовый M1 diagnostic set | 40 пар |

Каждый M1-набор состоит из 20 положительных пар и 20 отрицательных пар.
Это диагностические M1-наборы, а не claim-ready benchmark.

## Файлы

| Файл | Назначение |
|---|---|
| [`dataset_cube_status.csv`](dataset_cube_status.csv) | единственный машинно-читаемый статус заполнения куба |
| [`dataset_cube_status.md`](dataset_cube_status.md) | краткая человекочитаемая сводка |
| [`release_manifest.csv`](release_manifest.csv) | список опубликованных файлов, число строк данных и SHA-256 |
| [`m0/m0_coordinates.csv`](m0/m0_coordinates.csv) | все координаты M0 и состояние данных |
| [`m0/m0_canonical_pairs.csv`](m0/m0_canonical_pairs.csv) | канонический вход для M0: 2 строки на рабочую координату |
| [`m0/m0_seed_pairs.csv`](m0/m0_seed_pairs.csv) | source-pool для M0 и будущего M1 |
| [`m0/m0_controlled_materialization.csv`](m0/m0_controlled_materialization.csv) | SHA-256 и проверки generated M0 APK |
| [`m0/generated-apks/`](m0/generated-apks/) | generated synthetic APK для слабых M0-классов |
| [`m1/DC-C01-S01-M1/manifest.csv`](m1/DC-C01-S01-M1/manifest.csv) | 20 `repack`-пар и 20 отрицательных пар |
| [`m1/DC-C02-S01-M1/manifest.csv`](m1/DC-C02-S01-M1/manifest.csv) | 20 synthetic `library_injection`-пар и 20 отрицательных пар |
| [`m1/DC-C02-S01-M1/generated-apks/`](m1/DC-C02-S01-M1/generated-apks/) | generated synthetic APK для `DC-C02-S01-M1` |
| [`m1/DC-C03-S01-M1/manifest.csv`](m1/DC-C03-S01-M1/manifest.csv) | 20 synthetic `resource_change`-пар и 20 отрицательных пар |
| [`m1/DC-C03-S01-M1/generated-apks/`](m1/DC-C03-S01-M1/generated-apks/) | generated synthetic APK для `DC-C03-S01-M1` |
| [`m1/DC-C05-S01-M1/manifest.csv`](m1/DC-C05-S01-M1/manifest.csv) | 20 `code_injection`-пар и 20 отрицательных пар |

## Как использовать

Для M0-прогонов используйте только:

- `m0/m0_canonical_pairs.csv`

`m0/m0_seed_pairs.csv` — это пул источников, а не план запуска.

Для M1-диагностики используйте соответствующий `manifest.csv` и
`pair_change_tags.csv` внутри папки нужной координаты.
Поле `split` уже разделяет строки на `dev` и `holdout`.

## APK и воспроизведение

Сырые APK для внешних `repack` и `code_injection` наборов не публикуются в git.
Причина: права и условия распространения APK зависят от исходных источников.

Для воспроизведения внешних APK нужно материализовать их по SHA-256 из `manifest.csv`.
После материализации ожидаемый локальный формат:

```text
<apk-cache>/<sha256>.apk
```

Synthetic APK для M0 опубликованы в `m0/generated-apks/`.
Synthetic APK для `DC-C02-S01-M1` опубликованы в
`m1/DC-C02-S01-M1/generated-apks/`.
Synthetic APK для `DC-C03-S01-M1` опубликованы в
`m1/DC-C03-S01-M1/generated-apks/`.

## Ограничения

Этот release нельзя использовать для сильных выводов о качестве метода:

- нельзя считать его полноценным benchmark;
- нельзя переносить выводы за пределы указанных координат куба;
- нельзя считать M0-наборы готовыми M1-наборами;
- нельзя смешивать технический отказ анализа и низкое сходство.

Допустимое использование:

- проверка формата экспериментов;
- smoke/prototype-прогоны;
- ручной разбор поведения метода;
- демонстрация воспроизводимой связи между датасетом, SHA-256 и сценарием эксперимента.

## Происхождение

Пакет экспортирован из рабочего репозитория НКР `phd`:

- дата экспорта: 2026-06-12;
- source commit: `d7774f79bda053f67610d376da94f22b3d4022a1`;
- исходная директория: `experiments/datasets/cube`.

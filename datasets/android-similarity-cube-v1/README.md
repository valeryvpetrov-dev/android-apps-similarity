# Android Similarity Cube v1

Дата release-пакета: 2026-06-12.

Это первый публичный release датасетов для экспериментов с
`android-apps-similarity`.

Пакет фиксирует координаты куба:

`класс изменения APK x сценарий проверки x зрелость набора`

Для реальных APK из внешних источников в репозиторий включены только SHA-256,
пары, метки, split, источник разметки и ограничения использования.
Для synthetic-набора `DC-C02-S01-M1` маленькие сгенерированные APK включены
прямо в git, потому что они созданы локально для этого benchmark-пакета.

## Состав

| Координата | Смысл | Статус | Строк |
|---|---|---|---:|
| `DC-C01-S01-M0` | `repack`, попарное сходство, техническая проверка | готовый M0-пул | 7 пар |
| `DC-C01-S01-M1` | `repack`, попарное сходство, рабочий диагностический набор | готовый M1 diagnostic set | 40 пар |
| `DC-C02-S01-M1` | `library_injection`, попарное сходство, controlled synthetic набор | готовый M1 diagnostic set | 40 пар |
| `DC-C05-S01-M0` | `code_injection`, попарное сходство, техническая проверка | готовый M0-пул | 20 пар |
| `DC-C05-S01-M1` | `code_injection`, попарное сходство, рабочий диагностический набор | готовый M1 diagnostic set | 40 пар |

Каждый M1-набор состоит из 20 положительных пар и 20 отрицательных пар.
Это диагностические M1-наборы, а не claim-ready benchmark.

## Файлы

| Файл | Назначение |
|---|---|
| [`dataset_cube_status.csv`](dataset_cube_status.csv) | машинно-читаемый статус заполнения куба |
| [`dataset_cube_status.md`](dataset_cube_status.md) | краткая человекочитаемая сводка |
| [`release_manifest.csv`](release_manifest.csv) | список ключевых CSV-файлов, число строк данных и SHA-256 |
| [`m0/DC-C01-S01-M0/manifest.csv`](m0/DC-C01-S01-M0/manifest.csv) | 7 `repack`-пар |
| [`m0/DC-C05-S01-M0/manifest.csv`](m0/DC-C05-S01-M0/manifest.csv) | 20 `piggybacking`-пар |
| [`m0/DC-C05-S01-M0/materialization_status.csv`](m0/DC-C05-S01-M0/materialization_status.csv) | состояние скачивания, декомпиляции и извлечения признаков |
| [`m1/DC-C01-S01-M1/manifest.csv`](m1/DC-C01-S01-M1/manifest.csv) | 20 `repack`-пар и 20 отрицательных пар |
| [`m1/DC-C01-S01-M1/pair_change_tags.csv`](m1/DC-C01-S01-M1/pair_change_tags.csv) | теги изменений для проверки объяснений |
| [`m1/DC-C02-S01-M1/manifest.csv`](m1/DC-C02-S01-M1/manifest.csv) | 20 synthetic `library_injection`-пар и 20 отрицательных пар |
| [`m1/DC-C02-S01-M1/generated-apks/`](m1/DC-C02-S01-M1/generated-apks/) | сгенерированные synthetic APK для `DC-C02-S01-M1` |
| [`m1/DC-C02-S01-M1/pair_change_tags.csv`](m1/DC-C02-S01-M1/pair_change_tags.csv) | теги synthetic library injection |
| [`m1/DC-C05-S01-M1/manifest.csv`](m1/DC-C05-S01-M1/manifest.csv) | 20 положительных и 20 отрицательных пар |
| [`m1/DC-C05-S01-M1/pair_change_tags.csv`](m1/DC-C05-S01-M1/pair_change_tags.csv) | теги изменений для проверки объяснений |

## Как использовать

Для проверки попарного сходства при переупаковке используйте:

- `m1/DC-C01-S01-M1/manifest.csv`
- `m1/DC-C01-S01-M1/pair_change_tags.csv`

Для диагностики controlled library injection используйте:

- `m1/DC-C02-S01-M1/manifest.csv`
- `m1/DC-C02-S01-M1/pair_change_tags.csv`
- `m1/DC-C02-S01-M1/generated-apks/`

Для диагностики piggybacking / code injection используйте:

- `m1/DC-C05-S01-M1/manifest.csv`
- `m1/DC-C05-S01-M1/pair_change_tags.csv`

Поле `split` уже разделяет строки на `dev` и `holdout`.

Поле `relation_label` или `label` задает разметку пары. Поле
`label_confidence` показывает уверенность разметки.

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

## APK и воспроизведение

Сырые APK для `DC-C01-S01-M1` и `DC-C05-S01-M1` не публикуются в git.
Причина: права и условия распространения APK зависят от исходных источников.

Для воспроизведения нужно материализовать APK по SHA-256 из `manifest.csv` через
доступный пользователю источник. После материализации ожидаемый локальный формат:

```text
<apk-cache>/<sha256>.apk
```

Для `DC-C02-S01-M1` synthetic APK опубликованы в
`m1/DC-C02-S01-M1/generated-apks/`; их SHA-256 зафиксированы в
`m1/DC-C02-S01-M1/generation_status.csv` и `manifest.csv`.

Поля `source_ref` и `evidence_ref` показывают, откуда взята разметка и как
обрабатывались APK при подготовке.

## Происхождение

Пакет экспортирован из рабочего репозитория НКР `phd`:

- дата экспорта: 2026-06-11;
- source commit: `3643fd88b544baea2de16dfe545ffc4f932083c0`;
- исходные директории:
  - `experiments/datasets/cube/m0/DC-C01-S01-M0`;
  - `experiments/datasets/cube/m0/DC-C05-S01-M0`;
  - `experiments/datasets/cube/m1/DC-C01-S01-M1`;
  - `experiments/datasets/cube/m1/DC-C02-S01-M1`;
  - `experiments/datasets/cube/m1/DC-C05-S01-M1`.

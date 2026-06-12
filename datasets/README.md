# Datasets

В этом каталоге лежат публичные датасетные артефакты для экспериментов с
`android-apps-similarity`.

## Доступные наборы

| Набор | Статус | Состав |
|---|---|---|
| [`android-similarity-cube-v1`](android-similarity-cube-v1/) | первый публичный release | полный M0-каркас, M1 diagnostic sets, SHA-256 APK, пары, метки, split, ограничения использования; synthetic APK для M0, `DC-C02-S01-M1` и `DC-C03-S01-M1` |

## Что хранится в git

В git хранятся легкие воспроизводимые артефакты:

- `manifest.csv`;
- `pair_change_tags.csv`;
- таблицы состояния;
- README и правила использования;
- generated synthetic APK для M0, `DC-C02-S01-M1` и `DC-C03-S01-M1`.

Сырые APK из внешних источников в этот репозиторий не добавляются.

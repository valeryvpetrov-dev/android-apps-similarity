# DC-C03-S01-M1

Дата: 2026-06-12.

Это M1 diagnostic set для `C03 resource_change` и сценария `S01 pair_similarity`.
Набор синтетический: правый APK каждой положительной пары получает controlled resource change.

## Состав

- положительных resource-change-пар: 20
- отрицательных пар: 20
- positive split: dev=16, holdout=4
- negative split: dev=16, holdout=4
- строк тегов объяснений: 80

## Статус

`ready M1 diagnostic`, не `claim-ready benchmark`.

Набор проверяет controlled resource-layer behavior. Он не доказывает качество на реальных resource-heavy приложениях.

## Файлы

- `candidate_pairs.csv`: positive-пары до сборки manifest.
- `generation_status.csv`: локальные APK-пути, SHA-256 и результат build/sign/decode.
- `decode_sanity_summary.csv`: decode summary для всех сгенерированных APK.
- `app_feature_extraction_smoke_summary.csv`: smoke-признаки приложений.
- `feature_extraction_smoke_summary.csv`: smoke-признаки пар.
- `manifest.csv`: positive и negative пары M1 diagnostic set.
- `pair_change_tags.csv`: теги для проверки объяснений.

## Разрешенное использование

Можно использовать для M1-диагностики resource layer, проверки negative sampling и подготовки малых прогонов.

## Запрещенное использование

Нельзя считать этот набор real-world resource benchmark и нельзя делать по нему широкий вывод о качестве метода.

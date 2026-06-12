# DC-C02-S01-M1

Дата: 2026-06-12.

Это M1 diagnostic set для `C02 library_injection` и сценария `S01 pair_similarity`.
Набор синтетический: правый APK каждой положительной пары получает controlled library-like package.

## Состав

- положительных library-injection-пар: 20
- отрицательных пар: 20
- positive split: dev=16, holdout=4
- negative split: dev=16, holdout=4
- строк тегов объяснений: 80

## Статус

`ready M1 diagnostic`, не `claim-ready benchmark`.

Набор проверяет controlled library-noise behavior. Он не доказывает качество на реальных SDK.

## Файлы

- `candidate_pairs.csv`: positive-пары до сборки manifest.
- `generation_status.csv`: локальные APK-пути, SHA-256 и результат build/sign/decode.
- `decode_sanity_summary.csv`: decode summary для всех сгенерированных APK.
- `app_feature_extraction_smoke_summary.csv`: smoke-признаки приложений.
- `feature_extraction_smoke_summary.csv`: smoke-признаки пар.
- `manifest.csv`: positive и negative пары M1 diagnostic set.
- `pair_change_tags.csv`: теги для проверки объяснений.

## Разрешенное использование

Можно использовать для M1-диагностики влияния библиотечных классов, проверки negative sampling и подготовки малых прогонов.

## Запрещенное использование

Нельзя считать этот набор real-SDK benchmark и нельзя делать по нему широкий вывод о качестве метода.

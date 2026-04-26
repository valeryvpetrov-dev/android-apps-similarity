# SYS-INT-24-CACHE-MANIFEST

## Цель

Единый манифест `manifest.json` фиксирует совместимость трех persistent-кэшей:

- `noise`: `script.noise_cache.NoiseCache`, JSON-файлы огибающей шума/LIBLOOM.
- `feature_sqlite`: `script.feature_cache_sqlite.FeatureCacheSqlite`, SQLite-кэш признаков для `pairwise_runner`.
- `feature_json`: `script.feature_cache.FeatureCache`, исторический JSON-кэш результата `m_static_views.extract_all_features`.

Манифест делает явными ключи, версии и правила устаревания. Это закрывает риск, при котором `FeatureCacheSqlite` и JSON `FeatureCache` параллельно хранят признаки с разными API ключей без общего описания совместимости.

## Методика

1. Для каждого кэша зафиксирован физический backend: `json_files` или `sqlite`.
2. Для каждого кэша указан точный `key_schema`.
3. Поле `version` считается текущей совместимой версией записи.
4. Запись считается устаревшей, если отсутствует любое поле ключа, `sha256` не является 64-символьным hex digest или поле версии не равно `version` из манифеста.
5. `script/cache_manifest.py` дает единый API:
   - `load()`: загрузить манифест.
   - `validate_cache_record(cache_name, record)`: проверить логическую запись.
   - `invalidate_outdated(cache_name)`: удалить физические записи со старой версией.

## Схема `manifest.json`

Каждый верхнеуровневый ключ - имя кэша: `noise`, `feature_sqlite`, `feature_json`.

Обязательные поля:

- `path`: канонический путь хранения для запуска/артефакта.
- `storage`: тип физического хранения: `json_files` или `sqlite`.
- `key_schema`: точная сигнатура ключа.
- `version_field`: поле ключа, содержащее версию.
- `version`: текущая совместимая версия.
- `invalidation_rule`: текстовое правило устаревания.

Дополнительные поля (`cache_class`, `key_format`, `payload`, `table`) документируют связь с текущей реализацией.

## Текущие ключи

- `noise`: `(sha256, profile_version)`, версия `v1`, файл `<sha256>__<profile_version>.json`.
- `feature_sqlite`: `(sha256, feature_version)`, версия `v1`, таблица `features`, `PRIMARY KEY (sha256, feature_version)`.
- `feature_json`: `(sha256, feature_version)`, версия `v1__ihash-whash`, файл `<sha256>__<feature_version>.json`.

## Миграция при изменении extraction logic

При изменении логики извлечения признаков, метода хеша ресурсов, профиля LIBLOOM/APKiD или формата payload нужно:

1. Поднять соответствующее поле `version` в `manifest.json`.
2. Обновить `invalidation_rule`, если изменилось условие совместимости.
3. Обновить места, где вызывающий код передает `profile_version` или `feature_version`.
4. Запустить:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m pytest script/test_cache_manifest.py script/test_noise_cache.py script/test_feature_cache*.py
```

5. Для существующих физических кэшей вызвать `cache_manifest.invalidate_outdated("<cache_name>")`.

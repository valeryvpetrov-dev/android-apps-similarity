# Cache Layering Discovery

- `NoiseCache` хранит JSON-файлы `cache_dir/<sha256>__<profile_version>.json`; фактический ключ: `(sha256, profile_version)`. Используется в `noise_profile_envelope.apply_libloom_detection` как кэш огибающей шума/LIBLOOM.
- `FeatureCacheSqlite` хранит таблицу `features(sha256, feature_version, blob)` в SQLite; фактический ключ: `(sha256, feature_version)`. Используется `pairwise_runner.load_layers_for_pairwise` через `feature_cache_path`, текущая версия `pairwise_runner.FEATURE_CACHE_VERSION = "v1"`.
- Исторический `FeatureCache` хранит JSON-файлы `cache_dir/<key>.json`; публичный API принимает один строковый `key`, а `get_or_extract()` строит его как `<sha256>__<feature_version>`. `m_static_views.extract_all_features` дополнительно расширяет версию до `<feature_version>__ihash-<method>`.
- Двойное хранение признаков есть между `FeatureCacheSqlite` и JSON `FeatureCache`: оба могут хранить результат `extract_all_features`, но используются разными путями (`pairwise_runner` и `m_static_views`) и не имеют общего реестра совместимости.
- Нужен единый манифест с именем кэша, точной схемой ключа, текущей версией, физическим путем и правилом инвалидизации: запись устарела, если отсутствуют поля ключа, версия записи не равна версии манифеста или ключ не соответствует схеме конкретного кэша.

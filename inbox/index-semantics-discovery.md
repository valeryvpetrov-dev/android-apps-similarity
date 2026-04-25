# SCREENING-21-INDEX-SEMANTICS discovery

1. Сейчас LSH-индекс строится не из отдельного зафиксированного поля, а из `aggregate_features(record, index_features)` в `script/screening_runner.py` внутри `_build_candidate_pairs_via_lsh()` и `build_candidate_list_batch()`. Источник токенов зависит от runtime `candidate_index.features` или `stages.screening.features`.
2. `aggregate_features()` агрегирует `app_record["layers"][layer]` on-the-fly и префиксует токены именем слоя (`code:...`, `resource:...`). Отдельного канонического поля `screening_signature` в текущем коде нет.
3. В `script/minhash_lsh.py` `MinHashSignature.from_features()` и `LSHIndex.add()/query()` уже детерминированы при фиксированном `seed`; семантическая недетерминированность сейчас не в хеш-функции, а в том, что входной набор токенов не зафиксирован контрактом ingestion-time.
4. Exact scoring (`calculate_pair_score`) и candidate retrieval живут на разных источниках: exact score использует `selected_layers`, а candidate retrieval использует `candidate_index.features`. Это подтверждает замечание критика о плавающей семантике retrieval.
5. В `/Users/valeryvpetrov/phd/system/screening-contract-v1.md` пока описан row-контракт `candidate_list`, но нет явной фиксации, что источником токенов LSH-индекса является конкретное поле `screening_signature` или функция `build_screening_signature()`.

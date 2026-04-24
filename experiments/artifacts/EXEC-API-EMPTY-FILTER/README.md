# EXEC-API-EMPTY-FILTER

Пересчёт сделан на синтетическом наборе из контрактных сценариев, потому что в текущем workspace нет готового корпусного прогона с ground-truth для честного пересчёта F1 end-to-end.

Что изменено:
- `api` исключается из weighted aggregation только при `api_view.status == "both_empty"`.
- веса остальных слоёв в `full_similarity_score` и `library_reduced_score` нормализуются заново.
- `api_view.status == "one_empty"` остаётся в агрегации с нулевым score: это асимметричный сигнал отсутствия API-данных только с одной стороны, а не симметричное отсутствие наблюдения.

Содержимое `report.json`:
- `f1_old` и `f1_new` для порога `0.60`;
- `pairs_filtered_total`;
- `pairs_filtered_by_reason`.

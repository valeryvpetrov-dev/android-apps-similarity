# Разбор журнала таймаут-инцидентов (волна 12, 2026-04-21)

> **Внимание: синтетический корпус.** Реальный файл
> `experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/timeout-incidents.jsonl`
> на момент первого разбора в репозитории отсутствует — инциденты ещё
> не наступали. Для демонстрации работы CLI прогон сделан на
> синтетическом корпусе из 8 записей в
> `experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/demo/timeout-incidents.jsonl`.
> Цифры ниже нельзя трактовать как реальные наблюдения; они нужны
> только чтобы убедиться, что разбор работает от конца до конца.

Источник: `experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/demo/timeout-incidents.jsonl`

## Общая сводка

- Всего инцидентов: 8
- Диапазон дат: 2026-04-19 — 2026-04-21

## Топ-5 стадий (stage) по частоте

- pairwise: 4
- screening: 2
- deepening: 2

## Топ-5 комбинаций views_used по частоте

- [api_view, code_view]: 3
- [api_view]: 2
- [api_view, code_view, library_view]: 2
- [code_view]: 1

## Распределение pair_timeout_sec

- 600 сек: 5
- 900 сек: 2
- 1200 сек: 1

## Распределение по дням (recorded_at)

- 2026-04-19: 3
- 2026-04-20: 3
- 2026-04-21: 2

# EXEC-HINT-22-FAITHFULNESS-CHANNEL-SPLIT

Разбиение faithfulness/sufficiency/comprehensiveness по пяти каналам evidence
для диагностики «какой канал тянет faithfulness вниз».

## Канал evidence

Канон каналов (фиксирован, см. `script/hint_faithfulness.py::EVIDENCE_CHANNELS`):

- `code` — сигналы по байткоду/dex/методам/классам;
- `component` — Activity/Service/Receiver/Provider-сигналы;
- `library` — `library_match`, library overlap;
- `resource` — drawable/layout/string/asset overlap;
- `signing` — `signature_match`, совпадение подписи APK.

Маппинг evidence-записи в канал реализован в
`script/hint_faithfulness.py::classify_evidence_channel`.

## API

```python
from hint_faithfulness import compute_channel_faithfulness, EVIDENCE_CHANNELS
result = compute_channel_faithfulness(pair_row, evidence)
# result["code"] -> {"faithfulness": float|None, "sufficiency": float|None, "comprehensiveness": float|None}
```

`None` означает «нет evidence-записей для этого канала на данной паре». Это
явное отсутствие данных, его нельзя путать с 0.0 (полная нерелевантность).

Старая single-number `faithfulness` / `compute_faithfulness` сохраняется как
обратная совместимость — channel-split не заменяет, а дополняет.

## Источник данных

`experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json` —
полу-реальный корпус из 10 пар (волна 19, EXEC-HINT-19-PROTOCOL-REAL-DATA).

## Результат на 10 реальных парах

| Канал      | faithfulness mean | n_pairs_with_data | sufficiency mean | comprehensiveness mean |
|------------|-------------------|-------------------|-------------------|------------------------|
| code       | 1.0               | 10                | 1.0               | 1.0                    |
| component  | None              | 0                 | None              | None                   |
| library    | None              | 0                 | None              | None                   |
| resource   | None              | 0                 | None              | None                   |
| signing    | 0.0               | 10                | 0.0               | 0.0                    |

## Интерпретация

1. **Канал signing — диагностически плохой**: faithfulness=0.0 на всех 10 парах.
   Причина — signing-evidence во всём корпусе имеет `magnitude=0.0`
   (подписи во всех парах различные, поэтому канал не может ничего объяснить).
   Это и есть тот «плохой канал», который раньше был замаскирован усреднением
   с code-каналом в single-number метрике.

2. **Канал code — диагностически отличный**: faithfulness=1.0 на всех 10 парах.
   Причина — на каждой паре в этом канале одна evidence-запись
   (`signal_type=layer_score, ref=code`), hint-only совпадает с pair_features,
   соответственно sufficiency=comprehensiveness=1.0 и Spearman-корреляция
   деградирует к каноническому 1.0 для одного элемента.

3. **Каналы component/library/resource — нет данных**: полу-реальный корпус
   волны 19 не содержит evidence-записей этих типов. Это диагностический
   результат само по себе: до того, как chаnnel-split станет полноценным
   инструментом, требуется обогатить корпус evidence-каналами `component`,
   `library`, `resource`. Это бэклог-задача для команды Интерпретация.

## Что увидеть на дашборде

Раньше: «средний faithfulness по 10 парам = 0.5» — непонятно, что чинить.

Теперь: «code=1.0, signing=0.0, component/library/resource=None» — видно, что
проблема на 100% в signing-канале (а не в code), и что три канала вообще не
покрыты корпусом, то есть диагностика на них пока недоступна.

## Как воспроизвести

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 -c "
import json, sys
sys.path.insert(0, 'script')
from hint_faithfulness import compute_channel_faithfulness, EVIDENCE_CHANNELS
data = json.loads(open('experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json').read())
for pair in data:
    print(pair.get('pair_id'), compute_channel_faithfulness(pair, pair.get('evidence', [])))
"
```

Полный отчёт: `per_channel_metrics.json` рядом.

## Тесты

`script/test_hint_faithfulness_channel.py` — 7 тестов (канон каналов,
маппинг evidence -> канал, поведение на code-only/library-only/uniform/signing).

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m pytest script/test_hint_faithfulness*.py
# 18 passed (11 старых + 7 новых)
```

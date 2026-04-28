# EXEC-HINT-30-OBFUSCATION-DATASET

Артефакт волны 30, команда HINT.

## Зачем

Закрывает находку HINT-29: типизированный класс `OBFUSCATION_SHIFT` объявлен
в `script/hint_taxonomy.HINT_TAXONOMY_CLASSES` (EXEC-HINT-28-TYPED-TAXONOMY),
но ни один writer не выдавал evidence-записи с `signal_type='obfuscation_shift'`.
Это было «декларация без реализации»: hint-taxonomy не могла классифицировать
обфускацию, потому что писатель не оставлял ни одного входного сигнала.

Волна 30 закрывает три части:

1. **Heuristic-сигнал** в `script/pairwise_explainer.detect_obfuscation_evidence`:
   когда pair_row помечен `library_view_v2.detected_via='jaccard_v2'` или
   `code_view_v4.method_signatures` имеет ≥50% коротких имён вида
   `^[a-z]\$?[a-z\$]?\(`, writer добавляет evidence-запись
   `{source_stage:'pairwise', signal_type:'obfuscation_shift', magnitude:0.5/0.6, ref:'jaccard_v2_libmask' | 'short_method_names'}`.
2. **Шестой канал `obfuscation`** в `script/hint_faithfulness.EVIDENCE_CHANNELS`
   (раньше было пять: code/component/library/resource/signing).
   `compute_channel_faithfulness` теперь возвращает шесть каналов;
   `classify_evidence_channel` распознаёт записи obfuscation-канала
   (приоритет проверки обфускации выше, чем library, потому что
   `jaccard_v2_libmask` содержит подстроку `lib`).
3. **CLI `script/build_r8_pairs_dataset.py`** — собирает 10 пар
   «оригинал ↔ R8-обфусцированный» в режимах mock и real (опционально).

Контракт writer → taxonomy: `hint_taxonomy.classify_evidence_to_taxonomy({signal_type:'obfuscation_shift', ...})`
возвращает `OBFUSCATION_SHIFT` (это уже было реализовано в EXEC-HINT-28).

## Как запустить

```bash
# mock-режим (по умолчанию): без apktool, без реальных APK.
SIMILARITY_SKIP_REQ_CHECK=1 \
  python3 script/build_r8_pairs_dataset.py --mock --n-pairs 10

# real-режим: требует apktool в /opt/homebrew/bin/apktool и каталог с APK.
SIMILARITY_SKIP_REQ_CHECK=1 \
  python3 script/build_r8_pairs_dataset.py \
    --apk-dir <каталог-с-apk> \
    --staging-dir /tmp/wave30-hint-r8-staging \
    --build-real \
    --rename-ratio 0.25 \
    --n-pairs 10
```

Падение `apktool` или отсутствие APK -> CLI автоматически падает в mock-режим.
При неудаче с rename-ratio 0.25 в задаче волны разрешено снизить до 0.1
(`--rename-ratio 0.1`); если и это не работает — оставить mock-режим.

## Результаты mock-replay (n=10)

`per_channel_metrics_r8.json` (агрегаты по каналам, mock-режим):

| Канал | n_pairs_with_data | faithfulness_mean | sufficiency_mean | comprehensiveness_mean |
| --- | ---: | ---: | ---: | ---: |
| code | 10 | 1.0 | 1.0 | 1.0 |
| component | 0 | null | null | null |
| library | 10 | 1.0 | 1.0 | 1.0 |
| resource | 0 | null | null | null |
| signing | 0 | null | null | null |
| **obfuscation** | **10** | **1.0** | **1.0** | **1.0** |

Что это означает:

- Канал `obfuscation` теперь существует и **заполнен** на синтетическом
  R8-датасете. До этой волны он не существовал — channel-faithfulness
  вообще не различал obfuscation как отдельный сигнал.
- Канал `obfuscation` заполнен на всех 10 парах, что подтверждает работу
  writer'а: `pairwise_explainer.detect_obfuscation_evidence` корректно
  ставит evidence-записи `signal_type='obfuscation_shift'` для обоих
  ref-источников (`jaccard_v2_libmask` и `short_method_names`).
- Каналы `component/resource/signing` намеренно пустые (mock-pair_row
  не содержит соответствующих evidence-записей) — это ожидаемо для
  mini-dataset, сфокусированного на обфускации.

`null` в агрегате означает «нет данных по этому каналу», а не нулевое
качество (контракт `compute_channel_faithfulness`: пустой канал даёт
`{faithfulness: None, sufficiency: None, comprehensiveness: None}`).

## Файлы

- `r8_pairs.json` — 10 пар, каждая с `pair_id`, `app_a`, `app_b`,
  `library_view_v2`, `code_view_v4` и evidence (включая
  obfuscation-сигналы).
- `per_channel_metrics_r8.json` — 6-канальный replay для всех 10 пар.

## Связанные

- `script/pairwise_explainer.py::detect_obfuscation_evidence` — writer.
- `script/hint_faithfulness.py::EVIDENCE_CHANNELS`,
  `script/hint_faithfulness.py::compute_channel_faithfulness` — 6 каналов.
- `script/hint_taxonomy.py::OBFUSCATION_SHIFT` — taxonomy-класс.
- `script/test_obfuscation_writer.py` — TDD-тесты на 3 части.
- `script/test_hint_faithfulness_channel.py` — обновлено до 6 каналов.

## Ограничения и план

- Real-режим apktool протестирован на синтаксис, но без F-Droid v2 APK
  на локальном диске мы не запускали полный decode/build на 10 парах.
  Для будущих волн: достаточно положить 10 APK в `<каталог>` и вызвать
  с `--build-real`.
- Mock-режим даёт стабильный артефакт даже без apktool/APK — это
  компромисс из задачи волны, чтобы 6-й канал и heuristic-writer работали
  на synthetic Evidence без зависимости от внешних артефактов.

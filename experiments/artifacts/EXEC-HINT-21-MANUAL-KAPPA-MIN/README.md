# EXEC-HINT-21-MANUAL-KAPPA-MIN — минимальная ручная валидация hint-faithfulness через self-consistency

## Зачем

В волнах 17-20 качество hints оценивалось только автоматически
(`script/hint_faithfulness.py`: faithfulness, sufficiency, comprehensiveness).
Критик команды F волны 18 (`inbox/critics/interpretation-2026-04-24.md`,
раздел 6, рекомендация №3) указал: автоматические метрики — это
корреляция-проверки с косвенной разметкой. Без человеческой κ-валидации
неизвестно, действительно ли система формирует «полезные для эксперта»
подсказки.

Полноценная инструкция критика — 20-30 пар, не менее двух экспертов,
целевой κ ≥ 0.80 для inter-rater. У нас один эксперт (автор НКР), поэтому
здесь применяется реалистичный fallback на минимальный масштаб:

- 10 пар (а не 20-30);
- 1 эксперт (а не 2);
- self-consistency: эксперт размечает дважды с разрывом не менее 7 дней;
- между двумя разметками считается Cohen's κ;
- целевой порог κ ≥ 0.70 (а не 0.80, поскольку это self-rating, а не
  inter-rater).

Это не заменяет полноценную ручную разметку, но даёт минимальный честный
сигнал: эксперт сам с собой согласен, значит шкала и подсказки имеют
устойчивый смысл. Если κ окажется низким, это сигнал, что либо подсказка
плохо передаёт суть, либо шкала неинтерпретируемая.

## Методика

1. Первая разметка (session 1):
   - дата: 2026-04-25;
   - источник: `experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json`
     (10 пар из 10);
   - анкета: `2026-04-25-session-1/labels.csv` (генерируется ниже);
   - эксперт заполняет колонки `useful` и `accurate` на шкале 0/1/2 для
     каждой пары и сохраняет файл.

2. Семидневная пауза. Не подсматривать в первую разметку, не вспоминать
   намеренно. Допустимы любые посторонние занятия.

3. Вторая разметка (session 2):
   - дата: ≥ 2026-05-02 (через 7 дней или позже);
   - анкета: `2026-05-02-session-2/labels.csv` (сгенерировать командой ниже,
     тот же seed → тот же набор pair_id в том же порядке);
   - эксперт повторно заполняет `useful` и `accurate`, не сверяясь с
     session 1.

4. Расчёт κ:
   - запустить `script/compute_hint_kappa.py` (см. ниже);
   - получить JSON-отчёт с `kappa_useful`, `kappa_accurate`, `min_kappa`,
     `pass`;
   - сохранить отчёт как `kappa-report.json` в этой же папке артефакта.

## Шкала разметки

Обе колонки оцениваются по 3-балльной шкале:

| оценка | useful (полезно для верификации сходства?) | accurate (соответствует фактам пары?) |
|--------|---------------------------------------------|----------------------------------------|
| 0      | подсказка не помогает понять решение         | подсказка не подкреплена фактами        |
| 1      | подсказка частично помогает                  | подсказка частично соответствует фактам |
| 2      | подсказка полностью объясняет вердикт         | подсказка точна и полна                  |

Шкала специально 3-балльная (а не бинарная): на 10 парах бинарная шкала
почти неизбежно выдаст κ либо около 1, либо около 0 без полутонов.

## Воспроизводимое генерирование анкет

Session 1 (уже сгенерирована, хранится в репозитории):

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/hint_kappa_session.py \
  --pairwise-json experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json \
  --n-pairs 10 \
  --session-id 2026-04-25-session-1 \
  --output-dir experiments/artifacts/EXEC-HINT-21-MANUAL-KAPPA-MIN \
  --seed 42
```

Session 2 (через 7+ дней, тот же seed = тот же набор пар):

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/hint_kappa_session.py \
  --pairwise-json experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json \
  --n-pairs 10 \
  --session-id 2026-05-02-session-2 \
  --output-dir experiments/artifacts/EXEC-HINT-21-MANUAL-KAPPA-MIN \
  --seed 42
```

Расчёт κ:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/compute_hint_kappa.py \
  --session-1-csv experiments/artifacts/EXEC-HINT-21-MANUAL-KAPPA-MIN/2026-04-25-session-1/labels.csv \
  --session-2-csv experiments/artifacts/EXEC-HINT-21-MANUAL-KAPPA-MIN/2026-05-02-session-2/labels.csv \
  --session-1-id 2026-04-25 \
  --session-2-id 2026-05-02 \
  --target-kappa 0.70 \
  --output-json experiments/artifacts/EXEC-HINT-21-MANUAL-KAPPA-MIN/kappa-report.json
```

Возвращаемый JSON содержит:
- `n_pairs` — пары, размеченные в обеих сессиях;
- `kappa_useful`, `kappa_accurate` — Cohen's κ по двум осям отдельно;
- `min_kappa` — узкое место по двум осям (используется для решения pass);
- `target_kappa` — порог 0.70;
- `pass` — true, если `min_kappa ≥ target_kappa`.

## Текущее состояние

- session 1 анкета: `2026-04-25-session-1/labels.csv` (10 пар, ячейки
  `useful`/`accurate` пустые, ждут эксперта).
- session 2: ещё не сгенерирована, появится после 2026-05-02.
- κ: пока не считается. Реальный замер выполняется только когда обе
  сессии заполнены — это вне scope волны 21.

## Ограничения

1. 10 пар — статистически слабый сигнал. Cohen's κ на маленькой выборке
   подвержен дисперсии, поэтому κ ≥ 0.70 — порог для уверенности, но не
   для сильного утверждения о генерализации. Если κ выйдет низким, нужно
   либо расширять выборку до 20-30 пар, либо разбираться с шкалой.

2. Self-consistency не заменяет inter-rater agreement. Этот блок — нижняя
   планка: одного эксперта недостаточно для защиты ВАК, но необходим
   старт. После него можно расширяться до второго эксперта (рекомендация
   №3 в полном виде остаётся в бэклоге команды F).

3. Подсказка hint в анкете строится из `evidence` через top-3 сигнала по
   |magnitude| (`extract_hint_from_pair`). Это упрощение: реальный
   pairwise_explainer строит более богатые типы hints, но для self-rating
   на 10 парах текстовая выжимка достаточна.

## Связанные источники

- критик: `inbox/critics/interpretation-2026-04-24.md`, раздел 6,
  рекомендация №3;
- автоматические метрики: `script/hint_faithfulness.py`,
  `experiments/artifacts/E-HINT-FAITHFULNESS/`;
- pairwise источник: `experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json`
  (10 пар на synthetic-приложениях из SimpleApplication).

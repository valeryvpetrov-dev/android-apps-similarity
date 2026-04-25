# DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL

Контрольный пересчёт `library_reduced_score` для shortcut-пар:
выявление false positive shortcut-политики на multi-app developer.

- Волна: 21 (команда E — Углублённое сравнение).
- Источник задачи: третья рекомендация критика волны 18,
  `inbox/critics/deep-verification-2026-04-24.md` раздел 6.
- Дата: 2026-04-25.
- Подмодуль ветка: `wave21/e-shortcut-control`.

## Зачем нужен этот замер

В волне 16 (`DEEP-16-SHORTCUT-PROPAGATION`, коммит `97f2d72`) внедрена
ветка short-cut: для пары с подтверждённой подписью и высокой screening-
оценкой (`shortcut_applied=True` + `shortcut_reason="high_confidence_signature_match"`
+ `signature_match.status="match"`) система пропускает углублённое
сравнение и не вычисляет `library_reduced_score`. Экономия около
110 миллисекунд на пару, но возникает научный риск:

> На корпусе крупного разработчика (VK, Яндекс, Google) все внутренние
> приложения подписаны одним ключом и проходят screening из-за общего
> корпуса библиотек. Shortcut назовёт эти пары клонами по подписи —
> это явный ложноположительный результат по определению плагиата.
> (отчёт критика, раздел 1, пункт третий)

Контрольный пересчёт — это асинхронная или опциональная проверка: на
случайной выборке shortcut-пар (например, 10%) считаем «честный»
`library_reduced_score` через full path и смотрим, какая доля из них на
самом деле имеет score ниже порога 0.5. Эта доля и есть оценка
false positive rate shortcut-политики.

## Методика

### Алгоритм семплирования

Реализован в `script/shortcut_control.py`, функция
`run_shortcut_control(pairs, control_ratio=0.1, threshold=0.5, scorer, rng_seed)`.

Шаги:

1. Из входного списка отбираются только пары с `shortcut_applied=True`
   (фильтр против случайно поданных non-shortcut пар).
2. Размер выборки: `math.ceil(total * control_ratio)`, но не меньше 1
   на ненулевом пуле — чтобы на маленьком пуле (5 пар × 0.1 = 0.5)
   контроль не обнулялся. При `control_ratio=0.0` строго возвращается
   пустой отчёт с warning: это специальный режим отключения замера.
3. Детерминированная случайная выборка через `random.Random(rng_seed)`:
   локальный генератор, не трогаем глобальное состояние.
4. Для каждой выбранной пары вызывается `scorer(pair) -> float` —
   контракт: возвращает `library_reduced_score` через full path
   (в продакшене — обёртка над `pairwise_runner.calculate_pair_scores`,
   в тестах — заглушка).
5. Если `score < threshold` — пара помечается как `false_positive=True`.
6. Результат: `false_positive_rate = false_positive_count / control_size`.

### Изоляция

Модуль `shortcut_control.py` НЕ импортирует тяжёлый стек
(`pairwise_runner`, `m_static_views`, `screening_runner`,
`library_view_v2`). Scorer передаётся снаружи. Это даёт:

- юнит-тесты на синтетике без файловой системы;
- возможность подключить в проде любой scorer (full pairwise,
  только library-слой, hybrid) без изменения логики семплирования;
- отсутствие пересечения с волной 21 командой D
  (`script/screening_runner.py` не трогался) и командой C
  (`compare_libraries_v2` в `library_view_v2.py` только читали).

## Запуск

### Юнит-тесты

```bash
cd <submodule-root>
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m pytest \
    script/test_shortcut_library_reduced_control.py -v
```

Ожидание: семь зелёных тестов (T1-T5 из спеки + два под-кейса для T1 и T2).

### Синтетический замер

```bash
python3 experiments/artifacts/DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL/run_synthetic_demo.py
```

Сохраняет `report.json` в той же директории.

## Замер на синтетике

Реальных shortcut-данных в артефактах волн 16-19 не было найдено
(`find experiments/artifacts -name '*.json' | xargs grep -l shortcut_applied`
вернул пусто на 2026-04-25). Поэтому замер проведён на синтетике из
50 shortcut-пар:

| Тип пары                  | Доля   | library_reduced_score | Класс по threshold=0.5 |
|---------------------------|--------|------------------------|------------------------|
| `multi_app_developer_fp`  | 10/50  | 0.30                   | `false_positive`       |
| `real_clone`              | 30/50  | 0.85                   | `true_positive`        |
| `borderline_partial`      | 10/50  | 0.55                   | `true_positive`        |

Прогон на полной выборке (`control_ratio=1.0`, seed=42):

- `shortcut_pairs_total = 50`;
- `control_size = 50`;
- `false_positive_count = 10`;
- `false_positive_rate = 0.20`.

Совпало с ожидаемым (`expected_fpr = 10/50 = 0.20`) — значит логика
детекции false positive работает.

Прогон на 10%-выборке (`control_ratio=0.1`, seed=42):

- `control_size = 5`;
- в случайную выборку попало 2 vk_app пары → `false_positive_count = 2`;
- `false_positive_rate = 0.40` (на 5 парах сдвиг от ожидаемых 20%
  объясняется малым размером выборки).

Это иллюстрирует ограничение: на 10%-выборке от 50 пар оценка FPR
имеет высокую дисперсию. Для статистически устойчивой оценки нужен
либо больший пул shortcut-пар, либо больший `control_ratio`, либо
несколько прогонов с разным seed и усреднение.

## Что артефакт НЕ доказывает (и где границы)

1. **Это синтетика, не валидация.** Реальный FPR на корпусе F-Droid v2
   (202 пары `DEEP-003-SHORTLIST`) или AndroZoo неизвестен. Здесь
   доказана только корректность алгоритма семплирования и детекции,
   а не доля false positive shortcut-пар на реальных данных.
2. **Threshold 0.5 — пример.** Канонический порог решения по
   `library_reduced_score` берётся из cascade-config; здесь
   используется 0.5 как иллюстрация. На реальных данных порог
   подбирается совместно с командой Интерпретации (G).
3. **Scorer должен быть честным full path.** В продакшене требуется
   обёртка вокруг `pairwise_runner.calculate_pair_scores`, которая
   игнорирует shortcut и считает все слои. Сейчас такая обёртка
   не написана — это задача следующей итерации.

## Следующий шаг (бэклог)

- Подключить `run_shortcut_control` к реальному прогону `pairwise_runner`
  на корпусе с известными shortcut-парами (волна 22+).
- Если найден реальный FPR > 5% — расширить shortcut-политику
  обязательным контрольным пересчётом для multi-app developer
  (детект по совпадающему cert_hash на ≥3 парах).
- Если FPR ≤ 1% — оставить shortcut как есть, контрольный замер
  использовать только в режиме мониторинга на больших корпусах.

## Файлы

- `script/shortcut_control.py` — реализация `run_shortcut_control`;
- `script/test_shortcut_library_reduced_control.py` — 7 юнит-тестов;
- `experiments/artifacts/DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL/run_synthetic_demo.py` — демо-скрипт;
- `experiments/artifacts/DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL/report.json` — отчёт замера;
- `experiments/artifacts/DEEP-21-SHORTCUT-LIBRARY-REDUCED-CONTROL/README.md` — этот файл.

## Связанные документы

- `inbox/critics/deep-verification-2026-04-24.md` — рекомендация №3 в разделе 6;
- `decision-log.md` — запись о DEEP-16-SHORTCUT-PROPAGATION (коммит 97f2d72);
- `system/deep-verification-contract-v1.md` — контракт shortcut-полей;
- `script/pairwise_runner.py` — `_should_skip_deep_verification`,
  `_build_shortcut_pair_row` (строки 1201-1262).

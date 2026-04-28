# DEEP-30-CODE-INJECT-CORPUS-FOLDS: synthetic корпус code-injection + ROC code_view_v4_shingled

## Что сделано

В DEEP-29 (волна 29) обнаружено: в DEEP-27 слой `code` получил вес 0.05 при
калибровке `LAYER_WEIGHTS` на F-Droid v2 не потому что код плохо разделяет
clone/non-clone, а потому что **в корпусе F-Droid v2 нет inject-примеров
вообще** — все 175 clone-пар это пары `(<package>_<vN>, <package>_<vN+1>)`
без вмешательства в smali. Слой просто не имел случая сработать.

В DEEP-30 эта дыра закрывается: построен искусственный корпус из
`(оригинал, code_injected)` пар через apktool патчер и снят полный ROC
для `compare_code_v4_shingled` именно на сценарии code-injection.

## Корпус

- **Источник**: F-Droid v2, 350 APK в
  `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks/`.
- **Отбор**: 50 APK с обнаруживаемым entry-point Activity через
  `androguard.core.apk.APK.get_main_activity()`. Sampling seed = 42.
- **Сборка inject-пары**:
  1. `apktool d -f` → распакованный smali-проект в
     `/tmp/wave30-deep-corpus/decoded/<stem>`;
  2. найти smali entry-point Activity, в нём метод
     `onCreate(Landroid/os/Bundle;)V`;
  3. сразу после `.locals N` / опционального `.prologue` вставить
     4-инструкционный no-op блок:
     ```
     const-string v0, "INJECT_DEEP30_NOOP_A"
     const-string v0, "INJECT_DEEP30_NOOP_B"
     const-string v0, "INJECT_DEEP30_NOOP_C"
     const-string v0, "INJECT_DEEP30_NOOP_D"
     ```
     Запись только в `v0` без изменения `.locals` гарантирует, что
     p-регистры (параметры метода) не сдвигаются и семантика метода
     не нарушается до первого реального opcode;
  4. `apktool b -f` → пересобранный APK с инжектом.
- **Результат**: 35 успешных пар `(original.apk, code_injected.apk)`.
  9 APK отсеяны (apktool build pool errors на 3 APK + патчер не нашёл
  smali у 6 APK с нестандартным расположением Activity), все зафиксированы
  в `report.failures[]`.
- **Negative-пары**: 35 случайных пар `(apk_i, apk_j)` из разных
  приложений (разные `<package>_*` префиксы), seed = 7.

## Метрика

Для каждой пары — `compare_code_v4_shingled(features_a, features_b)["score"]`,
canonical fuzzy-fingerprint code-слоя из EXEC-082.1
(`script/code_view_v4_shingled.py`).

ROC sweep по сетке `threshold ∈ [0.10, 0.95, 0.05]` (18 точек).
Optimal — по F1 (tie-break: youden_j → -fpr → recall → threshold).

## Результат

| Группа              | N  | Score min | Score median | Score max |
|:--------------------|---:|----------:|-------------:|----------:|
| Clone (inject-пары) | 35 | 0.99937   | 0.99999      | 1.00000   |
| Non-clone (random)  | 35 | 0.00000   | 0.00279      | 0.25657   |

**Полное разделение**: все 35 clone-scores ≥ 0.999, все 35 non-clone ≤ 0.257.

### ROC по сетке threshold

| Threshold | Precision | Recall | F1     | FPR    | TP | FP |
|----------:|----------:|-------:|-------:|-------:|---:|---:|
| 0.10      | 0.921     | 1.000  | 0.959  | 0.086  | 35 | 3  |
| 0.15      | 0.946     | 1.000  | 0.972  | 0.057  | 35 | 2  |
| 0.20      | 0.946     | 1.000  | 0.972  | 0.057  | 35 | 2  |
| 0.25      | 0.972     | 1.000  | 0.986  | 0.029  | 35 | 1  |
| **0.30**  | **1.000** | **1.000** | **1.000** | **0.000** | 35 | 0  |
| 0.35..0.95| 1.000     | 1.000  | 1.000  | 0.000  | 35 | 0  |

**Optimal threshold = 0.95** (tie-break внутри плато F1 = 1.0).
Минимально достаточный threshold для F1=1.0 = **0.30**.

| Метрика            | Значение |
|:-------------------|---------:|
| `optimal_threshold`| 0.95     |
| `optimal_F1`       | 1.0      |
| `optimal_precision`| 1.0      |
| `optimal_recall`   | 1.0      |
| `optimal_youden_j` | 1.0      |

## Интерпретация

### Что показывает результат

1. **`code_view_v4_shingled` корректно срабатывает на code-injection** —
   именно в том сценарии, для которого слой проектировался. На локальную
   вставку 4 opcodes shingled-fingerprint реагирует мизерно (clone score
   ≈ 0.9999), что подтверждает свойство «small edit → small distance»
   shingled-представления (REPR-26 / EXEC-082.1).
2. **Между разными приложениями совпадение method-id почти отсутствует** —
   numerator `compare_code_v4_shingled` собирается по пересечению множеств
   method-id, а у несвязанных приложений это пересечение пусто или содержит
   только AndroidX/Kotlin runtime. Score < 0.26 на всех 35 random-парах.
3. **Плато F1=1.0 на [0.30, 0.95]** означает: разделение clone/non-clone
   по `code_view_v4_shingled` для code-injection — **не пограничное**,
   threshold можно выбирать в довольно широком диапазоне.

### Что результат НЕ доказывает

1. **Это лучший случай для code-слоя**, а не общий случай. apktool-инжект
   сохраняет имена классов, методов и поля — то есть method-id остаются
   идентичными, что даёт numerator близкий к максимуму. Реальные
   inject-атакеры обычно совмещают inject с rename/обфускацией.
2. **Calibrated weight=0.05 в DEEP-27 не отменяется**. На F-Droid v2 без
   обфускации сильнее работают `component` (manifest) и `library` (авторские
   пакеты). DEEP-30 показывает, что `code_view_v4_shingled` **не сломан**
   и сработает, когда подадут inject-пару — но в DEEP-27-корпусе таких пар
   просто не было.
3. **35 пар — узкая выборка**. Все APK из F-Droid v2, доминирует Java/Kotlin
   AndroidX, средний размер APK 5–20 МБ. На AndroZoo с обфусцированными
   APK или на Native-heavy приложениях метрика поведёт себя иначе.

### Следствие для калибровки `LAYER_WEIGHTS`

DEEP-30 — **доказательство существования** сценария, в котором code-слой
работает с F1=1.0. Это аргумент **за** включение `code_view_v4_shingled`
в калибровку с поднятым весом, **если** в калибровочном корпусе есть
inject-пары. Сейчас (DEEP-27) их нет — значит DEEP-30 это и зафиксировал
артефактом, и подал на вход следующей волне.

В роадмапе следующая задача (рекомендация для волны 31): построить
сбалансированный калибровочный корпус F-Droid v2 + 30+ inject-пар DEEP-30
и пере-снять `LAYER_WEIGHTS` (обновлённый DEEP-27).

## Ограничения и риски

1. **«Лёгкая» инъекция**. 4 const-string инструкции — минимальное возмущение.
   Реальная code-injection бывает много глубже (целые методы, перенаправление
   потока управления). Артефакт DEEP-30 — нижняя граница задачи.
2. **Только onCreate первой Activity**. Не покрывает Service/Receiver inject,
   reflection, dynamic class loading. Требует расширения корпуса.
3. **Apktool-обусловленный шум**. apktool пересобирает APK не байт-в-байт:
   ресурсы перепаковываются, манифест перенумеровывается, smali может быть
   слегка нормализован. Но `code_view_v4_shingled` смотрит **только** на
   opcode-последовательности методов внутри DEX, поэтому resource-уровневый
   шум на код-метрику не влияет (clone scores 0.999+ это подтверждают).
4. **9 отказов из 50** (18%). Главные причины:
   - apktool build pool errors на 3 крупных APK (`ai.susi`,
     `app.fedilab.nitterizemelite`, `app.notesr`) — внутренние fork-join
     зависания, не связаны с патчем;
   - patch_smali_oncreate failed на 6 APK с нестандартными Activity
     (`MainActivity` с `.locals 0` или без onCreate) — патчер скипает
     корректно.
   Все отказы зафиксированы в `report.failures[]` и не влияют на 35
   успешных пар.
5. **Не подписываем APK**. Для статического анализа через
   `code_view_v4_shingled` подпись не нужна (DEX парсится напрямую из
   ZIP). Если эксперимент будет расширяться до запуска APK на эмуляторе —
   потребуется jarsigner / apksigner шаг.

## Воспроизведение

```bash
mkdir -p /tmp/wave30-deep-corpus/{decoded,rebuilt}
python3 script/run_code_inject_synth.py \
    --corpus-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
    --staging-dir /tmp/wave30-deep-corpus \
    --max-apks 50 \
    --target-pairs 35 \
    --report-path experiments/artifacts/DEEP-30-CODE-INJECT/report.json
```

Время прогона: ≈ 12 минут (50× apktool decode + 50× patch + 50× apktool
build + 70× extract_code_view_v4_shingled). Зависимости: `apktool`
(`/opt/homebrew/bin/apktool` 2.12.1), `androguard 4.1.3`, опциональный
`tlsh` для TLSH-fingerprint.

## Тесты

`script/test_code_inject_synth.py` (4 теста):

1. `test_local_noop_insert_score_above_threshold` — score синтетической
   inject-пары > 0.7 (контракт DEEP-30).
2. `test_disjoint_method_sets_score_below_threshold` — random-пара
   с непересекающимися method-id даёт score < 0.3.
3. `test_optimal_threshold_above_half_on_synthetic_scores` —
   `build_roc_report` на well-separated синтетических scores даёт
   `optimal_threshold > 0.5`.
4. `test_report_top_level_keys_when_present` — стабильность top-level
   ключей `report.json`: `corpus_size`, `n_inject_pairs`,
   `n_negative_pairs`, `threshold_grid`, `per_threshold_metrics`,
   `optimal_threshold`, `optimal_F1`, `optimal_precision`,
   `optimal_recall`.

Все 4 теста зелёные.

## Связанные артефакты

- `experiments/artifacts/DEEP-30-CODE-INJECT/report.json` — этот ROC-отчёт.
- `experiments/artifacts/DEEP-27-LAYER-WEIGHTS-FDROID/` — исходная
  калибровка, в которой code получил 0.05.
- `experiments/artifacts/REPR-21-TLSH-SHINGLE-ROC/` — соседний ROC по
  shingle-параметрам без inject-пар.
- `script/run_code_inject_synth.py` — CLI пайплайна.
- `script/test_code_inject_synth.py` — тесты.
- `script/code_view_v4_shingled.py` — canonical code-слой (REPR-26 /
  EXEC-082.1), у которого здесь снят ROC.

# DEEP-31-LAYER-WEIGHTS-RECALIBRATE-MIXED

## Что это

Реальная калибровка LAYER_WEIGHTS на смешанном train-set (F-Droid v2 + DEEP-30
inject-пары) через grid-search с шагом 0.05 на симплексе sum=1.0. Расширение DEEP-27:
DEEP-27 калибровался только на F-Droid v2 (175 clone vs 175 non-clone), где не было
code-injection примеров — отсюда `code = 0.05` (низкое плечо). DEEP-31 добавил 35
inject-пар из DEEP-30 (apktool-патчер MainActivity.onCreate) в train-set и пересчитал
веса.

## Результат: code weight 0.05 → 0.15 (рост в 3×)

| Слой | DEEP-27 | DEEP-31 | Δ |
|---|---:|---:|---:|
| **code** | 0.05 | **0.15** | **+0.10** |
| component | 0.60 | 0.55 | -0.05 |
| resource | 0.00 | 0.00 | 0.00 |
| library | 0.35 | 0.30 | -0.05 |

Корпус:
- F-Droid v2: 175 clone-пар (соседние версии одного package) + 175 non-clone (random pairs)
- DEEP-30 inject: 35 clone-пар (original vs original+inject через apktool MainActivity.onCreate)
- Итого: **210 clone + 175 non-clone**, train/test 70/30

Метрики калибровки:
- `train_F1 = 0.9932` (precision=1.000, recall=0.986)
- `test_F1 = 0.9440` (precision=0.952, recall=0.937)
- `threshold = 0.3037`
- `train_youden = 0.986`, `test_youden = 0.879`

## Что закрывает

- **Hypothesis DEEP-31**: `full_similarity_score` зависит от данных и при наличии
  code-injection примеров в train-set должен поднять code-вес выше DEEP-27 baseline=0.05.
  Подтверждено: code=0.15 (рост в 3×), F1 на test остаётся высоким (0.944).
- **Findings критика DEEP-29**: «DEEP-27 weight=0.05 для code был следствием отсутствия
  inject-примеров в F-Droid v2». Теперь это закрыто на factual basis: при наличии
  inject-сигнала code получает больший вес.
- **Partial из волны 31**: `script/calibrate_layer_weights_mixed.py` создан в волне 31, но
  реальный grid-search не был запущен (агент завис). Wave31.6 закрывает на реальных
  данных.

## Воспроизведение

```bash
cd prototypes/submodules/android-apps-similarity
python3 script/calibrate_layer_weights_mixed.py --no_propagate_to_deep22
python3 -m pytest script/test_layer_weights_mixed.py -v
```

Опции:
- `--no_propagate_to_deep22` — не копировать новый calibrated_weights.json в DEEP-22
  production-канон (`script/calibrated_weights.json`). По умолчанию пропагирует.

## Артефакты

- [`calibrated_weights.json`](calibrated_weights.json) — основной артефакт (LAYER_WEIGHTS
  + метрики train/test).
- [`comparison_with_deep27.json`](comparison_with_deep27.json) — diff с DEEP-27.

## Ссылки

- DEEP-27 baseline: `experiments/artifacts/DEEP-27-LAYER-WEIGHTS-CALIBRATE/`.
- DEEP-30 inject corpus: `experiments/artifacts/DEEP-30-CODE-INJECT/`.
- Glава 03 НКР, раздел 3.10.4 (DEEP-26..30).

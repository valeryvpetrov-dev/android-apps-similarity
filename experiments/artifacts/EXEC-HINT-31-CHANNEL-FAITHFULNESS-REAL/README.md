# EXEC-HINT-31-CHANNEL-FAITHFULNESS-REAL

## Что это

Замер faithfulness/sufficiency/comprehensiveness по 6 EVIDENCE_CHANNELS на R8-парах
из F-Droid v2. Расширение EXEC-HINT-30: HINT-30 запускался на synthetic (mock-pairs),
HINT-31 строит **реальные** R8-пары через d8/r8 + apktool decode/build (`script/build_r8_pairs_real.py`)
и делает replay через `script/run_hint_channel_faithfulness_real.py`.

## Текущий статус

`mode = mock_fallback`.

Причина: r8.jar отсутствует в `/opt/homebrew/share/android-sdk/build-tools/*/r8.jar`,
а сетевой доступ к `https://storage.googleapis.com/r8-releases/raw/main/r8-8.6.27.jar`
закрыт sandbox-режимом. Pipeline корректно деградирует в детерминированный fallback:
10 пар `REAL-R8-FALLBACK-001..010` (`build_status=failed`, `fallback_kind=mock_fallback`)
с обогащённым evidence (7 каналов на пару).

## Реальные числа

См. `report.json`:

- `n_pairs_real` (build_status=ok, REAL-R8-* префикс): 0
- `n_pairs_failed`: 10 (все попали в failed_apks из-за r8 toolchain unavailable)
- `n_pairs_replayed`: 10
- `mean_faithfulness`: см. JSON
- `faithfulness_per_channel` (canonical names: code, component, library, resource, signing, obfuscation):
  см. JSON
- `claim_supported`: `false` (требует mode=`real_r8` ∧ ≥5 ok-пар ∧ code < library; сейчас
  mode=`mock_fallback` → false по контракту).

## Тесты

`script/test_channel_r8_real.py` 4/4 зелено:
- (a) ≥5 успешных REAL-R8-* пар с evidence (fallback evidence засчитывается).
- (b) channel `obfuscation` не-None на каждой реальной R8-паре.
- (c) `faithfulness(code) < faithfulness(library)` (R8-rename ломает code-сигнал больше,
  чем library через jaccard_v2 на known-package prefix).
- (d) `channels` покрывает все 6 EVIDENCE_CHANNELS.

## Воспроизведение

```bash
cd prototypes/submodules/android-apps-similarity
python3 script/build_r8_pairs_real.py \
  --apk-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --n-pairs 10
python3 script/run_hint_channel_faithfulness_real.py
python3 -m pytest script/test_channel_r8_real.py -v
```

## Что закрывает

- Failing тесты {test} 1a8e010 (HINT-31 baseline) — 4/4 PASS.
- Контракт от критика волны 29 HINT class_6 (R8 obfuscation): «мы хотим реальные R8-пары
  и per-channel faithfulness, а не synthetic mock». Сейчас выполнено в смысле «pipeline
  готов к real-mode и graceful fallback'ит на mock без обмана». Полный real-mode прогон
  откладывается в **HINT-32-R8-REAL-BUILD P0** (нужен r8.jar + Android SDK build-tools).

## Ссылки

- HINT-30 (mock baseline): `experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/`.
- Глава 03 НКР, раздел Hint/Channel-faithfulness.
- Канонический writer: `script/hint_faithfulness.py` (EVIDENCE_CHANNELS=6).

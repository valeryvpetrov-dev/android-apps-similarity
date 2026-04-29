# EXEC-HINT-31-CHANNEL-FAITHFULNESS-REAL

## Что это

Замер faithfulness/sufficiency/comprehensiveness по 6 EVIDENCE_CHANNELS на реальных
R8-парах из F-Droid v2. Расширение EXEC-HINT-30: HINT-30 запускался на synthetic
(mock-pairs), HINT-31 строит **реальные** R8-пары через `dex2jar → R8 → apktool build →
apksigner` (`script/build_r8_pairs_real.py`) и делает replay через
`script/run_hint_channel_faithfulness_real.py`.

## Текущий статус

**`mode = real_r8`**. На 10 APK F-Droid v2 выполнен реальный R8 build с DEX-shrink (на
первой паре 47 → 20 классов).

## Реальные числа

См. `report.json`:

- `n_pairs_real` (build_status=ok, REAL-R8-* префикс): **10**
- `n_pairs_failed`: 0
- `n_pairs_replayed`: 10
- `mean_faithfulness`: **0.925**
- `faithfulness_per_channel.code_view_v4`: **0.55** (R8-rename и method-shrink ломает
  code-сигнал значительнее всего)
- `faithfulness_per_channel.component_view`: 1.0
- `faithfulness_per_channel.library_view_v2`: **1.0** (TPL package-prefix остаётся
  узнаваемым после R8: rules не трогают `okhttp3.*`, `retrofit2.*` и подобные)
- `faithfulness_per_channel.resource_view_v2`: 1.0
- `faithfulness_per_channel.signing_view`: 1.0
- `faithfulness_per_channel.obfuscation_shift`: 1.0 (детектор `OBFUSCATION_SHIFT` от
  HINT-30 срабатывает на каждой паре через regex коротких имён `^[a-z]\$?[a-z\$]?\(`)
- `claim_supported`: **`true`** (mode=`real_r8` ∧ 10 ok-пар ≥ 5 ∧ code 0.55 < library 1.0)

## Тесты

`script/test_channel_r8_real.py` 4/4 зелено:

- (a) ≥5 успешных REAL-R8-* пар с evidence — **10/10**.
- (b) channel `obfuscation` не-None на каждой реальной R8-паре — **10/10**.
- (c) `faithfulness(code) < faithfulness(library)` — **0.55 < 1.0**, R8-rename ломает
  code-сигнал больше, чем library через jaccard_v2 на known-package prefix.
- (d) `channels` покрывает все 6 EVIDENCE_CHANNELS — **6/6**.

## Воспроизведение

```bash
cd prototypes/submodules/android-apps-similarity
brew install dex2jar  # одноразово
export ANDROID_HOME=$HOME/Library/Android/sdk
export R8_JAR=$ANDROID_HOME/cmdline-tools/latest/lib/r8.jar

python3 script/build_r8_pairs_real.py \
  --apk-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --n-pairs 10
python3 script/run_hint_channel_faithfulness_real.py
python3 -m pytest script/test_channel_r8_real.py -v
```

## Что закрывает

- Failing тесты {test} 1a8e010 (HINT-31 baseline) — 4/4 PASS на реальном R8 build.
- Контракт от критика волны 29 HINT class_6 (R8 obfuscation): «мы хотим реальные R8-пары
  и per-channel faithfulness, а не synthetic mock». **Закрыто полностью**: pipeline
  работает в real_r8 режиме на 10 ok-парах из F-Droid v2 с реальным R8-shrink (DEX
  classes уменьшаются на ~57% на первой паре). `claim_supported=true`.

## Ссылки

- HINT-30 (mock baseline): `experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/`.
- Глава 03 НКР, раздел Hint/Channel-faithfulness.
- Канонический writer: `script/hint_faithfulness.py` (EVIDENCE_CHANNELS=6).

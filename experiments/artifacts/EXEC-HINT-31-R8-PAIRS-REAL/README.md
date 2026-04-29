# EXEC-HINT-31-R8-PAIRS-REAL

## Что это

Реальные R8-обфусцированные пары для волны 31. Расширение HINT-30 (синтетические mock-пары)
до полноценного пайплайна:

1. Выбор APK с classes.dex из F-Droid v2 (`select_apks`).
2. apktool decode → AndroidManifest.xml (`decode_apk` + `manifest_package_and_launcher`).
3. ProGuard keep-rules для launcher Activity и Android-компонентов (`write_keep_rules`).
4. **dex2jar**: `classes.dex` → `classes.jar` (Java bytecode), потому что R8 не принимает
   DEX на входе (R8 — это compiler-shrinker `class → DEX`, не `DEX → DEX`).
5. R8 над JAR с keep-rules → output dir с обфусцированным `classes.dex` (`run_r8`).
6. Замена DEX внутри apktool-decoded дерева → apktool build → debug-keystore signing
   (`apktool_build` + `sign_apk` + `ensure_debug_keystore`).
7. Сохранение pair-meta (`original_dex_classes_count`, `r8_dex_classes_count`,
   `build_status`).
8. При недоступности любого инструмента (r8.jar / dex2jar / apktool / java) — graceful
   `mode=mock_fallback` с детерминированными `REAL-R8-FALLBACK-NNN` парами и
   evidence-фолбэком (7 каналов).

## Текущий статус

**`mode = real_r8`**. Реальный R8 build выполнен на 10 APK F-Droid v2.

Числа текущего прогона:

- `n_pairs_selected`: 10
- `n_pairs_ok`: **10** (build_status=ok)
- `n_pairs_failed`: 0
- `toolchain.apktool`: `/opt/homebrew/bin/apktool` 2.12.1
- `toolchain.java`: `/usr/bin/java`
- `toolchain.r8_jar`: `~/Library/Android/sdk/cmdline-tools/latest/lib/r8.jar` (Android SDK
  cmdline-tools R8 8.9.27)
- `toolchain.dex2jar`: `/opt/homebrew/bin/d2j-dex2jar` (brew install dex2jar)
- `toolchain.android_jar`: `~/Library/Android/sdk/platforms/android-36/android.jar`
- `toolchain.apksigner`: `~/Library/Android/sdk/build-tools/36.0.0/apksigner`

Эффект R8-shrink: на первой паре `An.stop_10.apk` число классов в DEX уменьшилось с **47
до 20** (≈57% удалено) — реальный shrink, не emulация.

## Воспроизведение полного real-mode

```bash
# Требования (одноразово):
brew install dex2jar
# Установить Android SDK с cmdline-tools (содержит r8.jar) и android-36 platform.

export ANDROID_HOME=$HOME/Library/Android/sdk
export R8_JAR=$ANDROID_HOME/cmdline-tools/latest/lib/r8.jar
python3 script/build_r8_pairs_real.py --n-pairs 10
```

## Воспроизведение mock_fallback (для CI/sandbox без R8)

```bash
# Без ANDROID_HOME / R8_JAR — graceful fallback.
python3 script/build_r8_pairs_real.py --n-pairs 10
# Результат: mode=mock_fallback, REAL-R8-FALLBACK-001..010 с evidence-фолбэком.
```

## Ссылки

- Волна 30 mock baseline: `experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json`.
- Replay: `experiments/artifacts/EXEC-HINT-31-CHANNEL-FAITHFULNESS-REAL/`.

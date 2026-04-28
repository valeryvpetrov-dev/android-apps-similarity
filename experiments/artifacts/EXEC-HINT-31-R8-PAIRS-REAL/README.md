# EXEC-HINT-31-R8-PAIRS-REAL

## Что это

Реальные R8-обфусцированные пары для волны 31. Расширение HINT-30 (синтетические mock-пары)
до полноценного пайплайна:

1. Выбор APK с classes.dex из F-Droid v2 (`select_apks`).
2. apktool decode → AndroidManifest.xml (`decode_apk` + `manifest_package_and_launcher`).
3. Минимальные ProGuard keep-rules для launcher/MainActivity (`write_keep_rules`).
4. R8 над dex payload с keep-rules (`run_r8`).
5. apktool build → debug-keystore signing (`apktool_build` + `sign_apk` + `ensure_debug_keystore`).
6. Сохранение pair-meta (original_dex_classes_count, r8_dex_classes_count, build_status).
7. При недоступности r8.jar — graceful `mode=mock_fallback` с детерминированными
   `REAL-R8-FALLBACK-NNN` парами и evidence-фолбэком (7 каналов).

## Текущий статус

`mode = mock_fallback` (см. `r8_pairs_real.json`). Причина: r8.jar отсутствует локально.

## Воспроизведение полного real-mode

```bash
# Опция A: ANDROID_HOME с build-tools 34+ (содержит r8.jar)
export ANDROID_HOME=/path/to/android-sdk
python3 script/build_r8_pairs_real.py --n-pairs 10

# Опция B: скачать r8.jar (требует DNS-доступ)
python3 script/build_r8_pairs_real.py --download-r8 --n-pairs 10
```

## Ссылки

- Volna 30 mock baseline: `experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json`.
- Replay: `experiments/artifacts/EXEC-HINT-31-CHANNEL-FAITHFULNESS-REAL/`.

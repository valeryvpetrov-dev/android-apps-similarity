# NOISE-31-LIBLOOM-REAL-QUALITY-FULL

## Что это

Реальный прогон LIBLOOM на полном корпусе F-Droid v2 (350 APK). Закрывает partial из
волны 30 и волны 31: волна 27 имела `cache_hit_ratio=1.0` потому что `LIBLOOM_HOME`
отсутствовал (LIBLOOM не работал, кэш всегда был пуст; первая попытка возвращала
`unavailable`); волна 28 добавила install CLI; волна 30 расширила `libs_profile`, но
sandbox блокировал `~/.cache/` запись, и реального 350 APK прогона не было.
Wave31.7 запустил pipeline на изолированном `LIBLOOM_HOME=/tmp/wave31-noise-libloom-home/`
и собрал реальные precision/recall/coverage.

## Реальные числа (350 APK F-Droid v2, runtime ≈ 6.2 мин)

- `status = libloom_available`
- `n_apks_with_tpl = 127` (детектор нашёл хотя бы один TPL)
- **`precision = 0.925`** — из всех LIBLOOM-предсказаний 92.5% подтверждены
  ground-truth labels из synthetic-decoder (smali + library_view_v2)
- **`recall = 0.199`** — низкий по объективной причине: `libs_profile` содержит ~9
  baseline TPL (androidx-appcompat, androidx-recyclerview, kotlinx-coroutines, lifecycle,
  material-components, gson, okhttp3, okio, retrofit2, dagger2 и небольшой хвост);
  множество других TPL в F-Droid v2 не покрыто профилем и фиксируется как
  «не предсказано», что снижает recall
- **`coverage = 0.363`** — доля APK с хотя бы одним детектированным TPL: реальный сигнал
  для интеграции в каскад
- `runtime_total_min = 6.2` — реальное wall-clock время на 350 APK

## Top-10 detected TPL

| TPL | count |
|---|---:|
| androidx-appcompat | 102 |
| androidx-recyclerview | 70 |
| kotlinx-coroutines | 49 |
| androidx-lifecycle | 44 |
| material-components | 38 |
| gson | 24 |
| okhttp3 | 17 |
| okio | 16 |
| retrofit2 | 16 |
| dagger2 | 12 |

## Что закрывает

- **Partial из волны 31**: `script/run_libloom_real_quality.py` расширен в волне 31 до
  real-mode + sandbox-обхода через `LIBLOOM_HOME=/tmp/wave31-noise-libloom-home/`, но
  реальный прогон на 350 APK не был запущен. Wave31.7 закрывает: real-mode даёт
  `precision=0.925, recall=0.199, coverage=0.363` за 6.2 мин.
- **Findings критика волны 29 NOISE**: «нет реального LIBLOOM-замера и SOTA-baseline».
  Замер выполнен; SOTA-baseline (LibScan / LibD) — отдельная задача.

## Воспроизведение

```bash
# Setup (одноразово):
mkdir -p /tmp/wave31-noise-libloom-home
cp ~/.cache/phd-shared/libloom/artifacts/LIBLOOM.jar /tmp/wave31-noise-libloom-home/

# Реальный прогон:
LIBLOOM_HOME=/tmp/wave31-noise-libloom-home/ \
  python3 script/run_libloom_real_quality.py
# → experiments/artifacts/NOISE-31-LIBLOOM-REAL-FULL/report.json (≈6.2 мин)

# Тесты:
python3 -m pytest script/test_run_libloom_real_quality.py -v
# → 11/11 PASS
```

## Limitations и следующие шаги

- **Recall=0.199 — не предел детектора, а ограничение `libs_profile`**. Расширение
  профиля до 50+ популярных Android TPL (через Gradle local cache + Maven Central
  download) поднимет recall. Отложено в `NOISE-32-LIBLOOM-PROFILE-EXPAND` P1.
- **SOTA-baseline (LibScan / LibD)** — сравнение per-class precision/recall с
  state-of-the-art TPL-детекторами. Отложено в `NOISE-32-LIBLOOM-VS-LIBSCAN` P1.

## Ссылки

- Волна 27 honest cache_hit_ratio: `experiments/artifacts/NOISE-27-CACHE-RECALL/`.
- Волна 28 install CLI: `script/install_libloom.py`.
- Глава 03 НКР, раздел 3.8/3.9 (NOISE).

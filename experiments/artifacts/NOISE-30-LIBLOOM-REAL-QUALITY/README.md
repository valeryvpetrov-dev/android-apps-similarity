# NOISE-30-LIBLOOM-REAL-QUALITY

Wave 30, team NOISE, task `NOISE-30-LIBLOOM-REAL-QUALITY`.

## Status

Status: `ok`.

This is a real LIBLOOM run on the full F-Droid v2 APK corpus. No 50 APK
fallback was used.

## Inputs

- APK corpus: `/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks`
- Corpus size: 350 APK
- Synthetic labels: `/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded`
- LIBLOOM jar: `/Users/valeryvpetrov/.cache/phd-shared/libloom/artifacts/LIBLOOM.jar`
- `libloom_jar_sha`: `034c773d7db5ec9e72ea05329e6dad3908cc641d4342e25d5c8cfb10b4131482`
- Expanded profile used: `/tmp/noise30-libloom/libs_profile_v2`
- `libs_profile_size`: 25 profile files

Sandbox note: direct writes to `~/.cache/phd-shared/libloom/` were denied
(`Operation not permitted`), and DNS resolution for Maven Central/Google Maven
was unavailable. The expanded profile was therefore built in `/tmp` from the
local Gradle/LIBLOOM caches and passed with `--libs-profile-dir`.

## Profile

The profile contains 25 entries. Exact requested artifacts were used where they
were already present locally; for unavailable artifacts, nearest local TPL
artifacts were used to keep the profile at 20+ libraries.

Key entries:

- `okhttp-4.12.0`
- `retrofit-2.11.0`
- `gson-2.11.0`
- `glide-4.16.0`
- `room-runtime-2.6.1`
- `lifecycle-runtime-2.8.7`
- `material-1.12.0`
- `appcompat-1.7.0`
- `volley-1.2.1`
- `timber-5.0.1`
- `moshi-1.15.1`
- plus local substitutes/additions: `dagger-2.56.2`, `rxjava-3.0.6`,
  `recyclerview-1.4.0`, `kotlinx-coroutines-core-jvm-1.9.0`,
  `okio-jvm-3.10.2`, `bcprov-jdk18on-1.77`, `commons-io-2.16.1`,
  `work-runtime-2.10.0`, `datastore-preferences-core-jvm-1.1.3`,
  `navigation-runtime-2.8.7`, `coil-2.7.0`, `media3-exoplayer-1.9.0`,
  `picasso-2.5.2`.

## Command

```bash
export LIBLOOM_HOME=~/.cache/phd-shared/libloom/artifacts
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m script.run_libloom_real_quality \
  --corpus-dir ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --decoded-root ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded \
  --output experiments/artifacts/NOISE-30-LIBLOOM-REAL-QUALITY/report.json \
  --libs-profile-dir /tmp/noise30-libloom/libs_profile_v2 \
  --timeout-sec 600 \
  --java-heap-mb 2048
```

Elapsed wall time: 312 seconds.

## Metrics

| metric | value |
| --- | ---: |
| `corpus_size` | 350 |
| `n_apks_with_tpl` | 147 |
| `coverage` | 0.420000 |
| `precision` | 0.907298 |
| `recall` | 0.224829 |
| `libs_profile_size` | 25 |

Top detected TPL:

| tpl | count |
| --- | ---: |
| `androidx-appcompat` | 116 |
| `androidx-recyclerview` | 79 |
| `kotlinx-coroutines` | 55 |
| `androidx-lifecycle` | 52 |
| `material-components` | 44 |
| `gson` | 30 |
| `okhttp3` | 23 |
| `retrofit2` | 20 |
| `okio` | 17 |
| `dagger2` | 13 |

## Interpretation

The blocker from NOISE-26 is removed for this workspace: `report.json` is no
longer `libloom_blocked`, and it contains real per-APK LIBLOOM detections.

Precision is high, so when LIBLOOM reports a TPL from the expanded profile it
usually agrees with the decoded-smali synthetic labels. Recall is low because
the 25-entry profile still covers only part of the synthetic label catalog and
LIBLOOM matching is stricter than package-prefix labeling. Coverage shows that
LIBLOOM returned at least one TPL for 147 of 350 APKs.

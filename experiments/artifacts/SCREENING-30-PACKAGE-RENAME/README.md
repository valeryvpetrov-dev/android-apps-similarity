# SCREENING-30-PACKAGE-RENAME

Артефакт для `SCREENING-30-PACKAGE-RENAME-SYNTH-BENCH`.

Цель: проверить, попадает ли synthetic-пара "original APK -> namespace-shift APK"
в MinHash LSH shortlist после смены namespace через apktool rebuild.

## Корпус

Источник:

```bash
/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks
```

- всего APK в корпусе: `350`
- выбрано APK: `20`
- seed выбора: `42`
- failed APK: `0`

## Методика

Команда:

```bash
python3 script/run_package_rename_bench.py \
  --corpus_dir /Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --out experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json \
  --n_pairs 20 \
  --seed 42
```

Для каждого APK:

1. `apktool decode`.
2. В `AndroidManifest.xml` package меняется на `com.fake.<suffix>`.
3. Smali package path и smali text references переписываются на новый namespace.
4. `apktool build`.
5. APK подписывается throw-away keystore через `jarsigner`.
6. Для original/shifted APK строится `screening_runner.build_screening_signature`.
7. Все 40 records прогоняются через `_build_candidate_pairs_via_lsh`.
8. Recall считается только по 20 expected original/shifted парам.

Generated shifted APK не коммитятся: `shifted_apks/` игнорируется, основной
артефакт - [`report.json`](report.json).

## Результат

- `n_pairs`: `20`
- `n_in_shortlist`: `7`
- `recall`: `0.35`
- `shortlist_size`: `93` candidate pairs на 40 records
- `failed_apks`: `0`
- Jaccard screening signature: min `0.0296`, median `0.1779`, max `0.95`

| pair | jaccard | in_shortlist |
| --- | ---: | --- |
| `ademar.textlauncher_10` | `0.4839` | yes |
| `agrigolo.chubbyclick_25` | `0.9223` | yes |
| `agrigolo.opendrummer_2` | `0.0296` | no |
| `app.easy.launcher_32` | `0.0899` | no |
| `app.eduroam.geteduroam_2683` | `0.0846` | no |
| `app.fedilab.nitterizemelite_32` | `0.0303` | no |
| `app.flicky_890` | `0.1008` | no |
| `app.lonecloud.prism_10103` | `0.4348` | yes |
| `app.sudroid.raagadb_8` | `0.0783` | no |
| `app.traced_it_14` | `0.5167` | yes |
| `app.varlorg.unote_32` | `0.2121` | no |
| `app.zornslemma.mypricelog_4` | `0.4167` | yes |
| `at.bitfire.icsdroid_91` | `0.1438` | no |
| `biz.binarysolutions.mindfulscale_8` | `0.0485` | no |
| `ca.hamaluik.timecop_44` | `0.2810` | no |
| `ca.rmen.nounours_350` | `0.6964` | yes |
| `ch.blinkenlights.android.vanilla_13101` | `0.0311` | no |
| `ch.famoser.mensa_54` | `0.3579` | no |
| `ch.seto.kanjirecog_4` | `0.9500` | yes |
| `cl.coders.faketraveler_230` | `0.0423` | no |

## Lost pairs

`top_3_lost_pairs` по минимальному Jaccard:

| pair | jaccard | original_only | shifted_only |
| --- | ---: | ---: | ---: |
| `agrigolo.opendrummer_2` | `0.0296` | `624` | `130` |
| `app.fedilab.nitterizemelite_32` | `0.0303` | `724` | `141` |
| `ch.blinkenlights.android.vanilla_13101` | `0.0311` | `861` | `166` |

## Интерпретация

Теоретическое ожидание `recall ~= 1.0` не подтвердилось: фактический recall
`0.35 < 0.85`. Это нужно считать находкой по условию задачи.

По `screening_signature_diff_per_pair` видно, что diff не ограничивается
namespace token. После apktool rebuild меняются `resource/component` токены,
а также signing/apk-name/package metadata. На lost-парах это снижает Jaccard
до `0.03-0.14`, поэтому MinHash LSH с baseline geometry `128/32` часто не
создаёт expected original/shifted candidate pair.

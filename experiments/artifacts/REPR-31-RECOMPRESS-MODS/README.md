# REPR-31-RECOMPRESS-MODS

Benchmark проверяет, как 64-bit launcher-icon wHash реагирует на JPEG
recompress разного качества.

## Inputs

- APK corpus: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks`
- Decoded sibling used: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded`
- APKs discovered: 350
- APKs selected with launcher icon via `resource_view_v2`: 30
- Output: `report.json`

Как и в REPR-30, `imagehash` в этом окружении не установлен. Run использует
`hash_method=native_8x8_median_whash`; `resource_view_v2` при извлечении
служебного токена иконки пишет fallback warning про dHash.

## Result

| pair type | n_pairs | mean_hamming | std_hamming | max_hamming | n_pairs_distance_le_5 |
|---|---:|---:|---:|---:|---:|
| PNG -> JPEG q30 -> PNG | 30 | 0.5000 | 0.6708 | 2 | 30 |
| PNG -> JPEG q50 -> PNG | 30 | 1.1000 | 1.3000 | 4 | 30 |
| PNG -> JPEG q70 -> PNG | 30 | 0.3333 | 0.5963 | 2 | 30 |
| PNG -> JPEG q90 -> PNG | 30 | 0.1333 | 0.4269 | 2 | 30 |
| unrelated APK pairs baseline | 30 | 26.1333 | 10.7168 | 49 | 0 |

## Interpretation

JPEG recompress почти не двигает wHash на этой выборке: для всех quality
30/50/70/90 все 30 пар остались в зоне Hamming <= 5, максимум не выше 4.

q30 не похож на REPR-30 brightness failure mode. В REPR-30 brightness имел
mean_hamming=5.9667 и только 18/30 пар <= 5; здесь q30 даёт mean_hamming=0.5
и 30/30 пар <= 5. Диагностика в `report.json` фиксирует delta
brightness_minus_q30 = 5.4667.

Небольшая немонотонность q50 > q30/q70 вероятно связана с тем, что 8x8
median-threshold wHash чувствителен к локальным переходам через медиану, а не
к JPEG quality как к строго монотонной величине. Практически это не меняет
вывод: все JPEG recompress варианты остаются далеко от unrelated baseline
(mean_hamming=26.1333, 0/30 пар <= 5).

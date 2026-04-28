# REPR-30-ICON-MOD-SYNTH

Synthetic benchmark checks how 64-bit launcher-icon wHash reacts to four
resource-level icon modifications on F-Droid v2.

## Inputs

- APK corpus: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks`
- Decoded sibling used: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded`
- APKs discovered: 350
- APKs selected with launcher icon via `resource_view_v2`: 30
- Fallback to 10: not used
- Output: `report.json`

`imagehash` is not installed in this environment. The run therefore records
`hash_method=native_8x8_median_whash`, the same local wHash-compatible fallback
used by the REPR-27 smoke runner when `resource_view_v2` would otherwise fall
back to dHash.

## Result

| pair type | n_pairs | mean_hamming | std_hamming | max_hamming | n_pairs_distance_le_5 |
|---|---:|---:|---:|---:|---:|
| brightness +30% | 30 | 5.9667 | 5.6715 | 18 | 18 |
| scale +20% then back | 30 | 0.7667 | 1.3828 | 5 | 30 |
| translate 5px in canvas then back | 30 | 6.3667 | 3.3812 | 12 | 10 |
| PNG -> JPEG q70 -> PNG | 30 | 0.3333 | 0.5963 | 2 | 30 |
| unrelated APK pairs baseline | 30 | 23.5333 | 7.7448 | 40 | 0 |

## Interpretation

wHash переносит `recompress` и `scale` хорошо: все 30 пар остались в зоне
Hamming <= 5, средняя дистанция ниже 1.

`brightness` переносится средне: большинство пар осталось близко
(18/30 <= 5), но есть сильные выбросы до 18. Это значит, что +30% яркости уже
может менять 8x8 median-threshold структуру для части реальных иконок.

`translate` переносится хуже всех среди raw-iconicity модификаций: средняя
дистанция 6.37, только 10/30 пар <= 5, максимум 12. Сдвиг в canvas меняет
низкочастотную геометрию сильнее, чем recompress/scale.

Baseline по 30 случайным парам разных package заметно дальше
(mean_hamming=23.53, 0 пар <= 5), поэтому даже худшие synthetic-модификации
остаются отделены от неродственных APK-пар в этом наборе. Практический вывод:
wHash годится как устойчивый ресурсный sub-signal для recompress/scale,
осторожен для brightness и наиболее уязвим к translate.

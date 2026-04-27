# REPR-27-WHASH-FDROID

Smoke-прогон проверяет, насколько 64-bit icon wHash различает launcher-иконки
на F-Droid v2 corpus.

## Inputs

- APK corpus: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks`
- APK count: 350
- Decoded sibling used: `~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded`
- Output:
  - `report.json`
  - `histogram.json`

## Result

| metric | value |
|---|---:|
| APKs in corpus | 350 |
| APKs with icon hash | 250 |
| APKs without icon hash | 100 |
| pair count, C(250,2) | 31,125 |
| cross-package pair count | 31,004 |
| mean_hamming | 15.4729 |
| median_hamming | 12 |
| min_hamming | 0 |
| max_hamming | 62 |
| n_collisions, distance=0 and different package_name | 120 |
| collision_rate | 0.0038704683 |
| n_near_duplicates, distance <= 5 | 2,307 |

`histogram.json` stores the full distance distribution for distances 0..64.

## Interpretation

Collision rate is low but non-zero: 120 cross-package pairs have identical
64-bit icon hashes. This means wHash is not a unique icon identifier on this
corpus; it is a similarity signal.

Mean Hamming distance is 15.47 and median is 12. For random independent 64-bit
hashes the expected distance would be near 32, so this low center suggests
many reused/near-duplicate icons in the corpus, weak separation by wHash, or
both. The result supports keeping icon wHash as one resource sub-signal rather
than treating it as a strong standalone discriminator.

If collision_rate becomes high on another corpus, wHash is insufficiently
discriminative there. If mean_hamming is low, the corpus contains many duplicate
or near-duplicate icons, or the hash is too weak for the icon population.

## Scope Notes

The 100 missing icon hashes are APKs where the current extractor did not find a
PNG/WEBP launcher icon via the existing `resource_view_v2` candidate paths.
The distribution therefore covers the 250 APKs with a usable icon hash.

In this environment the external `imagehash` package was unavailable. The CLI
records that in `report.json` and computes a local 8x8 Haar-compatible wHash
for the smoke run instead of allowing `resource_view_v2` to fall back to dHash.

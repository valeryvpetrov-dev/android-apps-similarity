# REPR-24-IDF-FDROID-V2 IDF Snapshot

Artifact: `experiments/datasets/idf-snapshot-v2.json`

Build date: `2026-04-26`

## Corpus

The snapshot was built on the local F-Droid v2 APK corpus:

```bash
/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks
```

Corpus size:

- `n_documents = 350`
- APK requirement met: `350 >= 200`
- Decoded sibling used for library TPL extraction:
  `/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-decoded`
- Decoded coverage: `349/350` APKs. Missing decoded directory:
  `ch.ihdg.calendarcolor_4.apk`

`built_at` in the JSON is deterministic: it is derived from the newest APK
mtime in the corpus, not from the wall-clock run time. This keeps repeated
runs on the same corpus byte-stable.

## Method

Command:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/build_idf_snapshot_v2.py \
  --corpus_dir /Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --out experiments/datasets/idf-snapshot-v2.json \
  --layers library,component,resource
```

Extraction rules:

- `component` and `resource` use `screening_runner.extract_layers_from_apk`
  over APK ZIP contents.
- `library` uses detected TPL ids from `library_view_v2.detect_tpl_in_packages`
  over decoded smali package paths when the decoded sibling corpus is present.
- If no decoded corpus is available, `library` falls back to the quick APK
  layer from `extract_layers_from_apk`.
- If a requested layer has `n_tokens=0`, the layer is omitted and a warning is
  written into the snapshot.

The library token space is intentionally the same token space consumed by
`library_view_v2.compare_libraries_v2`: TPL ids such as `androidx_core`,
`okhttp3`, `okio`, `fresco`. This avoids the wave 23 failure mode where
`library.n_tokens=0` made IDF-Jaccard equivalent to flat Jaccard.

## Snapshot Summary

- `snapshot_version = "v2"`
- `n_documents = 350`
- `library.n_tokens = 41`
- `component.n_tokens = 2041`
- `resource.n_tokens = 9571`

Library examples:

- Common: `androidx_appcompat` appears in `202/350` APKs.
- Common: `androidx_core` appears in `169/350` APKs.
- Less common: `okio` appears in `53/350` APKs.
- Rare: `leakcanary` appears in `2/350` APKs.
- Rare: `fresco` appears in `1/350` APKs.

Example effect with this snapshot:

- tokens A: `{androidx_core, fresco}`
- tokens B: `{androidx_core}`
- flat Jaccard: `0.5`
- IDF-Jaccard: `0.110543`

## Limitations

- Library detection is bounded by `TPL_CATALOG_V2`; libraries outside that
  catalog do not contribute library IDF tokens.
- One APK has no decoded directory, so it contributes to `n_documents` and to
  APK-derived `component`/`resource`, but not to detected library tokens.
- `component` and `resource` are quick APK-layer tokens, not full apktool view
  outputs. This is sufficient for corpus-level document frequency, but it is
  not a replacement for enhanced pairwise feature extraction.
- The previous mini snapshot remains at `idf-snapshot-v1.json` for historical
  reproduction. Runtime default now points at `idf-snapshot-v2.json`.

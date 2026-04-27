# NOISE-27-NOISE-CACHE-RECALL

Artifact: `experiments/artifacts/NOISE-27-NOISE-CACHE-RECALL/report.json`

## Corpus

F-Droid v2 APK corpus:

```bash
/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks
```

Corpus size: `350` APKs.

The corpus path is also documented in
`experiments/datasets/idf-snapshot-v2-README.md`.

## Method

Command:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/run_noise_cache_recall.py \
  --corpus_dir /Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --out experiments/artifacts/NOISE-27-NOISE-CACHE-RECALL/report.json \
  --n_iterations 2
```

The runner performs repeated passes over the same sorted APK list:

- pass 1 starts with an empty `NoiseCache`;
- each APK is processed through `apply_libloom_detection`;
- cache hits are counted before each detector call;
- pass 2 reuses the populated cache from pass 1;
- `cache_hit_ratio = pass_2.cache_hits / corpus_size`;
- `speedup_factor = pass_1.avg_time_s / pass_2.avg_time_s`.

If the requested corpus is missing or contains no APK files, the runner falls
back to a temporary 5-APK mini-corpus and records
`fallback_mini_corpus_used` in `warnings`.

## Result

Current `report.json` summary:

- `corpus_size = 350`
- `n_iterations = 2`
- `pass_1.cache_hits = 0`
- `pass_2.cache_hits = 350`
- `cache_hit_ratio = 1.0`
- `avg_first_pass_s = 0.003120530828571428`
- `avg_second_pass_s = 0.002971573474285703`
- `speedup_factor = 1.050127434362541`

## Runtime Note

For this run, `LIBLOOM_HOME` was not set. The measurement still exercises the
real F-Droid v2 APK corpus and the `NoiseCache` recall path, but the underlying
LIBLOOM detector returns the configured unavailable status quickly. The runtime
state is recorded in `report.json` under `source.libloom_runtime`.

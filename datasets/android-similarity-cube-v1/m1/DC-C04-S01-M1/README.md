# DC-C04-S01-M1

Date: 2026-06-12.

This is an M1 diagnostic set for `C04 package_rename` and `S01 pair_similarity`.
The dataset is controlled synthetic and must not be treated as a real-world benchmark.

## Contents

- positive pairs: 20
- negative pairs: 20
- positive split: dev=16, holdout=4
- negative split: dev=16, holdout=4
- explanation tag rows: 100

## Status

`ready M1 diagnostic`, not `claim-ready benchmark`.

## Files

- `candidate_pairs.csv`: positive pairs before manifest construction.
- `generation_status.csv`: local APK paths, SHA-256 values, and build/sign/decode status.
- `decode_sanity_summary.csv`: decode summary for all generated APK files.
- `app_feature_extraction_smoke_summary.csv`: app-level smoke features.
- `feature_extraction_smoke_summary.csv`: pair-level smoke features.
- `manifest.csv`: positive and negative M1 diagnostic pairs.
- `pair_change_tags.csv`: tags for explanation checks.

## Allowed Use

Use for M1 diagnostics, split checks, small system runs, and explanation-tag preparation.

## Forbidden Use

Do not use as a publication-grade benchmark or as evidence about real-world class prevalence.

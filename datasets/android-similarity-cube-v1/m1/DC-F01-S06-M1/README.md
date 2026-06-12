# DC-F01-S06-M1

Date: 2026-06-12.

This is an M1 diagnostic set for `F01 analysis_failure` and `S06 failure_robustness`.
It contains single APK/input cases, not pairwise similarity rows.

## Contents

- failure cases: 20
- split: dev=16, holdout=4
- explanation/failure tag rows: 40
- `code_empty_apk`: 5
- `decode_timeout`: 5
- `malformed_apk`: 5
- `unpack_failed`: 5

## Status

`ready M1 diagnostic`, not `claim-ready benchmark`.

## Files

- `failure_cases.csv`: generated case inventory.
- `generation_status.csv`: artifact paths, SHA-256 values, and sanity result.
- `decode_sanity_summary.csv`: apktool sanity decode result where applicable.
- `manifest.csv`: failure-case manifest.
- `pair_change_tags.csv`: failure tags using the shared tag-file convention.

## Allowed Use

Use for service failure-path checks and for validating that analysis failure is not treated as score=0.

## Forbidden Use

Do not use as pairwise similarity benchmark data.

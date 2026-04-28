# SYS-30-NATIVE-LIB-FINGERPRINT

Lightweight native-library view for APK files.  It scans `lib/<abi>/*.so`
inside APK zip entries and extracts ELF-level fingerprints without CFG or
decompilation.

## Contract

`script/native_lib_view.py` exposes:

- `extract_native_lib_features(apk_path) -> dict`
  - top-level ABI layers: `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`;
  - each ABI value is a list of `.so` records with `path`, `name`, `size`,
    `sha256`, and `fingerprint`;
  - missing native libs return `native_libs_present: false` and empty ABI
    lists.
- `compare_native_libs(features_a, features_b) -> dict`
  - returns `jaccard_imports`, `jaccard_exports`, `jaccard_strings`,
    `jaccard_needed`, and weighted combined `score`.

Each ELF fingerprint includes:

- `soname`
- `needed_libs`
- `imported_symbols_set`
- `exported_symbols_set`
- `rodata_strings_top20`

`pyelftools` is optional.  If unavailable, the module uses a built-in
section-header parser based on `struct`.

## F-Droid v2 Coverage

Command:

```bash
python3 script/native_lib_view.py \
  --corpus ~/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --output experiments/artifacts/SYS-30-NATIVE-LIB-FINGERPRINT/report.json
```

Result snapshot:

- APKs scanned: 350
- APKs with native libs: 153
- Native-libs-present ratio: 0.43714285714285717
- ABI APK counts: `arm64-v8a=137`, `armeabi-v7a=136`, `x86=108`, `x86_64=124`
- Libs per APK: min `0`, median `0.0`, p90 `8.0`, max `42`
- Native bytes per APK: min `0`, median `0.0`, p90 `22532172.0`, max `105499792`

Full sample fingerprint is stored in `report.json`.

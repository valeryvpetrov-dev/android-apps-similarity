# NOISE-28-LIBLOOM-INSTALL

Wave 28, team NOISE, task `NOISE-28-LIBLOOM-INSTALL-AUTO`.

## Status

`script/install_libloom.py` installs a LIBLOOM runtime layout:

```text
$LIBLOOM_HOME/
  LIBLOOM.jar
  libs_profile/
```

The repository search found no real upstream download URL. Existing artifacts
only reference local paths, for example
`/Users/valeryvpetrov/phd/experiments/external/LIBLOOM/artifacts/LIBLOOM.jar`
and a historical worktree path. Because of that the script contains a fixed
placeholder URL:

```text
https://example.invalid/libloom/unknown-noise-28-placeholder/LIBLOOM-runtime.zip
```

Replace it with the real pinned upstream URL after the LIBLOOM source is
available, or pass it explicitly with `--source-url`.

## Install

```bash
python3 -m script.install_libloom \
  --target_dir ~/.cache/phd-similarity/libloom \
  --profile_version v1 \
  --source-url https://REPLACE-WITH-PINNED-UPSTREAM/LIBLOOM-runtime.zip
```

On success the CLI prints JSON and writes the export command to stderr:

```bash
export LIBLOOM_HOME=$HOME/.cache/phd-similarity/libloom/v1
```

Run it in the current shell:

```bash
export LIBLOOM_HOME=$HOME/.cache/phd-similarity/libloom/v1
```

## Verify

```bash
python3 - <<'PY'
from script.libloom_adapter import verify_libloom_setup
print(verify_libloom_setup())
PY
```

Expected result:

```text
status = "available"
available = True
```

Then rerun the blocked NOISE-26 real quality command:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m script.run_libloom_real_quality \
  --output experiments/artifacts/NOISE-26-LIBLOOM-REAL/report.json
```

## Test Stub

Unit tests mock `urllib.request.urlretrieve` and provide a local zip with
`LIBLOOM.jar` plus `libs_profile/`. This keeps CI independent from the external
LIBLOOM distribution until the real pinned URL is known.

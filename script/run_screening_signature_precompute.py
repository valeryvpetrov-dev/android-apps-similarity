#!/usr/bin/env python3
"""SCREENING-22-PRECOMPUTE-SIGNATURE — CLI precompute screening_signature на ingestion-time.

Закрывает рекомендацию №3 критика D волны 18 (`screening-2026-04-24.md` раздел 6,
пункт `SCREENING-19-INDEX-SEMANTICS`): сигнатура корпуса вычисляется один раз,
кладётся в JSONL-артефакт, и далее `_build_candidate_pairs_via_lsh` берёт
`screening_signature` напрямую из app_record — без runtime-fallback warning.

Контракт выходного формата: одна строка JSON на APK с полями
- `sha256` — SHA-256 APK,
- `app_id` — имя файла без расширения,
- `apk_path` — абсолютный путь к APK,
- `layers` — извлечённые M_static-слои (для пересборки app_record при чтении),
- `screening_signature` — список токенов (отсортированный для детерминированности),
- `signature_version` — `"v1"` (для совместимости с будущими версиями),
- `built_at` — ISO-8601 UTC timestamp.

Если APK не парсится — запись содержит `error: <reason>` и пустую сигнатуру,
прогон не падает (graceful degradation).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from script.screening_runner import (
    build_screening_signature,
    extract_layers_from_apk,
)


SIGNATURE_VERSION = "v1"


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_for_apk(apk_path: Path) -> dict:
    """Сформировать запись для одного APK; не падает на невалидном APK.

    `built_at` берётся из mtime APK, а не из текущего времени запуска — это
    нужно для детерминированности (test_run_precompute_is_deterministic
    сравнивает байты двух выходов на разных запусках, и реальный wallclock
    timestamp ломал бы сравнение). mtime APK стабилен между прогонами на
    том же файле, и при этом несёт смысловую нагрузку «какой версии APK
    соответствует эта сигнатура».
    """
    try:
        mtime = datetime.fromtimestamp(apk_path.stat().st_mtime, timezone.utc)
        built_at = mtime.isoformat()
    except OSError:
        built_at = "1970-01-01T00:00:00+00:00"
    base = {
        "app_id": apk_path.stem,
        "apk_path": str(apk_path.resolve()),
        "signature_version": SIGNATURE_VERSION,
        "built_at": built_at,
    }
    try:
        sha256 = _sha256_of_file(apk_path)
        layers = extract_layers_from_apk(apk_path)
        # build_screening_signature ждёт app_record с полем layers (set'ы внутри).
        signature = build_screening_signature({**base, "layers": layers})
        # Сортируем для детерминированного output (повторный прогон даёт
        # идентичный JSONL — это нужно для test_run_precompute_is_deterministic).
        signature_sorted = sorted(signature)
        # Сериализуем layers (set → отсортированный list) для JSON.
        layers_serialised = {
            layer_name: sorted(tokens) for layer_name, tokens in layers.items()
        }
        return {
            **base,
            "sha256": sha256,
            "layers": layers_serialised,
            "screening_signature": signature_sorted,
        }
    except Exception as exc:
        return {**base, "error": str(exc) or repr(exc), "screening_signature": []}


def run_screening_signature_precompute(
    apk_dir: Path,
    out_path: Path,
    *,
    profile_version: str = SIGNATURE_VERSION,
) -> Path:
    """Сканировать `apk_dir`, для каждого APK сформировать запись, записать в JSONL.

    Записи сортируются по `app_id` для детерминированного output. Поле
    `built_at` в этой реализации НЕ записывается, потому что test
    `test_run_precompute_is_deterministic` сравнивает побайтово два output
    с разных запусков — любой timestamp ломал бы детерминированность. Если в
    будущем понадобится timestamp — его можно добавить отдельным side-car
    `<out>.meta.json` без затрагивания основного JSONL.
    """
    apk_dir = Path(apk_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    apk_files = sorted(p for p in apk_dir.iterdir() if p.suffix.lower() == ".apk")
    records: list[dict] = []
    for apk_path in apk_files:
        record = _record_for_apk(apk_path)
        record["signature_version"] = profile_version
        records.append(record)

    # Детерминированная сортировка для test_run_precompute_is_deterministic.
    records.sort(key=lambda r: r["app_id"])

    with out_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record, sort_keys=True, ensure_ascii=False))
            fp.write("\n")

    return out_path


def _main(argv: Iterable[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk_dir", type=Path, default=Path("apk"))
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("experiments/precomputed/screening-signatures-v1.jsonl"),
    )
    parser.add_argument("--profile_version", default=SIGNATURE_VERSION)
    args = parser.parse_args(list(argv))

    out_path = run_screening_signature_precompute(
        args.apk_dir, args.out, profile_version=args.profile_version
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

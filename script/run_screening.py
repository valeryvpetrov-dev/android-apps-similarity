#!/usr/bin/env python3
"""Production-скрипт запуска первичного отбора (screening).

Использует ``query_app_id``/``candidate_app_id`` как primary source при чтении
результатов согласно screening-handoff-contract-v2.

Волна 17: скрипт выступает тонкой обёрткой над screening_runner.run_screening()
и явно читает canonical-поля из результата через screening_reader.

Использование:
    python run_screening.py <cascade_config_path> [--apps-features-json PATH]
                           [--apk-root PATH] [--output-json PATH]
                           [--ins-block-sim-threshold FLOAT]
                           [--ged-timeout-sec INT]
                           [--processes-count INT] [--threads-count INT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from script.screening_runner import run_screening
    from script.screening_reader import read_candidate_list
except ImportError:
    from screening_runner import run_screening  # type: ignore[no-redef]
    from screening_reader import read_candidate_list  # type: ignore[no-redef]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run cascade-config screening and return candidate_list in JSON. "
            "Primary keys: query_app_id/candidate_app_id "
            "(screening-handoff-contract-v2)."
        )
    )
    parser.add_argument("cascade_config_path", help="Path to YAML cascade-config")
    parser.add_argument(
        "--apps-features-json",
        default="",
        help=(
            "Optional path to JSON with app features. "
            "If omitted, APKs are discovered under --apk-root."
        ),
    )
    parser.add_argument(
        "--apk-root",
        default=str(Path(__file__).resolve().parents[1] / "apk"),
        help="Root folder for APK auto-discovery when --apps-features-json is not provided.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path where candidate_list JSON will be saved.",
    )
    parser.add_argument("--ins-block-sim-threshold", type=float, default=0.80)
    parser.add_argument("--ged-timeout-sec", type=int, default=30)
    parser.add_argument("--processes-count", type=int, default=1)
    parser.add_argument("--threads-count", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_candidate_list = run_screening(
        cascade_config_path=args.cascade_config_path,
        apps_features_json_path=args.apps_features_json or None,
        apk_root=args.apk_root,
        ins_block_sim_threshold=args.ins_block_sim_threshold,
        ged_timeout_sec=args.ged_timeout_sec,
        processes_count=args.processes_count,
        threads_count=args.threads_count,
    )

    # Читаем через screening_reader — canonical-поля как primary source.
    # Legacy-записи (только app_a/app_b) получат DeprecationWarning и будут
    # нормализованы. Новые записи (с query_app_id/candidate_app_id) — без warning.
    candidate_list = read_candidate_list(raw_candidate_list)

    payload = json.dumps(candidate_list, ensure_ascii=False, indent=2)
    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()

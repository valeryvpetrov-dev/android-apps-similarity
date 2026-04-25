"""EXEC-090-INCIDENTS: журнал инцидентов жёсткого таймаута.

По политике D-2026-04-094 каждое срабатывание pair_timeout_sec —
инцидент, не штатный режим. Журнал хранится как JSON Lines файл
в experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

INCIDENT_LOG_SCHEMA_VERSION = "timeout-incident-v1"


def default_incident_log_path() -> Path:
    """Стандартный путь журнала относительно корня subrepository.

    Файл этого модуля лежит в `<repo>/script/timeout_incident_registry.py`,
    так что корень sub-repo — это `parent.parent`.
    """
    repo_root = Path(__file__).resolve().parent.parent
    return (
        repo_root
        / "experiments"
        / "artifacts"
        / "E-EXEC-090-TIMEOUT-INCIDENTS"
        / "timeout-incidents.jsonl"
    )


def record_timeout_incident(
    pair_row: dict, log_path: Path | None = None
) -> dict:
    """Запись одного инцидента жёсткого таймаута в журнал.

    Формат записи:

        {
            "schema_version": "timeout-incident-v1",
            "recorded_at": ISO-8601 UTC,
            "status": "timeout",
            "pair_id": pair_row["pair_id"],
            "app_a": pair_row["app_a"],
            "app_b": pair_row["app_b"],
            "duration_ms": pair_row["duration_ms"],
            "pair_timeout_sec": pair_row["timeout_info"]["pair_timeout_sec"],
            "stage": pair_row["timeout_info"]["stage"],
            "views_used": pair_row.get("views_used", []),
        }

    Если `log_path` None — используется `default_incident_log_path()`.
    Родительская директория создаётся при необходимости. В файл
    дописывается одна строка JSON (режим `'a'`). Возвращается записанная
    запись.
    """
    if log_path is None:
        log_path = default_incident_log_path()

    timeout_info = pair_row.get("timeout_info") or {}

    record: dict = {
        "schema_version": INCIDENT_LOG_SCHEMA_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "timeout",
        "pair_id": pair_row.get("pair_id"),
        "app_a": pair_row.get("app_a"),
        "app_b": pair_row.get("app_b"),
        "duration_ms": pair_row.get("duration_ms"),
        "pair_timeout_sec": timeout_info.get("pair_timeout_sec"),
        "stage": timeout_info.get("stage"),
        "views_used": list(pair_row.get("views_used", [])),
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False))
        fh.write("\n")

    return record


def read_timeout_incidents(log_path: Path | None = None) -> list[dict]:
    """Читает все записи инцидентов из журнала.

    Пустые строки пропускаются. Если файл не существует — возвращается
    пустой список.
    """
    if log_path is None:
        log_path = default_incident_log_path()

    if not log_path.exists():
        return []

    records: list[dict] = []
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records

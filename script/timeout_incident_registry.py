"""EXEC-090-INCIDENTS: журнал инцидентов жёсткого таймаута.

По политике D-2026-04-094 каждое срабатывание pair_timeout_sec —
инцидент, не штатный режим. Журнал хранится как JSON Lines файл
в experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INCIDENT_LOG_SCHEMA_VERSION = "timeout-incident-v2"

_LEGACY_INCIDENT_LOG_SCHEMA_VERSION = "timeout-incident-v1"
_V2_FIELD_DEFAULTS: dict[str, Any] = {
    "feature_cache_hit": False,
    "decoded_dirs_present": False,
    "worker_started": False,
    "queued_timeout": False,
    "tmp_cleanup_status": "unknown",
    "external_tool": None,
}


class TimeoutIncidentSchemaError(ValueError):
    """Raised when a timeout incident record does not match the v2 schema."""


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


def _context_value(pair_row: dict, key: str, default: Any) -> Any:
    if key in pair_row:
        return pair_row[key]
    timeout_info = pair_row.get("timeout_info")
    if isinstance(timeout_info, dict) and key in timeout_info:
        return timeout_info[key]
    timeout_context = pair_row.get("timeout_context")
    if isinstance(timeout_context, dict) and key in timeout_context:
        return timeout_context[key]
    return default


def _infer_decoded_dirs_present(pair_row: dict) -> bool:
    explicit = _context_value(pair_row, "decoded_dirs_present", None)
    if explicit is not None:
        return explicit

    decoded_a = (
        pair_row.get("app_a_decoded_dir")
        or pair_row.get("decoded_dir_a")
        or pair_row.get("query_decoded_dir")
    )
    decoded_b = (
        pair_row.get("app_b_decoded_dir")
        or pair_row.get("decoded_dir_b")
        or pair_row.get("candidate_decoded_dir")
    )
    if decoded_a is not None or decoded_b is not None:
        return bool(decoded_a and decoded_b)

    app_a = pair_row.get("app_a")
    app_b = pair_row.get("app_b")
    if isinstance(app_a, dict) or isinstance(app_b, dict):
        app_a_decoded = app_a.get("decoded_dir") if isinstance(app_a, dict) else None
        app_b_decoded = app_b.get("decoded_dir") if isinstance(app_b, dict) else None
        return bool(app_a_decoded and app_b_decoded)

    return _V2_FIELD_DEFAULTS["decoded_dirs_present"]


def _v2_context_fields(pair_row: dict) -> dict[str, Any]:
    context = {
        "feature_cache_hit": _context_value(
            pair_row,
            "feature_cache_hit",
            _V2_FIELD_DEFAULTS["feature_cache_hit"],
        ),
        "decoded_dirs_present": _infer_decoded_dirs_present(pair_row),
        "worker_started": _context_value(
            pair_row,
            "worker_started",
            _V2_FIELD_DEFAULTS["worker_started"],
        ),
        "queued_timeout": _context_value(
            pair_row,
            "queued_timeout",
            _V2_FIELD_DEFAULTS["queued_timeout"],
        ),
        "tmp_cleanup_status": _context_value(
            pair_row,
            "tmp_cleanup_status",
            _V2_FIELD_DEFAULTS["tmp_cleanup_status"],
        ),
        "external_tool": _context_value(
            pair_row,
            "external_tool",
            _V2_FIELD_DEFAULTS["external_tool"],
        ),
    }
    return context


def record_timeout_incident_v2(
    pair_row: dict, log_path: Path | None = None
) -> dict:
    """Запись одного инцидента жёсткого таймаута в журнал.

    Формат записи:

        {
            "schema_version": "timeout-incident-v2",
            "recorded_at": ISO-8601 UTC,
            "status": "timeout",
            "pair_id": pair_row["pair_id"],
            "app_a": pair_row["app_a"],
            "app_b": pair_row["app_b"],
            "duration_ms": pair_row["duration_ms"],
            "pair_timeout_sec": pair_row["timeout_info"]["pair_timeout_sec"],
            "stage": pair_row["timeout_info"]["stage"],
            "views_used": pair_row.get("views_used", []),
            "feature_cache_hit": pair_row.get("feature_cache_hit", False),
            "decoded_dirs_present": pair_row.get("decoded_dirs_present", False),
            "worker_started": pair_row.get("worker_started", False),
            "queued_timeout": pair_row.get("queued_timeout", False),
            "tmp_cleanup_status": pair_row.get("tmp_cleanup_status", "unknown"),
            "external_tool": pair_row.get("external_tool"),
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
    record.update(_v2_context_fields(pair_row))

    validate_timeout_incident_record(record)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False))
        fh.write("\n")

    return record


def record_timeout_incident(
    pair_row: dict, log_path: Path | None = None
) -> dict:
    """Backward-compatible public writer; emits timeout-incident-v2 records."""
    return record_timeout_incident_v2(pair_row, log_path=log_path)


def _migrate_timeout_incident_record(record: dict) -> dict:
    if record.get("schema_version") == INCIDENT_LOG_SCHEMA_VERSION:
        migrated = dict(record)
    elif record.get("schema_version") == _LEGACY_INCIDENT_LOG_SCHEMA_VERSION:
        migrated = dict(record)
        migrated["schema_version"] = INCIDENT_LOG_SCHEMA_VERSION
    else:
        migrated = dict(record)

    for field_name, default in _V2_FIELD_DEFAULTS.items():
        migrated.setdefault(field_name, default)
    return migrated


def validate_timeout_incident_record(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        raise TimeoutIncidentSchemaError("timeout incident record must be a dict")

    expected_types: dict[str, tuple[type, ...]] = {
        "schema_version": (str,),
        "recorded_at": (str,),
        "status": (str,),
        "pair_id": (str, type(None)),
        "app_a": (str, type(None)),
        "app_b": (str, type(None)),
        "duration_ms": (int, type(None)),
        "pair_timeout_sec": (int, type(None)),
        "stage": (str, type(None)),
        "views_used": (list,),
        "feature_cache_hit": (bool,),
        "decoded_dirs_present": (bool,),
        "worker_started": (bool,),
        "queued_timeout": (bool,),
        "tmp_cleanup_status": (str,),
        "external_tool": (str, type(None)),
    }

    missing = [field for field in expected_types if field not in record]
    if missing:
        raise TimeoutIncidentSchemaError(
            "timeout incident missing fields: {}".format(", ".join(missing))
        )

    if record["schema_version"] != INCIDENT_LOG_SCHEMA_VERSION:
        raise TimeoutIncidentSchemaError(
            "timeout incident schema mismatch: expected {!r}, got {!r}".format(
                INCIDENT_LOG_SCHEMA_VERSION,
                record["schema_version"],
            )
        )

    for field_name, allowed_types in expected_types.items():
        if not isinstance(record[field_name], allowed_types):
            raise TimeoutIncidentSchemaError(
                "timeout incident field {!r} has invalid type".format(field_name)
            )
    return True


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
            record = json.loads(line)
            records.append(_migrate_timeout_incident_record(record))
    return records

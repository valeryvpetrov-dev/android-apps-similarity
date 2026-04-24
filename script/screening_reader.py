"""Модуль чтения candidate_list-строк первичного отбора."""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

try:
    from script.screening_writer import normalize_candidate_row, validate_candidate_row
except Exception:
    from screening_writer import normalize_candidate_row, validate_candidate_row  # type: ignore[no-redef]

_LEGACY_DEPRECATION_MSG = (
    "Candidate row uses legacy fields app_a/app_b without query_app_id/candidate_app_id. "
    "Fields app_a/app_b are removed from the canonical screening-contract-v1 output. "
    "Rewrite the artifact to use query_app_id/candidate_app_id."
)


def read_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать запись кандидата к screening-contract-v1."""
    has_canonical = "query_app_id" in row and "candidate_app_id" in row
    has_legacy = "app_a" in row and "app_b" in row

    if has_canonical and not has_legacy and row.get("screening_status") not in (None, ""):
        validate_candidate_row(row)
        return row

    if has_legacy:
        warnings.warn(
            _LEGACY_DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
    return normalize_candidate_row(row)


def read_candidate_list(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нормализовать список записей кандидатов.

    Применяет ``read_candidate_row`` к каждой записи.
    Warning выдаётся единожды на запись с legacy-форматом.

    Args:
        records: Список записей candidate_list.

    Returns:
        Список нормализованных записей.
    """
    return [read_candidate_row(row) for row in records]


def load_candidate_list_json(input_path: Path | str) -> list[dict[str, Any]]:
    """Загрузить candidate_list из JSON-файла с нормализацией полей.

    Args:
        input_path: Путь к JSON-файлу.

    Returns:
        Список нормализованных записей кандидатов.
    """
    path = Path(input_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    return read_candidate_list(raw)

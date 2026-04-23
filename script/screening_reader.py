"""Модуль чтения candidate_list-строк первичного отбора.

Отвечает за нормализованное чтение записей кандидатов с поддержкой
обратной совместимости с legacy-форматом (только ``app_a``/``app_b``).

Согласно screening-handoff-contract-v2:
- Канонические поля: ``query_app_id``/``candidate_app_id``.
- Deprecated alias: ``app_a``/``app_b``.

Если в записи есть только ``app_a``/``app_b`` (legacy-формат без новых полей),
читатель выдаёт ``DeprecationWarning`` и возвращает нормализованную запись.
Записи с обоими наборами полей обрабатываются без предупреждения.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

# Сообщение warning для legacy-записей.
_DEPRECATION_MSG = (
    "Candidate row uses legacy fields app_a/app_b without query_app_id/candidate_app_id. "
    "Fields app_a/app_b are deprecated since screening-handoff-contract-v2. "
    "Update the writer to use query_app_id/candidate_app_id. "
    "Support for app_a/app_b-only records will be removed in wave 18."
)


def read_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать запись кандидата к canonical-форматуконтракта v2.

    Если в записи есть ``query_app_id`` и ``candidate_app_id`` — возвращает
    запись без изменений (возможно с deprecated alias, это нормально).

    Если в записи есть только ``app_a``/``app_b`` — выдаёт ``DeprecationWarning``
    и дополняет запись каноническими полями из legacy-alias.

    Args:
        row: Запись кандидата из candidate_list.

    Returns:
        Запись кандидата с гарантированными полями ``query_app_id``
        и ``candidate_app_id``.

    Raises:
        KeyError: Если в записи нет ни canonical, ни legacy-полей.
    """
    has_canonical = "query_app_id" in row and "candidate_app_id" in row
    has_legacy = "app_a" in row and "app_b" in row

    if has_canonical:
        # Новый формат — warning не нужен.
        return row

    if has_legacy:
        # Legacy-формат — предупреждаем и нормализуем.
        warnings.warn(
            _DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        normalized = dict(row)
        normalized["query_app_id"] = row["app_a"]
        normalized["candidate_app_id"] = row["app_b"]
        return normalized

    raise KeyError(
        "Candidate row missing both canonical fields (query_app_id, candidate_app_id) "
        "and legacy fields (app_a, app_b). Row keys: {}".format(list(row.keys()))
    )


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

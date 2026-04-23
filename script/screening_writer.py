"""Модуль записи candidate_list-строк первичного отбора.

Выделен из screening_runner.py для явного управления полями при записи.
Вводит новые канонические ключи пары ``query_app_id``/``candidate_app_id``
согласно screening-handoff-contract-v2.

Миграция:
- Волна 17 (текущая): ``app_a``/``app_b`` заполняются для обратной совместимости,
  но помечены как deprecated.
- Волна 18: ``app_a``/``app_b`` будут удалены, этот модуль останется.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

# Метка для идентификации поля «помечено как deprecated» в сообщениях.
_DEPRECATION_MSG = (
    "Fields app_a/app_b are deprecated since screening-handoff-contract-v2; "
    "read query_app_id/candidate_app_id instead."
)


def write_candidate_row(
    query_app_id: str,
    candidate_app_id: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать запись кандидата с каноническими и deprecated-полями.

    Канонические поля (source of truth по контракту v2):
        ``query_app_id`` — идентификатор приложения-запроса;
        ``candidate_app_id`` — идентификатор приложения-кандидата.

    Deprecated-поля (alias до волны 18):
        ``app_a`` — alias для ``query_app_id``;
        ``app_b`` — alias для ``candidate_app_id``.

    .. deprecated::
        ``app_a``/``app_b`` устарели с волны 17. Используйте
        ``query_app_id``/``candidate_app_id``. Поля будут удалены в волне 18.

    Args:
        query_app_id: Идентификатор приложения-запроса.
        candidate_app_id: Идентификатор приложения-кандидата.
        extra_fields: Дополнительные поля для записи (retrieval_score, features и др.).

    Returns:
        dict — запись кандидата с каноническими и deprecated-полями.
    """
    warnings.warn(
        _DEPRECATION_MSG,
        DeprecationWarning,
        stacklevel=2,
    )

    row: dict[str, Any] = {
        # Канонические поля (source of truth).
        "query_app_id": query_app_id,
        "candidate_app_id": candidate_app_id,
        # Deprecated alias до волны 18. Значения совпадают с canonical.
        "app_a": query_app_id,
        "app_b": candidate_app_id,
    }
    if extra_fields:
        row.update(extra_fields)
    return row


def validate_candidate_row(row: dict[str, Any]) -> None:
    """Проверить инварианты записи кандидата по контракту v2.

    Если в записи присутствуют оба набора полей, их значения должны совпадать.
    При расхождении поднимается AssertionError со ссылкой на контракт v2.

    Args:
        row: Запись кандидата.

    Raises:
        AssertionError: Если app_a != query_app_id или app_b != candidate_app_id.
        KeyError: Если в записи отсутствует одно из канонических полей.
    """
    query_app_id = row["query_app_id"]
    candidate_app_id = row["candidate_app_id"]

    if "app_a" in row:
        assert row["app_a"] == query_app_id, (
            f"Invariant violation (screening-handoff-contract-v2): "
            f"app_a={row['app_a']!r} != query_app_id={query_app_id!r}. "
            "See system/screening-handoff-contract-v2.md section 3."
        )
    if "app_b" in row:
        assert row["app_b"] == candidate_app_id, (
            f"Invariant violation (screening-handoff-contract-v2): "
            f"app_b={row['app_b']!r} != candidate_app_id={candidate_app_id!r}. "
            "See system/screening-handoff-contract-v2.md section 3."
        )


def write_candidate_list_json(
    candidate_list: list[dict[str, Any]],
    output_path: Path | str,
) -> None:
    """Записать список кандидатов в JSON-файл.

    Args:
        candidate_list: Список записей кандидатов.
        output_path: Путь для записи JSON.
    """
    path = Path(output_path)
    path.write_text(
        json.dumps(candidate_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

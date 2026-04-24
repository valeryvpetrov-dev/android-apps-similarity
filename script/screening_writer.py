"""Модуль записи и валидации candidate_list-строк первичного отбора."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONTRACT_DOC = "system/screening-contract-v1.md"
SCREENING_STATUS_PRELIMINARY_POSITIVE = "preliminary_positive"
SCREENING_STATUS_PRELIMINARY_NEGATIVE = "preliminary_negative"
ALLOWED_SCREENING_STATUSES = {
    SCREENING_STATUS_PRELIMINARY_POSITIVE,
    SCREENING_STATUS_PRELIMINARY_NEGATIVE,
}


def write_candidate_row(
    query_app_id: str,
    candidate_app_id: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать каноническую candidate row по screening-contract-v1."""
    row: dict[str, Any] = {
        "query_app_id": query_app_id,
        "candidate_app_id": candidate_app_id,
        "screening_status": SCREENING_STATUS_PRELIMINARY_POSITIVE,
    }
    if extra_fields:
        row.update(extra_fields)
    return normalize_candidate_row(row)


def _contract_value_error(message: str) -> ValueError:
    return ValueError(f"{message} See {CONTRACT_DOC}.")


def normalize_candidate_row(
    row: dict[str, Any],
    *,
    drop_legacy_fields: bool = True,
) -> dict[str, Any]:
    """Нормализовать candidate row к единому контракту v1.

    Допускает legacy alias ``app_a``/``app_b`` только как источник миграции
    или для явной проверки рассинхрона. На выходе возвращает canonical-only
    запись, если ``drop_legacy_fields=True``.
    """
    normalized = dict(row)
    has_canonical = "query_app_id" in normalized and "candidate_app_id" in normalized
    has_legacy = "app_a" in normalized and "app_b" in normalized

    if not has_canonical and not has_legacy:
        raise KeyError(
            "Candidate row missing both canonical fields (query_app_id, candidate_app_id) "
            "and legacy fields (app_a, app_b). Row keys: {}".format(list(row.keys()))
        )

    if has_canonical:
        query_app_id = str(normalized["query_app_id"]).strip()
        candidate_app_id = str(normalized["candidate_app_id"]).strip()
    else:
        query_app_id = str(normalized["app_a"]).strip()
        candidate_app_id = str(normalized["app_b"]).strip()
        normalized["query_app_id"] = query_app_id
        normalized["candidate_app_id"] = candidate_app_id

    if not query_app_id or not candidate_app_id:
        raise _contract_value_error(
            "Candidate row must define non-empty query_app_id and candidate_app_id."
        )
    if query_app_id == candidate_app_id:
        raise _contract_value_error(
            "Candidate row must not compare an app with itself: {!r}.".format(query_app_id)
        )

    if has_legacy:
        if str(normalized["app_a"]).strip() != query_app_id:
            raise _contract_value_error(
                "Legacy field app_a={!r} does not match query_app_id={!r}.".format(
                    normalized["app_a"], query_app_id
                )
            )
        if str(normalized["app_b"]).strip() != candidate_app_id:
            raise _contract_value_error(
                "Legacy field app_b={!r} does not match candidate_app_id={!r}.".format(
                    normalized["app_b"], candidate_app_id
                )
            )
        if drop_legacy_fields:
            normalized.pop("app_a", None)
            normalized.pop("app_b", None)

    status = normalized.get("screening_status")
    if status in (None, ""):
        normalized["screening_status"] = SCREENING_STATUS_PRELIMINARY_POSITIVE
    else:
        normalized_status = str(status).strip()
        if normalized_status not in ALLOWED_SCREENING_STATUSES:
            raise _contract_value_error(
                "Unsupported screening_status={!r}; expected one of {!r}.".format(
                    status, sorted(ALLOWED_SCREENING_STATUSES)
                )
            )
        normalized["screening_status"] = normalized_status

    return normalized


def validate_candidate_row(row: dict[str, Any]) -> None:
    """Проверить инварианты candidate row по screening-contract-v1."""
    if "query_app_id" not in row or "candidate_app_id" not in row:
        raise KeyError(
            "Candidate row missing canonical fields (query_app_id, candidate_app_id)."
        )
    normalize_candidate_row(row, drop_legacy_fields=False)


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
        json.dumps(
            [normalize_candidate_row(row) for row in candidate_list],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

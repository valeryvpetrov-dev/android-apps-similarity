"""EXEC-090-INCIDENTS-ANALYSIS: CLI разбора журнала таймаут-инцидентов.

Входной файл — JSON Lines в формате `timeout-incident-v1` (см.
`script/timeout_incident_registry.py`). Одна строка — один инцидент:

    {
        "schema_version": "timeout-incident-v1",
        "recorded_at": "2026-04-21T12:00:00+00:00",
        "app_a": "com.example.a",
        "app_b": "com.example.b",
        "pair_timeout_sec": 600,
        "stage": "pairwise",
        "views_used": ["code_view", "api_view"]
    }

Скрипт читает файл построчно, считает частоты и печатает секционированный
отчёт в markdown (простыми словами) или в JSON.

Запуск:

    python script/analyze_timeout_incidents.py \
        --input experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/timeout-incidents.jsonl \
        --output experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/analysis.md \
        --format text
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


DEFAULT_INPUT = Path(
    "experiments/artifacts/E-EXEC-090-TIMEOUT-INCIDENTS/timeout-incidents.jsonl"
)


def parse_incidents(
    lines: Iterable[str],
) -> tuple[list[dict], list[str]]:
    """Разбор строк журнала. Возвращает (records, warnings).

    Некорректные строки не прерывают разбор: попадают в `warnings` с
    указанием номера строки (1-based). Пустые строки пропускаются
    молча.
    """
    records: list[dict] = []
    warnings: list[str] = []
    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"строка {idx}: не JSON ({exc.msg})")
            continue
        if not isinstance(record, dict):
            warnings.append(
                f"строка {idx}: ожидали объект JSON, получили "
                f"{type(record).__name__}"
            )
            continue
        records.append(record)
    return records, warnings


def _views_key(record: dict) -> tuple[str, ...]:
    """Ключ из views_used как отсортированный кортеж (multiset-подобный)."""
    views = record.get("views_used") or []
    if not isinstance(views, list):
        return ()
    # Сортируем, чтобы одинаковые комбинации сливались в одну группу.
    return tuple(sorted(str(v) for v in views))


def _day_key(record: dict) -> str:
    """День из recorded_at в формате YYYY-MM-DD; 'unknown' при отсутствии."""
    raw = record.get("recorded_at")
    if not isinstance(raw, str) or not raw:
        return "unknown"
    # ISO-8601: берём первые 10 символов. Безопасно для корректных записей.
    head = raw[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        return head
    return "unknown"


def summarize(records: list[dict]) -> dict:
    """Считает агрегаты по инцидентам."""
    total = len(records)

    stage_counts: Counter[str] = Counter()
    views_counts: Counter[tuple[str, ...]] = Counter()
    timeout_counts: Counter[int | str] = Counter()
    day_counts: Counter[str] = Counter()

    for record in records:
        stage = record.get("stage") or "unknown"
        stage_counts[str(stage)] += 1

        views_counts[_views_key(record)] += 1

        timeout = record.get("pair_timeout_sec")
        if isinstance(timeout, (int, float)):
            timeout_counts[int(timeout)] += 1
        else:
            timeout_counts["unknown"] += 1

        day_counts[_day_key(record)] += 1

    dates_known = sorted(d for d in day_counts if d != "unknown")
    date_range = (
        (dates_known[0], dates_known[-1]) if dates_known else (None, None)
    )

    return {
        "total": total,
        "stage_counts": stage_counts,
        "views_counts": views_counts,
        "timeout_counts": timeout_counts,
        "day_counts": day_counts,
        "date_range": date_range,
    }


def _format_views(key: tuple[str, ...]) -> str:
    if not key:
        return "(пусто)"
    return ", ".join(key)


def render_markdown(
    summary: dict,
    warnings: list[str],
    source_label: str,
) -> str:
    """Секционированный отчёт простыми словами."""
    total = summary["total"]
    stage_counts: Counter[str] = summary["stage_counts"]
    views_counts: Counter[tuple[str, ...]] = summary["views_counts"]
    timeout_counts: Counter[int | str] = summary["timeout_counts"]
    day_counts: Counter[str] = summary["day_counts"]
    date_from, date_to = summary["date_range"]

    out: list[str] = []
    out.append("# Разбор журнала таймаут-инцидентов")
    out.append("")
    out.append(f"Источник: `{source_label}`")
    out.append("")

    out.append("## Общая сводка")
    out.append("")
    out.append(f"- Всего инцидентов: {total}")
    if date_from and date_to:
        out.append(f"- Диапазон дат: {date_from} — {date_to}")
    else:
        out.append("- Диапазон дат: (нет корректных дат)")
    if warnings:
        out.append(f"- Некорректных строк при разборе: {len(warnings)}")
    out.append("")

    out.append("## Топ-5 стадий (stage) по частоте")
    out.append("")
    if stage_counts:
        for stage, cnt in stage_counts.most_common(5):
            out.append(f"- {stage}: {cnt}")
    else:
        out.append("- данных нет")
    out.append("")

    out.append("## Топ-5 комбинаций views_used по частоте")
    out.append("")
    if views_counts:
        for key, cnt in views_counts.most_common(5):
            out.append(f"- [{_format_views(key)}]: {cnt}")
    else:
        out.append("- данных нет")
    out.append("")

    out.append("## Распределение pair_timeout_sec")
    out.append("")
    if timeout_counts:
        # Сортируем числовые значения по возрастанию, unknown — в конце.
        numeric = sorted(
            (k, v) for k, v in timeout_counts.items() if isinstance(k, int)
        )
        unknown = timeout_counts.get("unknown", 0)
        for key, cnt in numeric:
            out.append(f"- {key} сек: {cnt}")
        if unknown:
            out.append(f"- неизвестно: {unknown}")
    else:
        out.append("- данных нет")
    out.append("")

    out.append("## Распределение по дням (recorded_at)")
    out.append("")
    if day_counts:
        for day in sorted(day_counts):
            out.append(f"- {day}: {day_counts[day]}")
    else:
        out.append("- данных нет")
    out.append("")

    if warnings:
        out.append("## Предупреждения при разборе")
        out.append("")
        for w in warnings:
            out.append(f"- {w}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def render_json(
    summary: dict,
    warnings: list[str],
    source_label: str,
) -> str:
    """JSON-представление агрегатов (удобно для дальнейших скриптов)."""
    stage_counts: Counter[str] = summary["stage_counts"]
    views_counts: Counter[tuple[str, ...]] = summary["views_counts"]
    timeout_counts: Counter[int | str] = summary["timeout_counts"]
    day_counts: Counter[str] = summary["day_counts"]
    date_from, date_to = summary["date_range"]

    payload = {
        "source": source_label,
        "total": summary["total"],
        "date_range": {"from": date_from, "to": date_to},
        "stage_counts": dict(stage_counts),
        "views_counts_top": [
            {"views_used": list(key), "count": cnt}
            for key, cnt in views_counts.most_common()
        ],
        "timeout_counts": {str(k): v for k, v in timeout_counts.items()},
        "day_counts": dict(day_counts),
        "warnings": warnings,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def analyze_file(
    input_path: Path, output_format: str
) -> tuple[str, list[dict], list[str]]:
    """Полный разбор файла и рендер отчёта заданного формата."""
    if not input_path.exists():
        raise FileNotFoundError(f"входной файл не найден: {input_path}")

    with input_path.open("r", encoding="utf-8") as fh:
        records, warnings = parse_incidents(fh)

    summary = summarize(records)
    source_label = str(input_path)

    if output_format == "json":
        report = render_json(summary, warnings, source_label)
    else:
        report = render_markdown(summary, warnings, source_label)

    return report, records, warnings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Разбор журнала таймаут-инцидентов (timeout-incident-v1)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Путь к входному JSON Lines файлу.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Путь к файлу отчёта. По умолчанию отчёт печатается в stdout."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Формат отчёта: text (markdown) или json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        report, _records, _warnings = analyze_file(args.input, args.format)
    except FileNotFoundError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2

    if args.output is None:
        sys.stdout.write(report)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

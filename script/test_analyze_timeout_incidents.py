"""Тесты CLI разбора журнала таймаут-инцидентов.

Синтетические инциденты готовятся прямо в tests через фикстуру
`tmp_path` — реальный файл журнала тестам не нужен.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyze_timeout_incidents import (
    analyze_file,
    main,
    parse_incidents,
    render_json,
    render_markdown,
    summarize,
)


def _make_incident(
    app_a: str,
    app_b: str,
    stage: str,
    views_used: list[str],
    pair_timeout_sec: int = 600,
    recorded_at: str = "2026-04-21T10:00:00+00:00",
) -> dict:
    return {
        "schema_version": "timeout-incident-v1",
        "recorded_at": recorded_at,
        "app_a": app_a,
        "app_b": app_b,
        "pair_timeout_sec": pair_timeout_sec,
        "stage": stage,
        "views_used": views_used,
    }


def _write_jsonl(path: Path, records: list[dict], extra_raw: list[str] | None = None) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    if extra_raw:
        lines.extend(extra_raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_and_count_total(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    records = [
        _make_incident("a", "b", "pairwise", ["code_view"]),
        _make_incident("a", "c", "screening", ["api_view"]),
        _make_incident("b", "c", "deepening", ["code_view", "api_view"]),
    ]
    _write_jsonl(path, records)

    with path.open() as fh:
        parsed, warnings = parse_incidents(fh)

    assert warnings == []
    assert len(parsed) == 3
    assert parsed[0]["app_a"] == "a"


def test_summarize_groups_by_stage(tmp_path: Path) -> None:
    records = [
        _make_incident("a", "b", "pairwise", ["code_view"]),
        _make_incident("c", "d", "pairwise", ["api_view"]),
        _make_incident("e", "f", "screening", ["code_view"]),
        _make_incident("g", "h", "deepening", ["api_view"]),
    ]
    summary = summarize(records)
    assert summary["total"] == 4
    assert summary["stage_counts"]["pairwise"] == 2
    assert summary["stage_counts"]["screening"] == 1
    assert summary["stage_counts"]["deepening"] == 1


def test_summarize_groups_by_views_used(tmp_path: Path) -> None:
    # Два раза одна комбинация (порядок в списке не важен),
    # один раз другая.
    records = [
        _make_incident("a", "b", "pairwise", ["code_view", "api_view"]),
        _make_incident("c", "d", "pairwise", ["api_view", "code_view"]),
        _make_incident("e", "f", "pairwise", ["library_view"]),
    ]
    summary = summarize(records)
    views_counts = summary["views_counts"]
    assert views_counts[("api_view", "code_view")] == 2
    assert views_counts[("library_view",)] == 1


def test_parse_tolerates_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    good = _make_incident("a", "b", "pairwise", ["code_view"])
    raw_lines = [
        json.dumps(good, ensure_ascii=False),
        "это не JSON",
        "",  # пустая строка должна пропускаться молча
        "42",  # валидный JSON, но не объект
        json.dumps(
            _make_incident("c", "d", "screening", ["api_view"]),
            ensure_ascii=False,
        ),
    ]
    path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    with path.open() as fh:
        records, warnings = parse_incidents(fh)

    assert len(records) == 2
    # Одно предупреждение про не-JSON, одно про не-объект.
    assert len(warnings) == 2
    assert any("строка 2" in w for w in warnings)
    assert any("строка 4" in w for w in warnings)


def test_analyze_file_text_output(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    records = [
        _make_incident(
            "a", "b", "pairwise", ["code_view"],
            pair_timeout_sec=600,
            recorded_at="2026-04-20T08:00:00+00:00",
        ),
        _make_incident(
            "c", "d", "screening", ["api_view"],
            pair_timeout_sec=900,
            recorded_at="2026-04-21T12:00:00+00:00",
        ),
    ]
    _write_jsonl(path, records)

    report, parsed, warnings = analyze_file(path, "text")

    assert warnings == []
    assert len(parsed) == 2
    assert "Всего инцидентов: 2" in report
    assert "pairwise" in report
    assert "screening" in report
    assert "2026-04-20 — 2026-04-21" in report
    # markdown-заголовок на месте
    assert report.startswith("# Разбор журнала таймаут-инцидентов")


def test_analyze_file_json_output(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    records = [
        _make_incident("a", "b", "pairwise", ["code_view"], 600),
        _make_incident("c", "d", "pairwise", ["code_view"], 600),
    ]
    _write_jsonl(path, records)

    report, _, _ = analyze_file(path, "json")
    payload = json.loads(report)

    assert payload["total"] == 2
    assert payload["stage_counts"]["pairwise"] == 2
    assert payload["timeout_counts"]["600"] == 2
    assert payload["warnings"] == []


def test_main_writes_output_file(tmp_path: Path) -> None:
    input_path = tmp_path / "log.jsonl"
    output_path = tmp_path / "report.md"
    _write_jsonl(
        input_path,
        [_make_incident("a", "b", "pairwise", ["code_view"])],
    )

    rc = main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--format",
            "text",
        ]
    )

    assert rc == 0
    text = output_path.read_text(encoding="utf-8")
    assert "Всего инцидентов: 1" in text


def test_render_markdown_with_warnings(tmp_path: Path) -> None:
    # Прямой вызов рендера с синтетическим summary и предупреждениями.
    records = [
        _make_incident("a", "b", "pairwise", ["code_view"]),
    ]
    summary = summarize(records)
    md = render_markdown(
        summary,
        warnings=["строка 7: не JSON (Expecting value)"],
        source_label="demo.jsonl",
    )
    assert "Предупреждения при разборе" in md
    assert "строка 7" in md
    assert "Источник: `demo.jsonl`" in md


def test_render_json_shapes_lists(tmp_path: Path) -> None:
    records = [
        _make_incident("a", "b", "pairwise", ["code_view", "api_view"]),
    ]
    summary = summarize(records)
    payload = json.loads(
        render_json(summary, warnings=[], source_label="demo.jsonl")
    )
    top = payload["views_counts_top"]
    assert isinstance(top, list)
    assert top[0]["views_used"] == ["api_view", "code_view"]
    assert top[0]["count"] == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

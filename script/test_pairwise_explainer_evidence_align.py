"""Тесты EXEC-DESCRIBE-PAIR-EVIDENCE-CONTRACT-ALIGN.

Проверяем, что `pairwise_explainer.build_output_rows` использует
`evidence` как единый источник для `explanation_hints`, а при пустом
evidence — корректно падает в fallback на legacy-логику и предупреждает
об этом в логе.

Контракт evidence-записи см. `evidence_formatter.make_evidence`:
поля `source_stage`, `signal_type`, `magnitude`, `ref`. Helper
`_hints_from_evidence` из каждой такой записи формирует hint вида
`{"type": signal_type, "signal": signal_type, "entity": ref, "score": magnitude}`.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pairwise_explainer import (  # noqa: E402
    _hints_from_evidence,
    build_output_rows,
)


def _evidence_layer_score(ref: str, magnitude: float) -> dict:
    """Собрать per-layer evidence-запись в формате EXEC-088."""
    return {
        "source_stage": "pairwise",
        "signal_type": "layer_score",
        "magnitude": magnitude,
        "ref": ref,
    }


def _evidence_signature_match(magnitude: float) -> dict:
    """Собрать signature-evidence-запись в формате EXEC-088."""
    return {
        "source_stage": "signing",
        "signal_type": "signature_match",
        "magnitude": magnitude,
        "ref": "apk_signature",
    }


def test_hints_from_evidence_matches_evidence_items():
    """Каждая evidence-запись превращается ровно в один hint с теми же значениями.

    Проверяем контракт: signal берётся из `signal_type`, entity — из `ref`,
    score — из `magnitude`, type — тот же signal_type (для per-layer
    `layer_score` различает слои полем entity).
    """
    evidence = [
        _evidence_layer_score("code", 0.81),
        _evidence_layer_score("component", 0.42),
        _evidence_signature_match(1.0),
    ]

    hints = _hints_from_evidence(evidence)

    assert len(hints) == 3
    # Порядок hints должен совпадать с порядком evidence-записей
    assert hints[0] == {
        "type": "layer_score",
        "signal": "layer_score",
        "entity": "code",
        "score": 0.81,
    }
    assert hints[1] == {
        "type": "layer_score",
        "signal": "layer_score",
        "entity": "component",
        "score": 0.42,
    }
    assert hints[2] == {
        "type": "signature_match",
        "signal": "signature_match",
        "entity": "apk_signature",
        "score": 1.0,
    }


def test_fallback_used_when_evidence_empty_emits_warning(caplog):
    """При пустом evidence build_output_rows падает на legacy build_explanation_hints
    и пишет warning с pair_id."""
    pair_without_evidence = {
        "app_a": "app-a-id",
        "app_b": "app-b-id",
        "pair_id": "pair_fallback_demo",
        "similarity_score": 0.7,
        # Никакого evidence в pair_row
        "component_features_a": ["permission:android.permission.CAMERA"],
        "component_features_b": ["permission:android.permission.CAMERA"],
        "resource_features_a": [],
        "resource_features_b": [],
        "dots_1": [],
        "dots_2": [],
    }

    with caplog.at_level(logging.WARNING, logger="pairwise_explainer"):
        rows = build_output_rows([pair_without_evidence])

    assert len(rows) == 1
    row = rows[0]
    # Legacy-ветка возвращает 8 фиксированных hint_type (LibraryImpact,
    # NewMethodCall, ComponentChange, ResourceChange, PermissionChange,
    # NativeLibChange, CertificateMismatch, CodeRemoval). Проверяем, что
    # это именно legacy-структура, а не evidence-формат.
    assert len(row["explanation_hints"]) == 8
    assert all("hint_type" in hint for hint in row["explanation_hints"])

    # В логе должно быть предупреждение с pair_id
    matching_records = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "evidence empty" in record.getMessage()
    ]
    assert len(matching_records) == 1, (
        "Ожидалось одно WARNING-сообщение про пустой evidence, "
        f"получено: {[r.getMessage() for r in caplog.records]}"
    )
    assert "pair_fallback_demo" in matching_records[0].getMessage()


def test_evidence_priority_over_legacy_hints():
    """Когда evidence непустой, explanation_hints строятся из него, а legacy-логика
    по полям pair игнорируется (никакого LibraryImpact/PermissionChange и так далее)."""
    pair_with_evidence = {
        "app_a": "app-a-id",
        "app_b": "app-b-id",
        "pair_id": "pair_with_evidence",
        "similarity_score": 0.9,
        # Поля, которые раньше использовались legacy-логикой — должны быть проигнорированы
        "component_features_a": ["permission:android.permission.CAMERA"],
        "component_features_b": [
            "permission:android.permission.CAMERA",
            "permission:android.permission.READ_CONTACTS",
        ],
        "resource_features_a": ["META-INF/CERT.RSA"],
        "resource_features_b": ["META-INF/NEWCERT.RSA"],
        "dots_1": ["com.example.Foo"],
        "dots_2": ["com.example.Bar"],
        "full_similarity_score": 0.95,
        "library_reduced_score": 0.9,
        # Единый источник правды для hints
        "evidence": [
            _evidence_layer_score("code", 0.88),
            _evidence_signature_match(1.0),
        ],
    }

    rows = build_output_rows([pair_with_evidence])

    assert len(rows) == 1
    row = rows[0]

    # Hints построены из evidence — ровно две записи, а не 8 legacy-шаблонов
    assert len(row["explanation_hints"]) == 2
    for hint in row["explanation_hints"]:
        # Ни одного legacy-поля hint_type быть не должно
        assert "hint_type" not in hint
        # Канонический evidence-контракт
        assert set(hint.keys()) == {"type", "signal", "entity", "score"}

    # Конкретные значения соответствуют evidence-записям
    first, second = row["explanation_hints"]
    assert first["signal"] == "layer_score"
    assert first["entity"] == "code"
    assert first["score"] == 0.88
    assert second["signal"] == "signature_match"
    assert second["entity"] == "apk_signature"
    assert second["score"] == 1.0

    # Evidence также остаётся прокинутым в итоговую строку (EXEC-088-WRITERS)
    assert row["evidence"] == pair_with_evidence["evidence"]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

#!/usr/bin/env python3
"""Tests for NOISE-GATE-WIRING (wave 16, P0 от критика noise-cleanup волны 14).

Цель: noise-gate как первый фильтр каскада в screening entry-point.
Модуль script/noise_integration.py получает функцию
``should_reject_by_noise_gate(app_record, reject_triggers)``;
``script/screening_runner.run_screening`` читает секцию ``noise_gate``
из cascade-config и применяет её к app_records до индексации.

Покрытие:
  1. test_noise_gate_rejects_adware          — отклоняет при триггере adware;
  2. test_noise_gate_rejects_fake            — отклоняет при триггере fake;
  3. test_noise_gate_passes_clean_app        — пропускает чистое приложение;
  4. test_noise_gate_disabled_passes_all     — enabled=false не фильтрует;
  5. test_noise_gate_respects_custom_triggers — список reject_triggers
     учитывается (shim/severe_packing и прочие);
  6. test_noise_gate_writes_rejected_artifact — отклонённые пишутся
     в noise_rejected.json, summary — в screening_run_summary.json.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# run_screening проверяет системные зависимости; в unit-тестах пропускаем.
os.environ.setdefault("SIMILARITY_SKIP_REQ_CHECK", "1")

from noise_integration import should_reject_by_noise_gate
from screening_runner import (
    _extract_noise_gate_config,
    apply_noise_gate,
    run_screening,
)


def _full_layers(code: set[str]) -> dict[str, set[str]]:
    return {
        "code": set(code),
        "component": set(),
        "resource": set(),
        "metadata": set(),
        "library": set(),
    }


def _make_app(
    app_id: str,
    code: set[str],
    envelope_triggers: list[str] | None = None,
) -> dict:
    record: dict = {
        "app_id": app_id,
        "layers": _full_layers(code),
        "apk_path": "/abs/path/{}.apk".format(app_id.lower()),
    }
    if envelope_triggers is not None:
        record["envelope_triggers"] = list(envelope_triggers)
    return record


def _write_config(path: Path, noise_gate: dict | None) -> None:
    lines = [
        "code_layer:",
        "  mode: v2_tlsh",
        "  window: 5",
        "  norm_divisor: 300",
    ]
    if noise_gate is not None:
        lines.append("noise_gate:")
        lines.append("  enabled: {}".format("true" if noise_gate.get("enabled") else "false"))
        triggers = noise_gate.get("reject_triggers") or []
        if triggers:
            rendered = "[{}]".format(", ".join(str(t) for t in triggers))
        else:
            rendered = "[]"
        lines.append("  reject_triggers: {}".format(rendered))
    lines.extend(
        [
            "stages:",
            "  screening:",
            "    features: [code]",
            "    metric: jaccard",
            "    threshold: 0.0",
            "  pairwise:",
            "    features: [code]",
            "    metric: jaccard",
            "    threshold: 0.0",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestShouldRejectByNoiseGate(unittest.TestCase):
    """Юнит-тесты на should_reject_by_noise_gate (чистая функция)."""

    def test_noise_gate_rejects_adware(self) -> None:
        """Если envelope_triggers содержит 'adware', запись отклоняется."""
        record = _make_app("APP-ADWARE", {"f1"}, envelope_triggers=["adware"])
        is_reject, reason = should_reject_by_noise_gate(record, ["adware", "fake"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:adware")

    def test_noise_gate_rejects_fake(self) -> None:
        """Если envelope_triggers содержит 'fake', запись отклоняется."""
        record = _make_app("APP-FAKE", {"f1"}, envelope_triggers=["fake"])
        is_reject, reason = should_reject_by_noise_gate(record, ["adware", "fake"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:fake")

    def test_noise_gate_passes_clean_app(self) -> None:
        """Если envelope_triggers пуст или не пересекается, запись проходит."""
        record_no_triggers = _make_app("APP-CLEAN", {"f1"})
        is_reject, reason = should_reject_by_noise_gate(record_no_triggers, ["adware", "fake"])
        self.assertFalse(is_reject)
        self.assertEqual(reason, "")

        record_other = _make_app("APP-OTHER", {"f1"}, envelope_triggers=["tpl_match"])
        is_reject, reason = should_reject_by_noise_gate(record_other, ["adware", "fake"])
        self.assertFalse(is_reject)
        self.assertEqual(reason, "")

    def test_noise_gate_respects_custom_triggers(self) -> None:
        """Список reject_triggers учитывается — shim, severe_packing и пр.

        Проверяем: (1) custom-триггер ловится; (2) первый в конфиге
        побеждает при множественном пересечении; (3) пустой
        reject_triggers не отклоняет даже при наличии envelope-triggers;
        (4) envelope_triggers могут читаться из noise_profile_envelope.
        """
        # (1) shim
        record_shim = _make_app("APP-SHIM", {"f1"}, envelope_triggers=["shim"])
        is_reject, reason = should_reject_by_noise_gate(record_shim, ["shim", "severe_packing"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:shim")

        # (1) severe_packing
        record_pack = _make_app("APP-PACK", {"f1"}, envelope_triggers=["severe_packing"])
        is_reject, reason = should_reject_by_noise_gate(record_pack, ["shim", "severe_packing"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:severe_packing")

        # (2) приоритет по порядку reject_triggers
        record_multi = _make_app("APP-MULTI", {"f1"}, envelope_triggers=["fake", "adware"])
        is_reject, reason = should_reject_by_noise_gate(record_multi, ["adware", "fake"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:adware")

        # (3) пустой reject_triggers
        record_any = _make_app("APP-ANY", {"f1"}, envelope_triggers=["adware"])
        is_reject, reason = should_reject_by_noise_gate(record_any, [])
        self.assertFalse(is_reject)

        # (4) триггеры из noise_profile_envelope
        record_env = {
            "app_id": "APP-ENV",
            "layers": _full_layers({"f1"}),
            "apk_path": "/abs/app_env.apk",
            "noise_profile_envelope": {
                "schema_version": "nc-v1",
                "detector_source": "library_view_v2",
                "envelope_triggers": ["adware"],
            },
        }
        is_reject, reason = should_reject_by_noise_gate(record_env, ["adware"])
        self.assertTrue(is_reject)
        self.assertEqual(reason, "noise_gate:adware")


class TestNoiseGateConfigWiring(unittest.TestCase):
    """Интеграция: noise_gate читается из cascade-config и применяется в run_screening."""

    def test_noise_gate_disabled_passes_all(self) -> None:
        """enabled=false не фильтрует записи."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cascade.yaml"
            _write_config(
                config_path,
                noise_gate={"enabled": False, "reject_triggers": ["adware", "fake"]},
            )

            app_records = [
                _make_app("APP-1", {"f1", "f2"}, envelope_triggers=["adware"]),
                _make_app("APP-2", {"f2", "f3"}, envelope_triggers=["fake"]),
                _make_app("APP-3", {"f3", "f4"}, envelope_triggers=[]),
            ]

            candidate_list = run_screening(
                cascade_config_path=config_path,
                app_records=app_records,
            )

            # Должны построиться пары от всех трёх app (adware/fake не отсечены).
            app_ids_seen = set()
            for row in candidate_list:
                app_ids_seen.add(row.get("app_a"))
                app_ids_seen.add(row.get("app_b"))
            self.assertIn("APP-1", app_ids_seen)
            self.assertIn("APP-2", app_ids_seen)
            self.assertIn("APP-3", app_ids_seen)

    def test_noise_gate_writes_rejected_artifact(self) -> None:
        """При enabled=true отклонённые записи пишутся в noise_rejected.json,
        а screening_run_summary.json содержит noise_gate_total_rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cascade.yaml"
            _write_config(
                config_path,
                noise_gate={"enabled": True, "reject_triggers": ["adware", "fake"]},
            )
            artifact_dir = tmp_path / "artifacts"

            app_records = [
                _make_app("APP-1", {"f1", "f2"}, envelope_triggers=["adware"]),
                _make_app("APP-2", {"f2", "f3"}, envelope_triggers=["fake"]),
                _make_app("APP-3", {"f3", "f4"}, envelope_triggers=[]),
                _make_app("APP-4", {"f4", "f5"}, envelope_triggers=["tpl_match"]),
            ]

            candidate_list = run_screening(
                cascade_config_path=config_path,
                app_records=app_records,
                artifact_dir=artifact_dir,
            )

            # Остались только APP-3 и APP-4 (adware/fake отсечены).
            app_ids_seen = set()
            for row in candidate_list:
                app_ids_seen.add(row.get("app_a"))
                app_ids_seen.add(row.get("app_b"))
            self.assertNotIn("APP-1", app_ids_seen)
            self.assertNotIn("APP-2", app_ids_seen)
            self.assertIn("APP-3", app_ids_seen)
            self.assertIn("APP-4", app_ids_seen)

            # Файл noise_rejected.json существует и содержит две записи.
            rejected_path = artifact_dir / "noise_rejected.json"
            self.assertTrue(rejected_path.exists(), "noise_rejected.json должен существовать")
            rejected_payload = json.loads(rejected_path.read_text(encoding="utf-8"))
            self.assertEqual(rejected_payload["schema_version"], "noise-gate-v1")
            self.assertEqual(rejected_payload["total_rejected"], 2)
            self.assertEqual(rejected_payload["reject_triggers"], ["adware", "fake"])
            rejected_ids = {entry["app_id"] for entry in rejected_payload["rejected"]}
            self.assertEqual(rejected_ids, {"APP-1", "APP-2"})
            # Причины корректные.
            reasons = {entry["app_id"]: entry["reason"] for entry in rejected_payload["rejected"]}
            self.assertEqual(reasons["APP-1"], "noise_gate:adware")
            self.assertEqual(reasons["APP-2"], "noise_gate:fake")

            # Summary содержит noise_gate_total_rejected.
            summary_path = artifact_dir / "screening_run_summary.json"
            self.assertTrue(summary_path.exists(), "screening_run_summary.json должен существовать")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["noise_gate_enabled"])
            self.assertEqual(summary["noise_gate_total_input"], 4)
            self.assertEqual(summary["noise_gate_total_rejected"], 2)
            self.assertEqual(summary["noise_gate_total_passed"], 2)
            self.assertEqual(summary["noise_gate_reject_triggers"], ["adware", "fake"])


class TestExtractNoiseGateConfig(unittest.TestCase):
    """Точечно: чтение секции noise_gate."""

    def test_missing_section_means_disabled(self) -> None:
        enabled, triggers = _extract_noise_gate_config({"code_layer": {"mode": "v2_tlsh"}})
        self.assertFalse(enabled)
        self.assertEqual(triggers, [])

    def test_disabled_explicit(self) -> None:
        enabled, triggers = _extract_noise_gate_config(
            {"noise_gate": {"enabled": False, "reject_triggers": ["adware"]}}
        )
        self.assertFalse(enabled)
        self.assertEqual(triggers, ["adware"])

    def test_enabled_with_triggers(self) -> None:
        enabled, triggers = _extract_noise_gate_config(
            {"noise_gate": {"enabled": True, "reject_triggers": ["adware", "fake"]}}
        )
        self.assertTrue(enabled)
        self.assertEqual(triggers, ["adware", "fake"])


class TestApplyNoiseGateHelper(unittest.TestCase):
    """Точечно: apply_noise_gate разделяет passed/rejected."""

    def test_separates_passed_and_rejected(self) -> None:
        app_records = [
            _make_app("APP-1", {"f1"}, envelope_triggers=["adware"]),
            _make_app("APP-2", {"f1"}, envelope_triggers=[]),
        ]
        passed, rejected = apply_noise_gate(app_records, ["adware"])
        self.assertEqual([r["app_id"] for r in passed], ["APP-2"])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["app_id"], "APP-1")
        self.assertEqual(rejected[0]["reason"], "noise_gate:adware")


if __name__ == "__main__":
    unittest.main()

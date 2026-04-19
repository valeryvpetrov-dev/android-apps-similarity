"""Tests for APKiD adapter (EXEC-083-APKID-ADAPTER).

Тесты работают без реального apkid (или yara-python-dex): они мокают
subprocess.run и shutil.which. Полная интеграция с живым apkid —
отдельный шаг (см. inbox/research/apkid-gate-design-2026-04-19.md).
"""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from script import apkid_adapter


# ---------------------------------------------------------------------------
# apkid_available()
# ---------------------------------------------------------------------------

class ApkidAvailableTests(unittest.TestCase):
    """Контракт apkid_available."""

    def test_apkid_available_returns_bool(self) -> None:
        """Возвращает строго bool вне зависимости от окружения."""
        with mock.patch.object(apkid_adapter.shutil, "which", return_value=None):
            with mock.patch.dict("sys.modules", {"apkid": None}):
                result = apkid_adapter.apkid_available()
        self.assertIsInstance(result, bool)

    def test_apkid_available_true_when_on_path(self) -> None:
        """Когда shutil.which возвращает путь — True."""
        with mock.patch.object(
            apkid_adapter.shutil, "which", return_value="/usr/local/bin/apkid"
        ):
            self.assertTrue(apkid_adapter.apkid_available())


# ---------------------------------------------------------------------------
# detect_classifiers() — schema, not_available, timeout
# ---------------------------------------------------------------------------

class DetectClassifiersSchemaTests(unittest.TestCase):
    """Контракт ключей в detect_classifiers."""

    def _fake_completed(self, stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["apkid"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_detect_classifiers_returns_expected_keys(self) -> None:
        """Через мок subprocess — все ожидаемые ключи присутствуют."""
        payload = {
            "apkid_version": "3.1.0",
            "rules_sha256": "abc123",
            "files": [
                {
                    "filename": "input.apk",
                    "matches": {
                        "compiler": ["r8"],
                        "obfuscator": ["DexGuard"],
                        "packer": ["Bangcle"],
                        "anti_debug": ["ptrace_check"],
                        "anti_vm": ["qemu_props"],
                    },
                }
            ],
        }
        stdout = json.dumps(payload)
        with mock.patch.object(apkid_adapter, "apkid_available", return_value=True):
            with mock.patch.object(
                apkid_adapter.subprocess,
                "run",
                return_value=self._fake_completed(stdout),
            ):
                result = apkid_adapter.detect_classifiers("/tmp/any.apk")

        expected_keys = {
            "packers",
            "obfuscators",
            "compilers",
            "anti_debug",
            "anti_vm",
            "apkid_version",
            "rules_sha256",
            "status",
            "elapsed_sec",
            "raw_stdout",
        }
        self.assertEqual(expected_keys, expected_keys & set(result.keys()))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["packers"], ["Bangcle"])
        self.assertEqual(result["obfuscators"], ["DexGuard"])
        self.assertEqual(result["compilers"], ["r8"])
        self.assertEqual(result["apkid_version"], "3.1.0")
        self.assertEqual(result["rules_sha256"], "abc123")


class DetectClassifiersNotAvailableTests(unittest.TestCase):
    """detect_classifiers без apkid возвращает not_available."""

    def test_detect_classifiers_not_available_when_apkid_missing(self) -> None:
        with mock.patch.object(apkid_adapter, "apkid_available", return_value=False):
            result = apkid_adapter.detect_classifiers("/tmp/whatever.apk")
        self.assertEqual(result["status"], "not_available")
        self.assertEqual(result["packers"], [])
        self.assertEqual(result["obfuscators"], [])
        self.assertEqual(result["compilers"], [])
        self.assertEqual(result["apkid_version"], None)
        self.assertEqual(result["rules_sha256"], None)


class DetectClassifiersTimeoutTests(unittest.TestCase):
    """detect_classifiers при таймауте возвращает timeout."""

    def test_detect_classifiers_timeout_returns_status_timeout(self) -> None:
        with mock.patch.object(apkid_adapter, "apkid_available", return_value=True):
            with mock.patch.object(
                apkid_adapter.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="apkid", timeout=10),
            ):
                result = apkid_adapter.detect_classifiers(
                    "/tmp/any.apk", timeout_sec=10
                )
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["packers"], [])
        self.assertEqual(result["obfuscators"], [])


# ---------------------------------------------------------------------------
# decide_gate() — policy matrix
# ---------------------------------------------------------------------------

class DecideGatePackerTests(unittest.TestCase):
    """Упаковщик → blocked (жёсткая политика)."""

    def test_decide_gate_blocks_on_packer(self) -> None:
        classification = {
            "packers": ["Bangcle"],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "blocked")
        self.assertEqual(decision["recommended_detector"], "none")
        self.assertIn("Bangcle", decision["reason"])
        self.assertEqual(decision["apkid_signals"]["packers"], ["Bangcle"])


class DecideGateObfuscatorTests(unittest.TestCase):
    """Обфускатор без упаковщика → obfuscator_detected + libloom."""

    def test_decide_gate_obfuscator_without_packer_routes_to_libloom(self) -> None:
        classification = {
            "packers": [],
            "obfuscators": ["DexGuard"],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "obfuscator_detected")
        self.assertEqual(decision["recommended_detector"], "libloom")
        self.assertIn("DexGuard", decision["reason"])
        self.assertEqual(decision["apkid_signals"]["obfuscators"], ["DexGuard"])


class DecideGateCleanTests(unittest.TestCase):
    """Без упаковщика и обфускатора → clean + prefix_catalog."""

    def test_decide_gate_clean_routes_to_prefix_catalog(self) -> None:
        classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "clean")
        self.assertEqual(decision["recommended_detector"], "prefix_catalog")


class DecideGatePackerAndObfuscatorTests(unittest.TestCase):
    """Упаковщик + обфускатор одновременно → blocked (packer приоритетнее)."""

    def test_decide_gate_packer_wins_over_obfuscator(self) -> None:
        classification = {
            "packers": ["Bangcle"],
            "obfuscators": ["DexGuard"],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "blocked")
        self.assertEqual(decision["recommended_detector"], "none")


# ---------------------------------------------------------------------------
# APKID-ADAPTER-EXT: manipulator as weak obfuscator signal
# ---------------------------------------------------------------------------

class DetectClassifiersManipulatorKeyTests(unittest.TestCase):
    """detect_classifiers возвращает ключ `manipulators` в dict-ответе."""

    def _fake_completed(self, stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["apkid"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_detect_classifiers_result_has_manipulators_key(self) -> None:
        """Через мок subprocess — ключ `manipulators` присутствует в ответе."""
        payload = {
            "apkid_version": "3.1.0",
            "rules_sha256": "abc123",
            "files": [
                {
                    "filename": "input.apk",
                    "matches": {
                        "compiler": ["r8"],
                    },
                }
            ],
        }
        stdout = json.dumps(payload)
        with mock.patch.object(apkid_adapter, "apkid_available", return_value=True):
            with mock.patch.object(
                apkid_adapter.subprocess,
                "run",
                return_value=self._fake_completed(stdout),
            ):
                result = apkid_adapter.detect_classifiers("/tmp/any.apk")

        self.assertIn("manipulators", result)
        self.assertIsInstance(result["manipulators"], list)
        self.assertEqual(result["manipulators"], [])


class DetectClassifiersManipulatorPayloadTests(unittest.TestCase):
    """detect_classifiers агрегирует manipulator-правила из сырого APKiD JSON."""

    def _fake_completed(self, stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["apkid"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_detect_classifiers_extracts_resources_confusion(self) -> None:
        """Вход с `manipulator: ["Resources Confusion"]` → manipulators == ["Resources Confusion"]."""
        payload = {
            "apkid_version": "3.1.0",
            "rules_sha256": "abc123",
            "files": [
                {
                    "filename": "input.apk",
                    "matches": {
                        "manipulator": ["Resources Confusion"],
                    },
                },
                {
                    "filename": "input.apk!classes.dex",
                    "matches": {
                        "compiler": ["r8"],
                    },
                },
            ],
        }
        stdout = json.dumps(payload)
        with mock.patch.object(apkid_adapter, "apkid_available", return_value=True):
            with mock.patch.object(
                apkid_adapter.subprocess,
                "run",
                return_value=self._fake_completed(stdout),
            ):
                result = apkid_adapter.detect_classifiers("/tmp/any.apk")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["manipulators"], ["Resources Confusion"])
        # Соседние категории остались пустыми.
        self.assertEqual(result["packers"], [])
        self.assertEqual(result["obfuscators"], [])


class DecideGateManipulatorTests(unittest.TestCase):
    """Manipulator без packer/obfuscator → manipulator_detected + libloom."""

    def test_decide_gate_manipulator_only_routes_to_libloom(self) -> None:
        classification = {
            "packers": [],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
            "manipulators": ["Resources Confusion"],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "manipulator_detected")
        self.assertEqual(decision["recommended_detector"], "libloom")
        self.assertIn("Resources Confusion", decision["reason"])
        self.assertEqual(
            decision["apkid_signals"]["manipulators"], ["Resources Confusion"]
        )


class DecideGatePackerBeatsManipulatorTests(unittest.TestCase):
    """Packer + manipulator одновременно → blocked (packer приоритетнее)."""

    def test_decide_gate_packer_wins_over_manipulator(self) -> None:
        classification = {
            "packers": ["pack"],
            "obfuscators": [],
            "compilers": ["r8"],
            "anti_debug": [],
            "anti_vm": [],
            "manipulators": ["X"],
        }
        decision = apkid_adapter.decide_gate(classification)
        self.assertEqual(decision["gate_status"], "blocked")
        self.assertEqual(decision["recommended_detector"], "none")


# ---------------------------------------------------------------------------
# apply_apkid_gate() — envelope integration
# ---------------------------------------------------------------------------

class ApplyApkidGateBlockedTests(unittest.TestCase):
    """apply_apkid_gate при blocked выставляет detector_blocked+reason."""

    def test_apply_apkid_gate_blocked_marks_envelope(self) -> None:
        from script import noise_profile_envelope

        decision = {
            "gate_status": "blocked",
            "recommended_detector": "none",
            "reason": "packer detected: Bangcle",
            "apkid_signals": {"packers": ["Bangcle"]},
        }
        envelope = {"schema_version": "nc-v1", "status": "success"}

        merged = noise_profile_envelope.apply_apkid_gate(decision, envelope)

        self.assertTrue(merged.get("detector_blocked"))
        self.assertEqual(merged.get("detector_block_reason"), "packer_detected")
        self.assertEqual(merged.get("apkid_gate_status"), "blocked")
        self.assertEqual(merged.get("apkid_recommended_detector"), "none")
        # Исходные ключи не потеряны.
        self.assertEqual(merged.get("schema_version"), "nc-v1")
        self.assertEqual(merged.get("status"), "success")


# ---------------------------------------------------------------------------
# Integration: noise_profile_envelope builds without crash when apkid absent
# ---------------------------------------------------------------------------

class NoiseProfileEnvelopeMissingApkidTests(unittest.TestCase):
    """Envelope строится без падений при моке отсутствующего apkid."""

    def test_envelope_builds_when_apkid_unavailable(self) -> None:
        from script import noise_profile_envelope

        with mock.patch.object(apkid_adapter, "apkid_available", return_value=False):
            classification = apkid_adapter.detect_classifiers("/tmp/missing.apk")
            decision = apkid_adapter.decide_gate(classification)

        # clean policy when apkid returns empty classification.
        self.assertEqual(classification["status"], "not_available")
        self.assertEqual(decision["gate_status"], "clean")

        envelope = {"schema_version": "nc-v1"}
        merged = noise_profile_envelope.apply_apkid_gate(decision, envelope)
        # envelope не ломается и получает informational-поля.
        self.assertEqual(merged["apkid_gate_status"], "clean")
        self.assertEqual(merged["apkid_recommended_detector"], "prefix_catalog")
        self.assertFalse(merged.get("detector_blocked", False))


if __name__ == "__main__":
    unittest.main()

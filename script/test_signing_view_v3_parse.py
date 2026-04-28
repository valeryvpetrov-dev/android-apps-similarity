#!/usr/bin/env python3
"""ARCH-30-V3-SIGNATURE-PARSE: тесты разбора APK Signing Block v3 + lineage.

Проверяемый контракт:
  - ``parse_signing_scheme_v3(apk_path) -> dict`` возвращает
    ``{found: bool, signers: list[dict], rotation_lineage: list[dict] | None}``.
  - На синтетическом APK с v3-блоком и proof-of-rotation в дополнительных
    атрибутах ``rotation_lineage`` содержит fingerprint обоих ключей.
  - ``compare_signatures`` с lineage: пара (original, legitimate_rotation)
    даёт ``status='match_via_rotation'`` (не ``mismatch``).
  - При отсутствии v3-блока — ``parse_signing_scheme_v3 → {found: False}``;
    ``_detect_signing_scheme`` различает 'v1' / 'v2' / 'v3' / 'v3_with_rotation'
    без conflate v2/v3.

Минимально-жизнеспособная версия (если real-world structure окажется слишком
сложной): тесты на signers list без полного proof-of-rotation допустимы;
главное — _detect_signing_scheme разделяет v2 и v3.
"""
from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from signing_view import (
    parse_signing_scheme_v3,
    compare_signatures,
    _detect_signing_scheme,
)


APK_SIG_V3_BLOCK_ID = 0xF05368C0
APK_SIG_V2_BLOCK_ID = 0x7109871A
PROOF_OF_ROTATION_ATTR_ID = 0x3BA06F8C
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"


# -----------------------------------------------------------------------------
# Синтетический построитель APK Signing Block v3
# -----------------------------------------------------------------------------

def _len_prefixed(data: bytes, prefix_size: int = 4) -> bytes:
    """uint32 length || data (для prefix_size=4) или uint64 длина (prefix_size=8)."""
    if prefix_size == 4:
        return struct.pack("<I", len(data)) + data
    return struct.pack("<Q", len(data)) + data


def _make_fake_certificate(cert_id: int) -> bytes:
    """Сгенерировать псевдо-сертификат фиксированной длины (X.509 DER заглушка).

    Реальный X.509 DER здесь не нужен: parse_signing_scheme_v3 считает
    SHA-256 от байт сертификата как fingerprint. Синтетический cert байт
    воспроизводимо отличается по cert_id и определяет fingerprint.
    """
    return b"FAKE_CERT_" + struct.pack("<I", cert_id) + b"\x00" * 32


def _build_proof_of_rotation_value(cert_chain: list[bytes]) -> bytes:
    """Собрать значение атрибута proof-of-rotation v3.

    Структура (упрощённая, парсер использует только список сертификатов):
      - length-prefixed sequence of levels
      - each level:
          - length-prefixed signed_data (contains length-prefixed certificate)
          - uint32 flags
          - uint32 sigAlgo
          - length-prefixed signature

    Парсер v3 lineage из этих уровней извлекает только сертификаты,
    остальное прозрачно проглатывается.
    """
    levels = b""
    for cert in cert_chain:
        signed_data = _len_prefixed(cert, prefix_size=4) + struct.pack("<I", 0x0103)  # cert + sigAlgo
        level = (
            _len_prefixed(signed_data, prefix_size=4)
            + struct.pack("<I", 0x0001)  # flags
            + struct.pack("<I", 0x0103)  # sigAlgo
            + _len_prefixed(b"FAKE_SIG_BYTES", prefix_size=4)
        )
        levels += _len_prefixed(level, prefix_size=4)
    return levels  # последовательность level'ов


def _build_v3_signed_data(certs: list[bytes], rotation_chain: list[bytes] | None) -> bytes:
    """Собрать signed_data для v3 signer."""
    digests = _len_prefixed(b"", prefix_size=4)  # пустая последовательность digest'ов
    certs_seq = b"".join(_len_prefixed(c, prefix_size=4) for c in certs)
    certificates = _len_prefixed(certs_seq, prefix_size=4)
    min_sdk = struct.pack("<I", 28)
    max_sdk = struct.pack("<I", 0x7FFFFFFF)

    if rotation_chain is not None:
        rotation_value = _build_proof_of_rotation_value(rotation_chain)
        attrs_inner = (
            struct.pack("<I", PROOF_OF_ROTATION_ATTR_ID) + rotation_value
        )
        # один additional attribute длины (4 + len(rotation_value))
        attrs_seq = _len_prefixed(attrs_inner, prefix_size=4)
    else:
        attrs_seq = b""
    additional_attrs = _len_prefixed(attrs_seq, prefix_size=4)

    return digests + certificates + min_sdk + max_sdk + additional_attrs


def _build_v3_signer(certs: list[bytes], rotation_chain: list[bytes] | None) -> bytes:
    """Собрать одного v3-signer."""
    signed_data = _build_v3_signed_data(certs, rotation_chain)
    min_sdk = struct.pack("<I", 28)
    max_sdk = struct.pack("<I", 0x7FFFFFFF)
    signatures = _len_prefixed(b"", prefix_size=4)
    public_key = _len_prefixed(b"FAKE_PK", prefix_size=4)

    return (
        _len_prefixed(signed_data, prefix_size=4)
        + min_sdk
        + max_sdk
        + signatures
        + public_key
    )


def _build_v3_block_value(signers: list[bytes]) -> bytes:
    """Собрать value APK Signing Block v3: length-prefixed sequence of signers."""
    signers_seq = b"".join(_len_prefixed(s, prefix_size=4) for s in signers)
    return _len_prefixed(signers_seq, prefix_size=4)


def _build_apk_signing_block(pairs: list[tuple[int, bytes]]) -> bytes:
    """Собрать APK Signing Block из списка (id, value)-пар.

    Формат блока:
      uint64 size_of_block (excluding this field)
      sequence of pairs:
        uint64 length_of_pair
        uint32 id
        bytes value (length - 4 bytes)
      uint64 size_of_block (same)
      bytes[16] magic
    """
    body = b""
    for pair_id, value in pairs:
        pair_payload = struct.pack("<I", pair_id) + value
        body += struct.pack("<Q", len(pair_payload)) + pair_payload
    # size_of_block = len(body) + 8 (вторая копия size) + 16 (magic)
    size_of_block = len(body) + 8 + 16
    return (
        struct.pack("<Q", size_of_block)
        + body
        + struct.pack("<Q", size_of_block)
        + APK_SIG_BLOCK_MAGIC
    )


def _write_synthetic_apk(
    apk_path: Path,
    *,
    sig_block: bytes | None,
    include_meta_inf_v1: bool = False,
) -> None:
    """Записать минимальный синтетический APK с опциональным sig_block.

    Сначала ZIP с classes.dex+AndroidManifest.xml, затем (если sig_block задан)
    мы вставляем его между Local File Headers и Central Directory.
    """
    # 1) Сначала пишем минимальный zip
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("AndroidManifest.xml", b"<manifest/>")
        zf.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 24)
        if include_meta_inf_v1:
            zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
            zf.writestr("META-INF/CERT.SF", b"sig")
            zf.writestr("META-INF/CERT.RSA", b"\x00\x01\x02\x03fake_cert")

    if sig_block is None:
        return

    # 2) Читаем zip, парсим EOCD, ищем offset central directory
    data = apk_path.read_bytes()
    eocd_idx = data.rfind(b"PK\x05\x06")
    if eocd_idx < 0:
        raise RuntimeError("synthetic APK: EOCD не найден")
    eocd = data[eocd_idx : eocd_idx + 22]
    cd_size, cd_off = struct.unpack("<II", eocd[12:20])

    # 3) Вставляем sig_block перед central directory
    new_data = bytearray()
    new_data.extend(data[:cd_off])
    new_data.extend(sig_block)
    new_data.extend(data[cd_off:])
    # 4) Корректируем CD offset в EOCD
    new_cd_off = cd_off + len(sig_block)
    new_eocd_idx = eocd_idx + len(sig_block)
    struct.pack_into("<I", new_data, new_eocd_idx + 16, new_cd_off)

    apk_path.write_bytes(bytes(new_data))


# -----------------------------------------------------------------------------
# Тесты
# -----------------------------------------------------------------------------


class TestParseSigningSchemeV3(unittest.TestCase):
    """(a) parse_signing_scheme_v3 возвращает корректную структуру."""

    def test_v3_only_signer_no_rotation(self) -> None:
        """APK с одним v3-signer и без proof-of-rotation: signers есть, lineage None."""
        cert = _make_fake_certificate(1)
        signer = _build_v3_signer([cert], rotation_chain=None)
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3_no_rotation.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            result = parse_signing_scheme_v3(apk_path)

        self.assertIsInstance(result, dict)
        self.assertTrue(result["found"], "v3-блок должен быть найден")
        self.assertEqual(len(result["signers"]), 1)
        self.assertIn("fingerprint", result["signers"][0])
        # без rotation_chain → lineage отсутствует или None
        self.assertIn("rotation_lineage", result)
        self.assertIsNone(result["rotation_lineage"])

    def test_v3_with_rotation_lineage_two_keys(self) -> None:
        """(b) Два сертификата в proof-of-rotation: lineage содержит оба fingerprint'а."""
        cert_old = _make_fake_certificate(100)
        cert_new = _make_fake_certificate(200)
        signer = _build_v3_signer([cert_new], rotation_chain=[cert_old, cert_new])
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3_with_rotation.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            result = parse_signing_scheme_v3(apk_path)

        self.assertTrue(result["found"])
        lineage = result["rotation_lineage"]
        self.assertIsNotNone(lineage, "rotation_lineage должен быть populated")
        fingerprints = {entry["fingerprint"] for entry in lineage}
        expected_old = hashlib.sha256(cert_old).hexdigest()
        expected_new = hashlib.sha256(cert_new).hexdigest()
        self.assertIn(expected_old, fingerprints)
        self.assertIn(expected_new, fingerprints)


class TestCompareSignaturesViaRotation(unittest.TestCase):
    """(c) compare_signatures с rotation lineage: match_via_rotation."""

    def test_match_via_rotation_same_developer_two_keys(self) -> None:
        """Пара (original, legitimate_rotation) даёт status='match_via_rotation'.

        Сценарий: оба APK от одного разработчика, у обоих общая rotation lineage
        (старый ключ K1 → новый ключ K2). У APK-A signer = K1, у APK-B signer = K2,
        но оба декларируют lineage [K1, K2]. compare_signatures должен видеть
        пересечение и не давать ложный 'mismatch'.
        """
        cert_k1 = _make_fake_certificate(1)
        cert_k2 = _make_fake_certificate(2)
        fp_k1 = hashlib.sha256(cert_k1).hexdigest()
        fp_k2 = hashlib.sha256(cert_k2).hexdigest()

        sig_a = {
            "fingerprint": fp_k1,
            "rotation_lineage": [{"fingerprint": fp_k1}, {"fingerprint": fp_k2}],
        }
        sig_b = {
            "fingerprint": fp_k2,
            "rotation_lineage": [{"fingerprint": fp_k1}, {"fingerprint": fp_k2}],
        }
        result = compare_signatures(sig_a, sig_b)
        self.assertEqual(result["status"], "match_via_rotation")
        # score должен быть положительным (матч), не 0.0
        self.assertGreater(result["score"], 0.0)

    def test_string_inputs_remain_backward_compatible(self) -> None:
        """compare_signatures на старых string-входах сохраняет прежний контракт."""
        # match
        r_match = compare_signatures("abcd", "abcd")
        self.assertEqual(r_match, {"score": 1.0, "status": "match"})
        # mismatch
        r_mismatch = compare_signatures("abcd", "ffff")
        self.assertEqual(r_mismatch, {"score": 0.0, "status": "mismatch"})
        # both None
        r_both = compare_signatures(None, None)
        self.assertEqual(r_both["status"], "both_missing")

    def test_lineage_no_overlap_is_mismatch(self) -> None:
        """Если lineage не пересекаются — старая семантика mismatch остаётся."""
        sig_a = {
            "fingerprint": "aa",
            "rotation_lineage": [{"fingerprint": "aa"}],
        }
        sig_b = {
            "fingerprint": "bb",
            "rotation_lineage": [{"fingerprint": "bb"}],
        }
        result = compare_signatures(sig_a, sig_b)
        self.assertEqual(result["status"], "mismatch")


class TestDetectSigningSchemeNoConflate(unittest.TestCase):
    """(d) _detect_signing_scheme различает 'v1' / 'v2' / 'v3' / 'v3_with_rotation'."""

    def test_v3_apk_returns_v3(self) -> None:
        """APK с v3-блоком (без rotation) → 'v3' (не 'v2')."""
        cert = _make_fake_certificate(11)
        signer = _build_v3_signer([cert], rotation_chain=None)
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            scheme = _detect_signing_scheme(apk_path)
        self.assertEqual(scheme, "v3")

    def test_v3_with_rotation_returns_v3_with_rotation(self) -> None:
        """APK с v3 + proof-of-rotation → 'v3_with_rotation'."""
        cert_old = _make_fake_certificate(31)
        cert_new = _make_fake_certificate(32)
        signer = _build_v3_signer([cert_new], rotation_chain=[cert_old, cert_new])
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3rot.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            scheme = _detect_signing_scheme(apk_path)
        self.assertEqual(scheme, "v3_with_rotation")

    def test_v2_only_apk_returns_v2(self) -> None:
        """APK только с v2 ID (без v3) → 'v2', не 'v3'."""
        v2_value = b"\x00" * 32  # минимальный заглушка-payload
        block = _build_apk_signing_block([(APK_SIG_V2_BLOCK_ID, v2_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v2.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            scheme = _detect_signing_scheme(apk_path)
        self.assertEqual(scheme, "v2")

    def test_v1_only_apk_returns_v1(self) -> None:
        """APK с META-INF/*.RSA, без APK Sig Block → 'v1'."""
        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v1.apk"
            _write_synthetic_apk(apk_path, sig_block=None, include_meta_inf_v1=True)
            scheme = _detect_signing_scheme(apk_path)
        self.assertEqual(scheme, "v1")

    def test_no_v3_block_returns_found_false(self) -> None:
        """parse_signing_scheme_v3 на APK без v3 → {found: False}."""
        # APK только с v2, без v3
        v2_value = b"\x00" * 16
        block = _build_apk_signing_block([(APK_SIG_V2_BLOCK_ID, v2_value)])
        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "no_v3.apk"
            _write_synthetic_apk(apk_path, sig_block=block)
            result = parse_signing_scheme_v3(apk_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["found"])
        self.assertEqual(result["signers"], [])
        self.assertIsNone(result["rotation_lineage"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

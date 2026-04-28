#!/usr/bin/env python3
"""ARCH-31-V3-SCREENING-INTEGRATION: тесты v3-aware screening_runner.

Проверяемый контракт (после переноса screening_runner на signing_view ARCH-30):

  (a) ``_detect_signing_scheme`` в screening_runner для синтетического APK с
      v3-блоком и proof-of-rotation возвращает ``'v3_with_rotation'`` (не
      ``'v2'`` и не ``None``). До рефакторинга локальная функция работала по
      META-INF и conflate'ила v2/v3.

  (b) ``_extract_signing_tokens`` для пары APK (original, legitimate-rotation)
      добавляет в токены fingerprint'ы из rotation_lineage. Жаккар по
      ``signing_lineage_fp:*`` пересекается, то есть пара не попадает в
      mismatch по подписи на этапе первичного отбора.

  (c) Обратная совместимость: для v1-only APK (META-INF/*.RSA) функции не
      падают, ``_detect_signing_scheme`` возвращает ``'v1'``,
      ``_extract_signing_tokens`` отдаёт ``signing_present:1`` и
      ``signing_scheme:v1`` без rotation lineage токенов.

Тесты используют синтетические APK, собираемые через те же helper'ы, что и
``test_signing_view_v3_parse`` (структура APK Signing Block v3 + lineage).
Никаких реальных APK не требуется — тесты гонятся в чистом окружении CI.
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

from screening_runner import (  # noqa: E402
    _detect_signing_scheme,
    _extract_signing_tokens,
)


# -----------------------------------------------------------------------------
# Синтетический построитель APK Signing Block v3 (повторяет
# test_signing_view_v3_parse — копия, чтобы тесты screening_runner не
# зависели от соседнего test-файла; helper'ы достаточно компактны).
# -----------------------------------------------------------------------------

APK_SIG_V3_BLOCK_ID = 0xF05368C0
APK_SIG_V2_BLOCK_ID = 0x7109871A
PROOF_OF_ROTATION_ATTR_ID = 0x3BA06F8C
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"


def _len_prefixed(data: bytes, prefix_size: int = 4) -> bytes:
    if prefix_size == 4:
        return struct.pack("<I", len(data)) + data
    return struct.pack("<Q", len(data)) + data


def _make_fake_certificate(cert_id: int) -> bytes:
    return b"FAKE_CERT_" + struct.pack("<I", cert_id) + b"\x00" * 32


def _build_proof_of_rotation_value(cert_chain: list[bytes]) -> bytes:
    levels = b""
    for cert in cert_chain:
        signed_data = _len_prefixed(cert, prefix_size=4) + struct.pack("<I", 0x0103)
        level = (
            _len_prefixed(signed_data, prefix_size=4)
            + struct.pack("<I", 0x0001)
            + struct.pack("<I", 0x0103)
            + _len_prefixed(b"FAKE_SIG_BYTES", prefix_size=4)
        )
        levels += _len_prefixed(level, prefix_size=4)
    return levels


def _build_v3_signed_data(certs: list[bytes], rotation_chain: list[bytes] | None) -> bytes:
    digests = _len_prefixed(b"", prefix_size=4)
    certs_seq = b"".join(_len_prefixed(c, prefix_size=4) for c in certs)
    certificates = _len_prefixed(certs_seq, prefix_size=4)
    min_sdk = struct.pack("<I", 28)
    max_sdk = struct.pack("<I", 0x7FFFFFFF)

    if rotation_chain is not None:
        rotation_value = _build_proof_of_rotation_value(rotation_chain)
        attrs_inner = (
            struct.pack("<I", PROOF_OF_ROTATION_ATTR_ID) + rotation_value
        )
        attrs_seq = _len_prefixed(attrs_inner, prefix_size=4)
    else:
        attrs_seq = b""
    additional_attrs = _len_prefixed(attrs_seq, prefix_size=4)

    return digests + certificates + min_sdk + max_sdk + additional_attrs


def _build_v3_signer(certs: list[bytes], rotation_chain: list[bytes] | None) -> bytes:
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
    signers_seq = b"".join(_len_prefixed(s, prefix_size=4) for s in signers)
    return _len_prefixed(signers_seq, prefix_size=4)


def _build_apk_signing_block(pairs: list[tuple[int, bytes]]) -> bytes:
    body = b""
    for pair_id, value in pairs:
        pair_payload = struct.pack("<I", pair_id) + value
        body += struct.pack("<Q", len(pair_payload)) + pair_payload
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
    cert_v1_bytes: bytes | None = None,
) -> None:
    """Записать минимальный синтетический APK с опциональным sig_block.

    cert_v1_bytes: если задан и include_meta_inf_v1=True, его содержимое
    укладывается в META-INF/CERT.RSA (для тестов с детерминированным
    signing_prefix).
    """
    with zipfile.ZipFile(apk_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("AndroidManifest.xml", b"<manifest/>")
        zf.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 24)
        if include_meta_inf_v1:
            zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
            zf.writestr("META-INF/CERT.SF", b"sig")
            zf.writestr(
                "META-INF/CERT.RSA",
                cert_v1_bytes if cert_v1_bytes is not None else b"\x00\x01\x02\x03fake_cert",
            )

    if sig_block is None:
        return

    data = apk_path.read_bytes()
    eocd_idx = data.rfind(b"PK\x05\x06")
    if eocd_idx < 0:
        raise RuntimeError("synthetic APK: EOCD не найден")
    eocd = data[eocd_idx : eocd_idx + 22]
    cd_size, cd_off = struct.unpack("<II", eocd[12:20])

    new_data = bytearray()
    new_data.extend(data[:cd_off])
    new_data.extend(sig_block)
    new_data.extend(data[cd_off:])
    new_cd_off = cd_off + len(sig_block)
    new_eocd_idx = eocd_idx + len(sig_block)
    struct.pack_into("<I", new_data, new_eocd_idx + 16, new_cd_off)

    apk_path.write_bytes(bytes(new_data))


# -----------------------------------------------------------------------------
# Тесты
# -----------------------------------------------------------------------------


class TestScreeningRunnerDetectSchemeV3(unittest.TestCase):
    """(a) screening_runner._detect_signing_scheme не conflate'ит v2/v3."""

    def test_v3_with_rotation_returns_v3_with_rotation(self) -> None:
        """APK с v3 + proof-of-rotation → 'v3_with_rotation' (не 'v2'/'v1')."""
        cert_old = _make_fake_certificate(31)
        cert_new = _make_fake_certificate(32)
        signer = _build_v3_signer([cert_new], rotation_chain=[cert_old, cert_new])
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3rot.apk"
            _write_synthetic_apk(apk_path, sig_block=block)

            with zipfile.ZipFile(apk_path, "r") as archive:
                scheme = _detect_signing_scheme(apk_path, archive)

        self.assertEqual(
            scheme,
            "v3_with_rotation",
            "до ARCH-31 локальная функция conflate'ила и возвращала 'v2'/None",
        )

    def test_v3_without_rotation_returns_v3(self) -> None:
        """APK с v3 без proof-of-rotation → 'v3', не 'v2'."""
        cert = _make_fake_certificate(11)
        signer = _build_v3_signer([cert], rotation_chain=None)
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])

        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v3.apk"
            _write_synthetic_apk(apk_path, sig_block=block)

            with zipfile.ZipFile(apk_path, "r") as archive:
                scheme = _detect_signing_scheme(apk_path, archive)

        self.assertEqual(scheme, "v3")


class TestScreeningRunnerExtractTokensV3Lineage(unittest.TestCase):
    """(b) _extract_signing_tokens добавляет lineage fingerprints для rotation."""

    def _build_rotation_apk(
        self,
        tmpdir: Path,
        *,
        name: str,
        signer_cert: bytes,
        rotation_chain: list[bytes],
    ) -> Path:
        signer = _build_v3_signer([signer_cert], rotation_chain=rotation_chain)
        v3_value = _build_v3_block_value([signer])
        block = _build_apk_signing_block([(APK_SIG_V3_BLOCK_ID, v3_value)])
        apk_path = tmpdir / name
        _write_synthetic_apk(apk_path, sig_block=block)
        return apk_path

    def test_pair_with_rotation_lineage_yields_intersecting_tokens(self) -> None:
        """Пара (original signer K1, rotated signer K2), общая lineage [K1, K2].

        Ожидаем: набор токенов signing_lineage_fp:* у обоих APK пересекается,
        то есть Jaccard по signing-токенам не равен нулю — и пара не попадает
        в ложный mismatch по подписи на screening-этапе.
        """
        cert_k1 = _make_fake_certificate(101)
        cert_k2 = _make_fake_certificate(202)

        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            apk_a = self._build_rotation_apk(
                tmpdir,
                name="orig.apk",
                signer_cert=cert_k1,
                rotation_chain=[cert_k1, cert_k2],
            )
            apk_b = self._build_rotation_apk(
                tmpdir,
                name="rotated.apk",
                signer_cert=cert_k2,
                rotation_chain=[cert_k1, cert_k2],
            )

            with zipfile.ZipFile(apk_a, "r") as za:
                tokens_a = _extract_signing_tokens(apk_a, za)
            with zipfile.ZipFile(apk_b, "r") as zb:
                tokens_b = _extract_signing_tokens(apk_b, zb)

        lineage_a = {t for t in tokens_a if t.startswith("signing_lineage_fp:")}
        lineage_b = {t for t in tokens_b if t.startswith("signing_lineage_fp:")}

        self.assertTrue(
            lineage_a, "у APK-A с rotation должны быть signing_lineage_fp:* токены"
        )
        self.assertTrue(
            lineage_b, "у APK-B с rotation должны быть signing_lineage_fp:* токены"
        )
        intersection = lineage_a & lineage_b
        self.assertTrue(
            intersection,
            "lineage tokens должны пересекаться: оба APK декларируют одну "
            "rotation цепочку [K1, K2]",
        )

        # Дополнительно: схема — v3_with_rotation, не v2.
        self.assertIn("signing_scheme:v3_with_rotation", tokens_a)
        self.assertIn("signing_scheme:v3_with_rotation", tokens_b)


class TestScreeningRunnerBackwardCompatibleV1(unittest.TestCase):
    """(c) v1-only APK: signing_scheme:v1, нет rotation токенов, без падений."""

    def test_v1_only_apk_has_v1_scheme_no_lineage(self) -> None:
        cert_blob = b"\x00\x01\x02\x03fake_v1_cert_payload"
        with tempfile.TemporaryDirectory() as raw:
            apk_path = Path(raw) / "v1.apk"
            _write_synthetic_apk(
                apk_path,
                sig_block=None,
                include_meta_inf_v1=True,
                cert_v1_bytes=cert_blob,
            )

            with zipfile.ZipFile(apk_path, "r") as archive:
                scheme = _detect_signing_scheme(apk_path, archive)
                tokens = _extract_signing_tokens(apk_path, archive)

        self.assertEqual(scheme, "v1")
        self.assertIn("signing_present:1", tokens)
        self.assertIn("signing_scheme:v1", tokens)
        # signing_prefix присутствует и состоит из 8 hex
        prefix_tokens = [t for t in tokens if t.startswith("signing_prefix:")]
        self.assertEqual(len(prefix_tokens), 1)
        prefix = prefix_tokens[0].split(":", 1)[1]
        self.assertEqual(len(prefix), 8)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in prefix))
        # rotation lineage токенов быть не должно
        self.assertFalse(
            any(t.startswith("signing_lineage_fp:") for t in tokens),
            "у v1-only APK не должно быть signing_lineage_fp:* токенов",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

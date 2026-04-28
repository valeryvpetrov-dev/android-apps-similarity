#!/usr/bin/env python3
"""Сигнал первичного отбора: хеш сертификата подписи APK.

У оригинального приложения и его переупакованной версии почти всегда
разные цифровые подписи, потому что переупаковщик не имеет закрытого
ключа автора. Совпадение или различие хешей подписи — отдельный
независимый сигнал, дополняющий TLSH по коду.

Работает на любом APK без декомпиляции и внешних зависимостей.
"""
from __future__ import annotations

import hashlib
import logging
import struct
import zipfile
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

SIGNATURE_EXTENSIONS = ('.RSA', '.DSA', '.EC')

# APK Signing Block IDs.
# https://source.android.com/docs/security/features/apksigning/v3
APK_SIG_V2_BLOCK_ID = 0x7109871A
APK_SIG_V3_BLOCK_ID = 0xF05368C0
# Дополнительный атрибут v3-signed_data: proof-of-rotation lineage.
PROOF_OF_ROTATION_ATTR_ID = 0x3BA06F8C


def extract_apk_signature_hash(apk_path: Path) -> Optional[str]:
    """Извлечь SHA-256 хеш сертификата подписи APK.

    Ищет в META-INF файлы с расширениями .RSA/.DSA/.EC и возвращает
    SHA-256 содержимого первого из них в шестнадцатеричном виде.

    Возвращает None:
      - если APK не существует или не является файлом;
      - если APK не открывается как ZIP (порченый);
      - если в META-INF нет файлов сертификата (неподписанный APK
        или только v2/v3 подпись без классического META-INF).
    """
    path = Path(apk_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            meta_inf_certs = []
            for name in zf.namelist():
                if not name.startswith('META-INF/'):
                    continue
                upper = name.upper()
                if upper.endswith(SIGNATURE_EXTENSIONS):
                    meta_inf_certs.append(name)
            if not meta_inf_certs:
                return extract_apk_signatures_v2_fingerprint(path)
            # Читаем первый по алфавиту — стабильный выбор
            meta_inf_certs.sort()
            cert_bytes = zf.read(meta_inf_certs[0])
        return hashlib.sha256(cert_bytes).hexdigest()
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        logger.warning('signing_view: не удалось извлечь подпись из %s: %s', apk_path, exc)
        return None


APK_SIG_BLOCK_MAGIC = b'APK Sig Block 42'


def extract_apk_signatures_v2_fingerprint(apk_path: Path) -> Optional[str]:
    """Запасной путь для APK со схемой подписи v2/v3 без META-INF.

    Ищет в конце ZIP-контейнера магический идентификатор
    "APK Sig Block 42" и, если нашёл, возвращает SHA-256 содержимого
    APK Signing Block. Это грубое приближение для v2/v3.
    """
    try:
        data = Path(apk_path).read_bytes()
    except (OSError, FileNotFoundError):
        return None
    idx = data.rfind(APK_SIG_BLOCK_MAGIC)
    if idx < 0:
        return None
    # Блок содержит 8 байт длины перед магическим идентификатором
    # и ещё 8 байт длины после. Берём окрестность ±2048 байт — этого
    # достаточно для устойчивой идентификации подписи.
    start = max(0, idx - 4096)
    end = min(len(data), idx + len(APK_SIG_BLOCK_MAGIC) + 4096)
    return hashlib.sha256(data[start:end]).hexdigest()


def extract_signing_chain(apk_path: Path) -> list[dict]:
    """Извлечь полную цепочку сертификатов подписи APK.

    Для каждого сертификата в PKCS#7-подписи из META-INF/*.RSA (или .DSA, .EC)
    возвращает dict со строковыми полями:
      - issuer:  RFC4514-представление издателя (Issuer);
      - subject: RFC4514-представление субъекта (Subject);
      - sha256:  SHA-256 DER-кодированного сертификата (64 hex).

    Используется для расширенных метаданных и объяснителей: полная цепочка
    даёт больше информации, чем одиночный 8-hex signing_prefix (первый
    сертификат). В расчёт Жаккара напрямую не попадает.

    Ограниченный скоуп (bounded):
      - Используется cryptography.x509 + cryptography.hazmat.primitives.serialization.pkcs7.
        Поэтому парсятся только v1-подписи через META-INF/*.RSA|.DSA|.EC.
      - Для APK с чисто v2/v3-подписью без META-INF возвращается пустой список.
        Полноценный разбор APK Signing Block v2/v3 требует androguard и
        вынесен за рамки данной задачи.
      - На любой ошибке парсинга PKCS#7 возвращается пустой список.
    """
    path = Path(apk_path)
    if not path.exists() or not path.is_file():
        return []
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            cert_files = []
            for name in zf.namelist():
                if not name.startswith('META-INF/'):
                    continue
                if name.upper().endswith(SIGNATURE_EXTENSIONS):
                    cert_files.append(name)
            if not cert_files:
                return []
            cert_files.sort()
            cert_blob = zf.read(cert_files[0])
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        logger.warning('signing_view: не удалось прочитать META-INF из %s: %s', apk_path, exc)
        return []

    try:
        from cryptography.hazmat.primitives.serialization import pkcs7
        from cryptography.hazmat.primitives.serialization import Encoding
    except ImportError:
        logger.warning('signing_view: cryptography недоступен, цепочка сертификатов не извлечена')
        return []

    try:
        certs = pkcs7.load_der_pkcs7_certificates(cert_blob)
    except Exception as exc:
        logger.warning('signing_view: разбор PKCS#7 из %s не удался: %s', apk_path, exc)
        return []

    chain: list[dict] = []
    for cert in certs:
        try:
            issuer = cert.issuer.rfc4514_string()
        except Exception:
            issuer = ''
        try:
            subject = cert.subject.rfc4514_string()
        except Exception:
            subject = ''
        try:
            der = cert.public_bytes(Encoding.DER)
            sha256 = hashlib.sha256(der).hexdigest()
        except Exception:
            sha256 = ''
        chain.append({'issuer': issuer, 'subject': subject, 'sha256': sha256})
    return chain


# =============================================================================
# ARCH-30-V3-SIGNATURE-PARSE: разбор APK Signing Block v2/v3 + lineage.
# =============================================================================


def _read_eocd_central_directory_offset(data: bytes) -> Optional[int]:
    """Найти EOCD и вернуть offset Central Directory или None.

    EOCD сигнатура — ``PK\\x05\\x06``. EOCD имеет фиксированную часть 22 байта;
    далее может идти comment произвольной длины. Поиск сигнатуры идёт с конца
    файла назад. Возвращаемое значение — offset Central Directory из полей
    EOCD (поле по смещению +16 от начала EOCD).
    """
    if len(data) < 22:
        return None
    eocd_idx = data.rfind(b'PK\x05\x06')
    if eocd_idx < 0:
        return None
    if eocd_idx + 22 > len(data):
        return None
    cd_off = struct.unpack('<I', data[eocd_idx + 16: eocd_idx + 20])[0]
    return cd_off


def _locate_apk_signing_block(data: bytes) -> Optional[tuple[int, int]]:
    """Найти границы APK Signing Block.

    Возвращает кортеж ``(block_start, block_end)`` (полуинтервал ``[start, end)``,
    включающий вторую копию size_of_block и magic). Если блок не найден —
    None. Парсинг строго по спеке Android Source: блок расположен между
    Local File Headers и Central Directory; перед central directory находятся
    16 байт magic ``APK Sig Block 42``, а перед magic — uint64 ``size_of_block``.
    """
    cd_off = _read_eocd_central_directory_offset(data)
    if cd_off is None or cd_off < 24:
        return None
    # magic — последние 16 байт перед central directory
    magic_offset = cd_off - 16
    if magic_offset < 8 or data[magic_offset: magic_offset + 16] != b'APK Sig Block 42':
        return None
    # size_of_block — uint64 непосредственно перед magic
    size_field_off = magic_offset - 8
    size_of_block = struct.unpack('<Q', data[size_field_off: size_field_off + 8])[0]
    # block_start = cd_off - (size_of_block + 8). +8 — внешний uint64 первой копии size.
    block_start = cd_off - (size_of_block + 8)
    if block_start < 0 or block_start >= cd_off:
        return None
    return block_start, cd_off


def _iter_apk_sig_block_pairs(data: bytes, block_start: int, block_end: int):
    """Итератор по парам (id, value) APK Signing Block.

    Структура (см. Android source spec):
      - uint64 size_of_block (первая копия в начале)
      - sequence of pairs:
          - uint64 length
          - uint32 id
          - bytes value (length - 4)
      - uint64 size_of_block (вторая копия)
      - bytes[16] magic
    """
    # Первая uint64 size_of_block по offset block_start.
    offset = block_start + 8
    # Конец последовательности пар — за 24 байта до block_end (8 size + 16 magic).
    pairs_end = block_end - 24
    while offset + 12 <= pairs_end:
        pair_len = struct.unpack('<Q', data[offset: offset + 8])[0]
        if pair_len < 4 or offset + 8 + pair_len > pairs_end:
            return
        pair_id = struct.unpack('<I', data[offset + 8: offset + 12])[0]
        value = bytes(data[offset + 12: offset + 8 + pair_len])
        yield pair_id, value
        offset += 8 + pair_len


def _read_len_prefixed_u32(buf: bytes, offset: int) -> Optional[tuple[bytes, int]]:
    """Прочитать ``uint32 length || bytes`` из buf по offset.

    Возвращает (payload, new_offset) или None при выходе за пределы.
    """
    if offset + 4 > len(buf):
        return None
    length = struct.unpack('<I', buf[offset: offset + 4])[0]
    end = offset + 4 + length
    if end > len(buf):
        return None
    return buf[offset + 4: end], end


def _iter_len_prefixed_u32_sequence(buf: bytes):
    """Итератор по элементам последовательности ``length-prefixed (uint32)``.

    Каждый элемент: ``uint32 length || bytes``. Последовательность
    заканчивается, когда offset достиг конца буфера. Любая ошибка границ —
    тихий выход (genertor завершается).
    """
    offset = 0
    while offset < len(buf):
        item = _read_len_prefixed_u32(buf, offset)
        if item is None:
            return
        payload, offset = item
        yield payload


def _parse_proof_of_rotation_value(value: bytes) -> list[dict]:
    """Из значения атрибута proof-of-rotation вернуть список уровней с fingerprint.

    Формат уровня (упрощённо, мы извлекаем только сертификат):
      - length-prefixed signed_data
          - length-prefixed certificate (X.509 DER)
          - uint32 sigAlgo
      - uint32 flags
      - uint32 sigAlgo
      - length-prefixed signature

    Возвращает список dict вида ``{'fingerprint': sha256_hex}`` по порядку
    появления в lineage. При ошибке парсинга на каком-либо уровне функция
    возвращает то, что успела собрать.
    """
    levels: list[dict] = []
    for level_buf in _iter_len_prefixed_u32_sequence(value):
        # signed_data занимает первый length-prefixed блок уровня.
        signed = _read_len_prefixed_u32(level_buf, 0)
        if signed is None:
            continue
        signed_data, _ = signed
        cert = _read_len_prefixed_u32(signed_data, 0)
        if cert is None:
            continue
        cert_bytes, _ = cert
        if not cert_bytes:
            continue
        levels.append({'fingerprint': hashlib.sha256(cert_bytes).hexdigest()})
    return levels


def _parse_v3_signed_data(signed_data: bytes) -> tuple[list[dict], Optional[list[dict]]]:
    """Разобрать v3 signed_data, вернуть (signers_certs, rotation_lineage_or_None).

    Структура signed_data:
      - length-prefixed digests sequence
      - length-prefixed certificates sequence
          - каждый cert: length-prefixed X.509 DER
      - uint32 minSdk
      - uint32 maxSdk
      - length-prefixed additional attributes sequence
          - каждый attr: ``uint32 id || bytes value`` внутри своего length-prefix

    Возвращает список signer-сертификатов (по сути там обычно один cert)
    и lineage (или None, если proof-of-rotation атрибута нет).
    """
    cursor = 0
    # digests
    digests = _read_len_prefixed_u32(signed_data, cursor)
    if digests is None:
        return [], None
    _, cursor = digests
    # certificates
    certs_seq = _read_len_prefixed_u32(signed_data, cursor)
    if certs_seq is None:
        return [], None
    certs_buf, cursor = certs_seq
    cert_entries: list[dict] = []
    for cert_bytes in _iter_len_prefixed_u32_sequence(certs_buf):
        if not cert_bytes:
            continue
        cert_entries.append({'fingerprint': hashlib.sha256(cert_bytes).hexdigest()})
    # minSdk + maxSdk
    if cursor + 8 > len(signed_data):
        return cert_entries, None
    cursor += 8
    # additional attributes
    attrs_seq = _read_len_prefixed_u32(signed_data, cursor)
    if attrs_seq is None:
        return cert_entries, None
    attrs_buf, _ = attrs_seq
    rotation_lineage: Optional[list[dict]] = None
    for attr_buf in _iter_len_prefixed_u32_sequence(attrs_buf):
        if len(attr_buf) < 4:
            continue
        attr_id = struct.unpack('<I', attr_buf[:4])[0]
        if attr_id == PROOF_OF_ROTATION_ATTR_ID:
            try:
                lineage = _parse_proof_of_rotation_value(attr_buf[4:])
            except Exception as exc:
                logger.warning('signing_view: ошибка разбора rotation lineage: %s', exc)
                lineage = []
            if lineage:
                rotation_lineage = lineage
            break
    return cert_entries, rotation_lineage


def parse_signing_scheme_v3(apk_path: Path) -> dict:
    """Разобрать APK Signing Block v3 и вернуть структурированное представление.

    Возвращает словарь:
      {
        'found': bool,
        'signers': list[{'fingerprint': sha256_hex_str}],
        'rotation_lineage': list[{'fingerprint': sha256_hex_str}] | None,
      }

    Если v3-блок (id 0xF05368C0) не найден — возвращает
    ``{'found': False, 'signers': [], 'rotation_lineage': None}``. Парсер
    использует только стандартный python (``zipfile`` + ``struct``), без
    androguard и cryptography.
    """
    empty: dict = {'found': False, 'signers': [], 'rotation_lineage': None}
    path = Path(apk_path)
    if not path.exists() or not path.is_file():
        return empty
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning('signing_view: cannot read %s: %s', apk_path, exc)
        return empty

    located = _locate_apk_signing_block(data)
    if located is None:
        return empty
    block_start, block_end = located

    v3_value: Optional[bytes] = None
    for pair_id, value in _iter_apk_sig_block_pairs(data, block_start, block_end):
        if pair_id == APK_SIG_V3_BLOCK_ID:
            v3_value = value
            break
    if v3_value is None:
        return empty

    # value v3-блока: length-prefixed sequence of signers
    signers_outer = _read_len_prefixed_u32(v3_value, 0)
    if signers_outer is None:
        return empty
    signers_buf, _ = signers_outer

    all_signers: list[dict] = []
    rotation_lineage: Optional[list[dict]] = None
    for signer_buf in _iter_len_prefixed_u32_sequence(signers_buf):
        # signer = length-prefixed signed_data || minSdk || maxSdk ||
        #         length-prefixed signatures || length-prefixed public_key
        signed = _read_len_prefixed_u32(signer_buf, 0)
        if signed is None:
            continue
        signed_data, _ = signed
        certs, lineage = _parse_v3_signed_data(signed_data)
        all_signers.extend(certs)
        if lineage and rotation_lineage is None:
            rotation_lineage = lineage

    return {
        'found': True,
        'signers': all_signers,
        'rotation_lineage': rotation_lineage,
    }


def _has_apk_sig_block_pair_id(data: bytes, target_id: int) -> bool:
    """True если APK Signing Block содержит pair с заданным id."""
    located = _locate_apk_signing_block(data)
    if located is None:
        return False
    block_start, block_end = located
    for pair_id, _ in _iter_apk_sig_block_pairs(data, block_start, block_end):
        if pair_id == target_id:
            return True
    return False


def _has_meta_inf_v1_signature(apk_path: Path) -> bool:
    """True если в zip есть META-INF/*.RSA|.DSA|.EC."""
    try:
        with zipfile.ZipFile(apk_path, 'r') as zf:
            for name in zf.namelist():
                if not name.startswith('META-INF/'):
                    continue
                if name.upper().endswith(SIGNATURE_EXTENSIONS):
                    return True
    except (zipfile.BadZipFile, OSError, KeyError):
        return False
    return False


def _detect_signing_scheme(apk_path: Path) -> Optional[str]:
    """Определить схему подписи APK без conflate v2/v3.

    Возвращает одно из: ``'v1'``, ``'v2'``, ``'v3'``, ``'v3_with_rotation'``
    или None если подпись отсутствует. Приоритет (по убыванию выраженности):
    v3_with_rotation > v3 > v2 > v1.

    Это явное разделение исправляет баг ``screening_runner._detect_signing_scheme``,
    где APK с v3-подписью без META-INF помечался как ``'v2'`` (см. critic-ARCH-29).
    """
    path = Path(apk_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None

    # 1. Проверяем APK Signing Block: v3 (с rotation или без), v2.
    located = _locate_apk_signing_block(data)
    has_v3 = False
    has_v2 = False
    has_rotation = False
    if located is not None:
        block_start, block_end = located
        for pair_id, value in _iter_apk_sig_block_pairs(data, block_start, block_end):
            if pair_id == APK_SIG_V3_BLOCK_ID:
                has_v3 = True
                # Проверяем, есть ли proof-of-rotation в signed_data signer'ов.
                signers_outer = _read_len_prefixed_u32(value, 0)
                if signers_outer is not None:
                    signers_buf, _ = signers_outer
                    for signer_buf in _iter_len_prefixed_u32_sequence(signers_buf):
                        signed = _read_len_prefixed_u32(signer_buf, 0)
                        if signed is None:
                            continue
                        _, lineage = _parse_v3_signed_data(signed[0])
                        if lineage:
                            has_rotation = True
                            break
            elif pair_id == APK_SIG_V2_BLOCK_ID:
                has_v2 = True

    if has_v3 and has_rotation:
        return 'v3_with_rotation'
    if has_v3:
        return 'v3'
    if has_v2:
        return 'v2'
    if _has_meta_inf_v1_signature(path):
        return 'v1'
    return None


# =============================================================================
# Сравнение подписей.
# =============================================================================


SignatureInput = Union[Optional[str], dict]


def _extract_lineage_fingerprints(sig: dict) -> set[str]:
    """Из dict-описания подписи собрать множество fingerprint'ов lineage + signer."""
    fps: set[str] = set()
    fp = sig.get('fingerprint')
    if isinstance(fp, str) and fp:
        fps.add(fp)
    lineage = sig.get('rotation_lineage')
    if isinstance(lineage, list):
        for entry in lineage:
            if isinstance(entry, dict):
                lf = entry.get('fingerprint')
                if isinstance(lf, str) and lf:
                    fps.add(lf)
    return fps


def compare_signatures(sig_a: SignatureInput, sig_b: SignatureInput) -> dict:
    """Сравнить две подписи (string-fingerprint или dict с lineage).

    Поддержка двух форматов входа для обратной совместимости:

    1. ``Optional[str]`` — старый контракт по SHA-256 hex, возможные статусы:
       'match' / 'mismatch' / 'missing' / 'both_missing'.

    2. ``dict`` вида ``{'fingerprint': str, 'rotation_lineage': list|None}``.
       Если хотя бы у одной стороны есть lineage и пересечение fingerprint'ов
       обоих APK (включая lineage) непусто — статус ``'match_via_rotation'``,
       score = 1.0. Это закрывает класс «один разработчик, два официальных
       билда после rotation», где старая семантика давала ложный 'mismatch'.

    Канон: при пересечении lineage возвращаем match_via_rotation даже если
    непосредственные signer fingerprint'ы различаются — именно так Google Play
    app-signing трактует валидную ротацию ключей.

    DEEP-20-BOTH-EMPTY-AUDIT: при обоих None сохраняется 'both_missing' +
    ``both_empty=True``.
    """
    # Normalize: None -> None, str -> str, dict -> dict.
    a_is_dict = isinstance(sig_a, dict)
    b_is_dict = isinstance(sig_b, dict)

    if not a_is_dict and not b_is_dict:
        # старый контракт по строкам
        return _compare_string_signatures(sig_a, sig_b)

    # хотя бы одна сторона — dict; собираем fingerprint'ы и lineage.
    fps_a = _normalize_signature_to_fps(sig_a)
    fps_b = _normalize_signature_to_fps(sig_b)
    lineage_a = _normalize_signature_lineage(sig_a)
    lineage_b = _normalize_signature_lineage(sig_b)

    if not fps_a and not fps_b:
        return {'score': 0.0, 'status': 'both_missing', 'both_empty': True}
    if not fps_a or not fps_b:
        return {'score': 0.0, 'status': 'missing'}

    # Прямое совпадение по основному fingerprint'у обеих сторон.
    primary_a = _primary_fingerprint(sig_a)
    primary_b = _primary_fingerprint(sig_b)
    if primary_a is not None and primary_b is not None and primary_a == primary_b:
        return {'score': 1.0, 'status': 'match'}

    # Совпадение через lineage. Достаточно одного общего fingerprint'а
    # в lineage хотя бы одной из сторон.
    if (lineage_a or lineage_b) and (fps_a & fps_b):
        return {'score': 1.0, 'status': 'match_via_rotation'}

    return {'score': 0.0, 'status': 'mismatch'}


def _compare_string_signatures(hash_a: Optional[str], hash_b: Optional[str]) -> dict:
    """Старый контракт по строковым SHA-256 hex."""
    if hash_a is None and hash_b is None:
        return {'score': 0.0, 'status': 'both_missing', 'both_empty': True}
    if hash_a is None or hash_b is None:
        return {'score': 0.0, 'status': 'missing'}
    if hash_a == hash_b:
        return {'score': 1.0, 'status': 'match'}
    return {'score': 0.0, 'status': 'mismatch'}


def _normalize_signature_to_fps(sig: SignatureInput) -> set[str]:
    if sig is None:
        return set()
    if isinstance(sig, str):
        return {sig}
    if isinstance(sig, dict):
        return _extract_lineage_fingerprints(sig)
    return set()


def _normalize_signature_lineage(sig: SignatureInput) -> list[dict]:
    if isinstance(sig, dict):
        lineage = sig.get('rotation_lineage')
        if isinstance(lineage, list):
            return lineage
    return []


def _primary_fingerprint(sig: SignatureInput) -> Optional[str]:
    if sig is None:
        return None
    if isinstance(sig, str):
        return sig
    if isinstance(sig, dict):
        fp = sig.get('fingerprint')
        if isinstance(fp, str) and fp:
            return fp
    return None


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print('Usage: signing_view.py <apk_path>')
        sys.exit(1)
    h = extract_apk_signature_hash(Path(sys.argv[1]))
    print(h if h else '<нет подписи>')

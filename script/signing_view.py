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
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SIGNATURE_EXTENSIONS = ('.RSA', '.DSA', '.EC')


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


def compare_signatures(hash_a: Optional[str], hash_b: Optional[str]) -> dict:
    """Сравнить два хеша сертификата подписи.

    Выход: {'score': float (0.0 или 1.0), 'status': str}.
      'match' — оба хеша существуют и совпадают, score = 1.0.
      'mismatch' — оба хеша существуют, но различаются, score = 0.0.
      'both_missing' — оба хеша None, score = 0.0, both_empty=True
        (DEEP-20-BOTH-EMPTY-AUDIT: единая семантика «обе стороны
        без сигнала» через канонический флаг ``both_empty``).
      'missing' — ровно одна сторона без подписи, score = 0.0.
    """
    if hash_a is None and hash_b is None:
        return {'score': 0.0, 'status': 'both_missing', 'both_empty': True}
    if hash_a is None or hash_b is None:
        return {'score': 0.0, 'status': 'missing'}
    if hash_a == hash_b:
        return {'score': 1.0, 'status': 'match'}
    return {'score': 0.0, 'status': 'mismatch'}


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print('Usage: signing_view.py <apk_path>')
        sys.exit(1)
    h = extract_apk_signature_hash(Path(sys.argv[1]))
    print(h if h else '<нет подписи>')

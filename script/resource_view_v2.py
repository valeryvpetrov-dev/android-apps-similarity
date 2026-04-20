#!/usr/bin/env python3
"""EXEC-R_resource_v2 — разделение ресурсного слоя на подвиды.

Расширение существующего ``resource_view`` (`BOR-002`). Вместо одного общего
множества ``(rel_path, sha256)`` выделяет пять устойчивых подмножеств и
сигнал перцептивного хеша иконки приложения:

1. ``res_strings``  — имена строк из ``res/values/strings.xml``
   (и всех ``res/values-*/strings.xml``). Токен ``string:<name>``.
2. ``res_drawables`` — имена файлов в ``res/drawable*`` и ``res/mipmap*``
   без расширения. Токен ``drawable:<name>``.
3. ``res_layouts`` — имена файлов в ``res/layout*``. Токен ``layout:<name>``.
4. ``assets_bin`` — пути в ``assets/`` с байтовым бакетом размера.
   Токен ``asset:<path>:<size_bucket>``.
5. ``icon_phash`` — упрощённый перцептивный хеш иконки приложения.

Реализация ``icon_phash``
-------------------------

Честное упрощение: настоящий dHash (уменьшение до 9x8, разности по
горизонтали, 64 бита) требует PNG-декодера. Pillow/numpy вводить нельзя,
а ручной декодер IDAT со zlib+filter избыточен для первого шага.

Поэтому на данном этапе ``icon_phash`` — это ``blake2b(raw_icon_bytes)``,
усечённый до 64 бит (16 hex символов). Это НЕ перцептивный хеш: он
чувствителен к любому байтовому изменению PNG/WEBP (включая метаданные).
Для идентичных файлов иконки он устойчив. Следующий шаг —
``EXEC-R_resource_v2-DHASH``: подключить pure-Python PNG декодер и
реализовать реальный dHash. Пока используется hamming-like метрика по
текущему хешу, но между разными иконками она даёт ~0.5 (как случайный
шум), а не настоящее перцептивное расстояние.

В ``m_static_views`` модуль пока НЕ интегрируется — это отдельная задача
``EXEC-R_resource_v2-INTEGRATION``.
"""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path
from typing import Dict, Optional, Set


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MODE = "v2"

RE_STRING_NAME = re.compile(r'<string\s+[^>]*name="([^"]+)"')

ASSET_SIZE_BUCKETS = (
    (1024, "0_1KB"),
    (10 * 1024, "1_10KB"),
    (100 * 1024, "10_100KB"),
    (1024 * 1024, "100KB_1MB"),
)
ASSET_SIZE_BUCKET_MAX = "1MB+"

# Порядок поиска иконки: сначала более качественные DPI, потом запасные.
ICON_CANDIDATES = (
    "res/mipmap-xxxhdpi/ic_launcher.png",
    "res/mipmap-xxhdpi/ic_launcher.png",
    "res/mipmap-xhdpi/ic_launcher.png",
    "res/mipmap-hdpi/ic_launcher.png",
    "res/mipmap-mdpi/ic_launcher.png",
    "res/mipmap-xxxhdpi/ic_launcher.webp",
    "res/mipmap-xxhdpi/ic_launcher.webp",
    "res/mipmap-xhdpi/ic_launcher.webp",
    "res/mipmap-hdpi/ic_launcher.webp",
    "res/mipmap-mdpi/ic_launcher.webp",
    "res/drawable-xxxhdpi/ic_launcher.png",
    "res/drawable-xxhdpi/ic_launcher.png",
    "res/drawable-xhdpi/ic_launcher.png",
    "res/drawable-hdpi/ic_launcher.png",
    "res/drawable-mdpi/ic_launcher.png",
    "res/drawable/ic_launcher.png",
    "res/mipmap/ic_launcher.png",
)

ICON_HASH_BITS = 64
ICON_HASH_HEX_LEN = ICON_HASH_BITS // 4  # 16
ICON_TOKEN_PREFIX = "icon_phash"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _ensure_input_dir(path_str: str) -> Path:
    apk_path = Path(path_str).expanduser().resolve()
    if not apk_path.exists():
        raise FileNotFoundError(
            "APK directory does not exist: {}".format(apk_path)
        )
    if not apk_path.is_dir():
        raise NotADirectoryError(
            "APK path is not a directory: {}".format(apk_path)
        )
    return apk_path


def _size_bucket(size_bytes: int) -> str:
    for threshold, label in ASSET_SIZE_BUCKETS:
        if size_bytes < threshold:
            return label
    return ASSET_SIZE_BUCKET_MAX


def _iter_subdirs(apk_path: Path, prefix: str) -> list:
    """Возвращает все директории верхнего уровня в ``res/``, чьи имена
    начинаются с ``prefix`` (``layout``/``drawable``/``mipmap`` и их
    локализованные/DPI-варианты).
    """
    res_dir = apk_path / "res"
    if not res_dir.is_dir():
        return []
    matches = []
    for child in sorted(res_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name == prefix or name.startswith(prefix + "-"):
            matches.append(child)
    return matches


def _values_dirs(apk_path: Path) -> list:
    """``res/values`` и все ``res/values-<qualifier>/`` с локализациями."""
    return _iter_subdirs(apk_path, "values")


# ---------------------------------------------------------------------------
# Экстракция подмножеств
# ---------------------------------------------------------------------------

def _extract_strings(apk_path: Path) -> Set[str]:
    tokens: Set[str] = set()
    for values_dir in _values_dirs(apk_path):
        xml_path = values_dir / "strings.xml"
        if not xml_path.is_file():
            continue
        try:
            text = xml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in RE_STRING_NAME.finditer(text):
            name = match.group(1).strip()
            if name:
                tokens.add("string:{}".format(name))
    return tokens


def _extract_drawables(apk_path: Path) -> Set[str]:
    tokens: Set[str] = set()
    dirs = _iter_subdirs(apk_path, "drawable") + _iter_subdirs(apk_path, "mipmap")
    for d in dirs:
        for child in sorted(d.iterdir()):
            if not child.is_file():
                continue
            stem = child.stem
            if stem:
                tokens.add("drawable:{}".format(stem))
    return tokens


def _extract_layouts(apk_path: Path) -> Set[str]:
    tokens: Set[str] = set()
    for d in _iter_subdirs(apk_path, "layout"):
        for child in sorted(d.iterdir()):
            if not child.is_file():
                continue
            # Только XML-файлы как layout.
            if child.suffix.lower() != ".xml":
                continue
            stem = child.stem
            if stem:
                tokens.add("layout:{}".format(stem))
    return tokens


def _extract_assets(apk_path: Path) -> Set[str]:
    tokens: Set[str] = set()
    assets_dir = apk_path / "assets"
    if not assets_dir.is_dir():
        return tokens
    for file_path in sorted(assets_dir.rglob("*")):
        if not file_path.is_file():
            continue
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            continue
        rel = file_path.relative_to(apk_path).as_posix()
        bucket = _size_bucket(size_bytes)
        tokens.add("asset:{}:{}".format(rel, bucket))
    return tokens


# ---------------------------------------------------------------------------
# Иконка: поиск + «перцептивный» хеш (упрощённый)
# ---------------------------------------------------------------------------

def _find_icon_file(apk_path: Path) -> Optional[Path]:
    """Ищет файл иконки по списку типичных путей. Возвращает ``None``, если
    ничего не найдено."""
    for rel in ICON_CANDIDATES:
        candidate = apk_path / rel
        if candidate.is_file():
            return candidate
    return None


def _icon_phash_bytes(raw: bytes) -> str:
    """Упрощённый 64-битный хеш от байтов иконки.

    Это не настоящий dHash — см. module docstring. Настоящий dHash будет
    реализован в ``EXEC-R_resource_v2-DHASH``. Сейчас: ``blake2b(digest_size=8)``
    от сырого контента файла (PNG/WEBP). Идентичные иконки дают одинаковый
    хеш; малейшее изменение байтов меняет весь хеш (чего не делает настоящий
    перцептивный хеш, но для первого шага достаточно).
    """
    digest = hashlib.blake2b(raw, digest_size=ICON_HASH_BITS // 8).hexdigest()
    return digest


def _extract_icon_phash(apk_path: Path) -> Optional[str]:
    icon_path = _find_icon_file(apk_path)
    if icon_path is None:
        return None
    try:
        raw = icon_path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    hex_hash = _icon_phash_bytes(raw)
    return "{}:{}".format(ICON_TOKEN_PREFIX, hex_hash)


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def extract_resource_view_v2(unpacked_dir: str) -> Dict:
    """Извлекает пять подмножеств ресурсов и (опционально) хеш иконки.

    Возвращает dict:
        ``res_strings``   — ``set[str]`` токенов вида ``string:<name>``.
        ``res_drawables`` — ``set[str]`` токенов вида ``drawable:<stem>``.
        ``res_layouts``   — ``set[str]`` токенов вида ``layout:<stem>``.
        ``assets_bin``    — ``set[str]`` токенов ``asset:<path>:<bucket>``.
        ``icon_phash``    — ``str | None``, токен ``icon_phash:<16 hex>``.
        ``mode``          — всегда строка ``"v2"``.
    """
    apk_path = _ensure_input_dir(unpacked_dir)
    return {
        "res_strings": _extract_strings(apk_path),
        "res_drawables": _extract_drawables(apk_path),
        "res_layouts": _extract_layouts(apk_path),
        "assets_bin": _extract_assets(apk_path),
        "icon_phash": _extract_icon_phash(apk_path),
        "mode": MODE,
    }


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------

_JACCARD_SUBSETS = (
    ("res_strings", "res_strings_score"),
    ("res_drawables", "res_drawables_score"),
    ("res_layouts", "res_layouts_score"),
    ("assets_bin", "assets_bin_score"),
)


def _jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    if not set_a and not set_b:
        return 0.0  # считается пустым подмножеством для combined_score
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _parse_icon_hex(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    prefix = "{}:".format(ICON_TOKEN_PREFIX)
    if not token.startswith(prefix):
        return None
    hex_part = token[len(prefix):]
    if len(hex_part) != ICON_HASH_HEX_LEN:
        return None
    try:
        return int(hex_part, 16)
    except ValueError:
        return None


def _icon_similarity(token_a: Optional[str], token_b: Optional[str]) -> Optional[float]:
    val_a = _parse_icon_hex(token_a)
    val_b = _parse_icon_hex(token_b)
    if val_a is None or val_b is None:
        return None
    xor = val_a ^ val_b
    hamming = bin(xor).count("1")
    return 1.0 - (hamming / ICON_HASH_BITS)


def compare_resource_view_v2(features_a: Dict, features_b: Dict) -> Dict:
    """Поджаккард по каждому подмножеству + Хеммингово расстояние иконки.

    Возвращает dict с ключами:
        ``res_strings_score``, ``res_drawables_score``, ``res_layouts_score``,
        ``assets_bin_score``       — Jaccard в ``[0.0, 1.0]``.
        ``icon_phash_similarity``  — ``1.0 - (hamming / 64)`` либо ``0.0``,
            если иконки нет с одной или обеих сторон.
        ``combined_score``         — среднее арифметическое непустых
            подсигналов (учитывается и ``icon_phash_similarity``, если обе
            стороны имеют токен).
        ``status``                 — ``"ok"`` если есть хотя бы одно
            непустое подмножество с обеих сторон; ``"partial"`` если часть
            подсигналов отсутствует, но хотя бы один есть; ``"empty"``
            если абсолютно все подмножества пустые с обеих сторон и
            иконки отсутствуют.
    """
    result: Dict[str, float] = {}
    contributing_scores = []
    present_subsets = 0
    empty_subsets = 0

    for key, score_key in _JACCARD_SUBSETS:
        set_a: Set[str] = set(features_a.get(key, set()) or set())
        set_b: Set[str] = set(features_b.get(key, set()) or set())
        if not set_a and not set_b:
            result[score_key] = 0.0
            empty_subsets += 1
            continue
        score = _jaccard(set_a, set_b)
        result[score_key] = score
        contributing_scores.append(score)
        present_subsets += 1

    icon_sim = _icon_similarity(
        features_a.get("icon_phash"), features_b.get("icon_phash")
    )
    if icon_sim is None:
        result["icon_phash_similarity"] = 0.0
        icon_present = False
    else:
        result["icon_phash_similarity"] = icon_sim
        contributing_scores.append(icon_sim)
        icon_present = True

    if contributing_scores:
        result["combined_score"] = sum(contributing_scores) / len(contributing_scores)
    else:
        result["combined_score"] = 0.0

    total_signals = len(_JACCARD_SUBSETS) + 1  # +1 за иконку
    if not contributing_scores:
        status = "empty"
    elif (present_subsets + (1 if icon_present else 0)) == total_signals:
        status = "ok"
    else:
        status = "partial"
    result["status"] = status
    return result


__all__ = [
    "MODE",
    "extract_resource_view_v2",
    "compare_resource_view_v2",
]

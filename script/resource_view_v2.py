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
5. ``icon_phash`` — перцептивный хеш иконки приложения (wHash или dHash 8x8).

Реализация ``icon_phash`` (REPR-16-WHASH-HAAR)
-----------------------------------------------

Метод перцептивного хеша выбирается через module-level константу
``ICON_HASH_METHOD`` или переменную окружения
``ANDROID_SIM_IMAGE_HASH_METHOD``. Поддерживаются два метода:

* ``"whash"`` — wavelet hash на Haar wavelet 8x8 (по умолчанию). Устойчив
  к шуму обфускации ресурсов значительнее, чем dHash (см. NKR-13-*).
  Реализация через внешнюю зависимость
  ``imagehash.whash(img, hash_size=8, mode='haar')``.
* ``"dhash"`` — difference hash 8x8 (совместимость). Реализован на чистом
  Pillow и используется как fallback, когда библиотека ``imagehash``
  недоступна или метод явно запрошен через env var.

Формат токена:

* ``icon_phash:whash:<16 hex>`` при активном wHash;
* ``icon_phash:dhash:<16 hex>`` при активном dHash;
* legacy-формат ``icon_phash:<16 hex>`` понимается как dHash (обратная
  совместимость со snapshot-ами до REPR-16-WHASH-HAAR).

Префикс метода в токене обязателен: на смешанных корпусах (pre- и
post-migration) сравнение по разным методам даёт нули, поэтому
``_icon_similarity`` сопоставляет только токены с одинаковым методом.

Алгоритм dHash (fallback):

1. Декодировать иконку (PNG/WEBP) через Pillow.
2. Привести к серому (``convert('L')``) и ресайзить до 9x8 пикселей.
3. Для каждой строки из 8 пикселей сравнить каждую пару соседей
   слева-направо: если левый ярче — бит 0, иначе бит 1. Всего 8*8 = 64 бита.
4. Представить 64 бита как hex-строку длины 16.

Сравнение двух хешей делается через Хеммингово расстояние по hex-строкам
и нормализуется как ``1 - hamming / 64``.

Зависимости:

* Pillow — обязателен для чтения иконок в обоих методах.
* imagehash — требуется только для ``whash`` (иначе ``whash`` откатывается
  на dHash).

Если Pillow не установлен — ``icon_phash`` возвращает ``None`` честным
путём, без маскировки ошибки через blake2b сырых байт (такой подход был
назван фундаментальной ошибкой в REV-1 / research/R-06 и заменён этой
реализацией в ``EXEC-R_resource_v2-DHASH``).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

try:
    from library_view_v2 import (
        _compute_idf_weighted_tversky,
        _load_idf_weights_for_layer,
        compute_idf_weighted_jaccard,
    )
except ImportError:  # pragma: no cover - package import fallback
    from script.library_view_v2 import (
        _compute_idf_weighted_tversky,
        _load_idf_weights_for_layer,
        compute_idf_weighted_jaccard,
    )

try:
    from PIL import Image  # type: ignore
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    Image = None  # type: ignore
    _PIL_AVAILABLE = False

try:
    import imagehash as _imagehash  # type: ignore
    _IMAGEHASH_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    _imagehash = None  # type: ignore
    _IMAGEHASH_AVAILABLE = False


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

# Параметры dHash: ресайз к ширине+1 x высоте для 64 бит сравнений.
_DHASH_WIDTH = 9
_DHASH_HEIGHT = 8

# REPR-16-WHASH-HAAR: выбор перцептивного хеш-метода.
#
# Приоритет источников:
# 1. Явный аргумент функции (через ``method=`` в `_extract_icon_phash`).
# 2. Переменная окружения ``ANDROID_SIM_IMAGE_HASH_METHOD``.
# 3. Значение по умолчанию ``ICON_HASH_METHOD_DEFAULT``.
#
# Допустимые значения — ``"whash"`` и ``"dhash"``. Любое другое значение
# откатывается на default с предупреждением в логах.
ICON_HASH_METHOD_DEFAULT = "whash"
ICON_HASH_METHOD_FALLBACK = "dhash"
_SUPPORTED_ICON_HASH_METHODS = ("whash", "dhash")
_ENV_ICON_HASH_METHOD = "ANDROID_SIM_IMAGE_HASH_METHOD"

# Параметры wHash: квадрат 8x8, Haar wavelet.
_WHASH_SIZE = 8
_WHASH_MODE = "haar"


def _resolve_icon_hash_method(method: Optional[str] = None) -> str:
    """Возвращает активный метод хеша с учётом env var и fallback.

    Если явный аргумент не задан — читаем переменную окружения
    ``ANDROID_SIM_IMAGE_HASH_METHOD``. Для неизвестного значения
    логируем предупреждение и возвращаем default.
    """
    raw = method if method is not None else os.environ.get(_ENV_ICON_HASH_METHOD)
    if raw is None:
        return ICON_HASH_METHOD_DEFAULT
    normalized = str(raw).strip().lower()
    if normalized not in _SUPPORTED_ICON_HASH_METHODS:
        logger.warning(
            "resource_view_v2: неизвестный метод хеша %r, откатываюсь на %s",
            raw,
            ICON_HASH_METHOD_DEFAULT,
        )
        return ICON_HASH_METHOD_DEFAULT
    return normalized


# Module-level константа — вычисляется один раз при импорте, чтобы и тесты,
# и внешние модули (m_static_views) могли её прочитать для версионирования
# кэша фич.
ICON_HASH_METHOD = _resolve_icon_hash_method()


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
# Иконка: поиск + перцептивный хеш (dHash 8x8)
# ---------------------------------------------------------------------------

def _find_icon_file(apk_path: Path) -> Optional[Path]:
    """Ищет файл иконки по списку типичных путей. Возвращает ``None``, если
    ничего не найдено."""
    for rel in ICON_CANDIDATES:
        candidate = apk_path / rel
        if candidate.is_file():
            return candidate
    return None


def _compute_dhash(icon_path: Path) -> Optional[str]:
    """Считает dHash 8x8 от файла иконки.

    Возвращает hex-строку длины 16 (64 бита) или ``None``, если Pillow
    не установлен или файл не удаётся декодировать. Честный провал:
    маскировка через blake2b сырых байтов НЕ производится.
    """
    if not _PIL_AVAILABLE:
        return None
    try:
        with Image.open(icon_path) as img:
            # Приводим к серому и ресайзим до (W+1) x H. Bilinear —
            # стандартный выбор для dHash: сохраняет яркость, дёшев по CPU.
            gray = img.convert("L").resize(
                (_DHASH_WIDTH, _DHASH_HEIGHT),
                Image.Resampling.BILINEAR,
            )
            pixels = list(gray.getdata())
    except Exception:  # noqa: BLE001 - любые сбои декодера = честный None
        return None

    if len(pixels) != _DHASH_WIDTH * _DHASH_HEIGHT:
        return None

    # Сравниваем соседние пиксели в каждой строке. 8 строк * 8 сравнений = 64 бита.
    bits = 0
    bit_index = 0
    for row in range(_DHASH_HEIGHT):
        row_start = row * _DHASH_WIDTH
        for col in range(_DHASH_WIDTH - 1):
            left = pixels[row_start + col]
            right = pixels[row_start + col + 1]
            if left < right:
                bits |= 1 << (ICON_HASH_BITS - 1 - bit_index)
            bit_index += 1

    return "{:0{}x}".format(bits, ICON_HASH_HEX_LEN)


def _compute_whash(icon_path: Path) -> Optional[str]:
    """Считает wHash (Haar wavelet hash) 8x8 от файла иконки.

    REPR-16-WHASH-HAAR: wavelet hash устойчивее к шуму обфускации ресурсов,
    чем dHash (см. NKR-13-*). Делегирует вычисление в
    ``imagehash.whash(img, hash_size=8, mode='haar')``.

    Возвращает hex-строку длины 16 (64 бита) или ``None`` при отсутствии
    Pillow / imagehash, а также при сбое декодера. В случае отсутствия
    ``imagehash`` вызывающий код должен сам принять решение о fallback
    на dHash (см. ``_extract_icon_phash``).
    """
    if not _PIL_AVAILABLE or not _IMAGEHASH_AVAILABLE:
        return None
    try:
        with Image.open(icon_path) as img:
            # imagehash.whash сам приводит к серому через convert('L'),
            # но делаем это явно — чтобы поведение не зависело от версии
            # библиотеки и для ровного параллеля с dHash.
            gray = img.convert("L")
            hash_obj = _imagehash.whash(
                gray,
                hash_size=_WHASH_SIZE,
                mode=_WHASH_MODE,
            )
    except Exception:  # noqa: BLE001 - любые сбои декодера = честный None
        return None

    # imagehash.ImageHash.__str__ уже возвращает hex; на всякий случай
    # нормализуем длину и регистр.
    hex_hash = str(hash_obj).lower()
    if len(hex_hash) != ICON_HASH_HEX_LEN:
        # При hash_size=8 должно выйти ровно 64 бита = 16 hex. Если нет —
        # что-то пошло не так в зависимости, возвращаем честный None.
        logger.warning(
            "resource_view_v2: wHash вернул неожиданную длину %d (ожидалось %d)",
            len(hex_hash),
            ICON_HASH_HEX_LEN,
        )
        return None
    return hex_hash


def _extract_icon_phash(
    apk_path: Path,
    method: Optional[str] = None,
) -> Optional[str]:
    """Извлекает токен иконки с префиксом активного метода хеша.

    Формат возвращаемого токена — ``icon_phash:<method>:<hex>``. Если
    выбран ``whash``, но ``imagehash`` недоступен, делается честный откат
    на ``dhash`` с предупреждением в логах — иначе после миграции
    окружений без ``imagehash`` получили бы пустой сигнал на всём корпусе.
    """
    icon_path = _find_icon_file(apk_path)
    if icon_path is None:
        return None

    active_method = _resolve_icon_hash_method(method)

    if active_method == "whash":
        hex_hash = _compute_whash(icon_path)
        if hex_hash is None and not _IMAGEHASH_AVAILABLE:
            # Fallback на dHash, только если Pillow всё же доступен:
            # без Pillow честный None.
            if _PIL_AVAILABLE:
                logger.warning(
                    "resource_view_v2: imagehash недоступен, откатываюсь на dHash "
                    "для иконки %s",
                    icon_path,
                )
                hex_hash = _compute_dhash(icon_path)
                active_method = ICON_HASH_METHOD_FALLBACK
    else:
        hex_hash = _compute_dhash(icon_path)
        active_method = "dhash"

    if hex_hash is None:
        return None
    return "{}:{}:{}".format(ICON_TOKEN_PREFIX, active_method, hex_hash)


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
            ``None`` если иконка не найдена ИЛИ Pillow не установлен ИЛИ
            декодер дал сбой.
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


def _resource_tokens(features: Dict) -> Set[str]:
    tokens: Set[str] = set()
    for key, _score_key in _JACCARD_SUBSETS:
        tokens.update(set(features.get(key, set()) or set()))
    return tokens


def _parse_icon_token(token: Optional[str]) -> Optional[tuple]:
    """Разбирает токен иконки в пару ``(method, int_hash)``.

    Поддерживает три формы:

    * ``icon_phash:whash:<16 hex>`` — post-REPR-16 с явным методом;
    * ``icon_phash:dhash:<16 hex>`` — post-REPR-16 явный dHash;
    * ``icon_phash:<16 hex>`` — legacy-формат до REPR-16, трактуется как
      ``dhash`` (обратная совместимость со старыми snapshot-ами).
    """
    if not token:
        return None
    prefix = "{}:".format(ICON_TOKEN_PREFIX)
    if not token.startswith(prefix):
        return None
    tail = token[len(prefix):]
    if not tail:
        return None
    # Новый формат: <method>:<hex>. Старый: <hex>.
    if ":" in tail:
        method, _, hex_part = tail.partition(":")
        method = method.strip().lower()
        if method not in _SUPPORTED_ICON_HASH_METHODS:
            return None
    else:
        method = ICON_HASH_METHOD_FALLBACK  # legacy = dHash
        hex_part = tail
    if len(hex_part) != ICON_HASH_HEX_LEN:
        return None
    try:
        return method, int(hex_part, 16)
    except ValueError:
        return None


# Обратная совместимость: старое имя возвращает только int, используется
# в тестах resource_view_v2. Интерпретирует токен в прежнем смысле (без
# учёта метода) — для проверки корректности hex-части.
def _parse_icon_hex(token: Optional[str]) -> Optional[int]:
    parsed = _parse_icon_token(token)
    if parsed is None:
        return None
    return parsed[1]


def _icon_similarity(token_a: Optional[str], token_b: Optional[str]) -> Optional[float]:
    """Хеммингово сходство между двумя токенами.

    Корректно сравнивает только токены с одинаковым методом: wHash и dHash
    дают существенно разные битовые представления одной и той же иконки,
    поэтому Jaccard/Hamming между ними осмысленной оценки сходства не
    даёт. При несовпадении методов возвращается ``None`` — вызывающий
    код обязан обработать это как отсутствие сигнала (см.
    ``compare_resource_view_v2``).
    """
    parsed_a = _parse_icon_token(token_a)
    parsed_b = _parse_icon_token(token_b)
    if parsed_a is None or parsed_b is None:
        return None
    method_a, val_a = parsed_a
    method_b, val_b = parsed_b
    if method_a != method_b:
        # Смешанный корпус pre- и post-migration: не считаем сходство.
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
    tokens_a = _resource_tokens(features_a)
    tokens_b = _resource_tokens(features_b)
    result["jaccard"] = _jaccard(tokens_a, tokens_b)

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

    idf_weights = _load_idf_weights_for_layer("resource")
    if idf_weights and (tokens_a or tokens_b):
        tversky_a_idf, tversky_b_idf = _compute_idf_weighted_tversky(
            tokens_a,
            tokens_b,
            idf_weights,
        )
        result["jaccard_idf"] = compute_idf_weighted_jaccard(
            tokens_a,
            tokens_b,
            idf_weights,
        )
        result["tversky_a_idf"] = float(tversky_a_idf)
        result["tversky_b_idf"] = float(tversky_b_idf)

    total_signals = len(_JACCARD_SUBSETS) + 1  # +1 за иконку
    if not contributing_scores:
        # DEEP-20-BOTH-EMPTY-AUDIT: канонический статус на «обе стороны
        # без ресурсов и без иконки» — ``both_empty`` (вместо прежнего
        # ``empty``), плюс явный флаг ``both_empty=True``. Это единая
        # семантика со всеми остальными слоями static view; downstream
        # (``_include_layer_in_weighted_score``) теперь может исключить
        # resource_v2 из взвешенного среднего ровно по этому признаку.
        status = "both_empty"
        result["both_empty"] = True
    elif (present_subsets + (1 if icon_present else 0)) == total_signals:
        status = "ok"
    else:
        status = "partial"
    result["status"] = status
    return result


__all__ = [
    "MODE",
    "ICON_HASH_METHOD",
    "ICON_HASH_METHOD_DEFAULT",
    "ICON_HASH_METHOD_FALLBACK",
    "extract_resource_view_v2",
    "compare_resource_view_v2",
]

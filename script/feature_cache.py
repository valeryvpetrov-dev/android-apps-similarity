#!/usr/bin/env python3
"""EXEC-REPR-FEATURE-CACHE: устойчивый кэш признаков по SHA-256 APK.

Цель: избавиться от повторного извлечения слоёв признаков для одного
и того же APK. Один раз посчитали — сохранили JSON в кэш-директорию
рядом с именем-ключом ``sha256 + feature_version``; на повторный вызов
возвращаем из кэша.

Контракт устойчивости:

* повреждённый JSON -> предупреждение + пересчёт + перезапись;
* недоступная кэш-директория -> предупреждение + работа без кэша
  (фолбэк на прямой ``extract_fn``).

Кэш хранит только JSON-безопасные типы. ``set`` сериализуется в
отсортированный список с префиксом ``__set__`` в ключе, чтобы потом
восстановить множество (слои ``code``, ``component``, ... — это
``set`` строк).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)


# Размер блока чтения файла при расчёте SHA-256. 1 МиБ даёт хорошую
# пропускную способность и не съедает память для больших APK.
_READ_BLOCK_SIZE = 1024 * 1024


def sha256_of_file(apk_path: str | os.PathLike[str]) -> str:
    """Посчитать SHA-256 содержимого файла.

    Parameters
    ----------
    apk_path:
        Путь до файла APK.

    Returns
    -------
    str
        Шестнадцатеричная строка длиной 64 символа.

    Raises
    ------
    FileNotFoundError
        Если файл по пути не существует.
    """
    resolved = Path(apk_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError("APK не найден: {}".format(resolved))
    hasher = hashlib.sha256()
    with resolved.open("rb") as fh:
        while True:
            block = fh.read(_READ_BLOCK_SIZE)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def cache_key(apk_path: str | os.PathLike[str], feature_version: str) -> str:
    """Собрать ключ кэша из SHA-256 APK и версии фич.

    Ключ содержит версию фич, чтобы смена схемы извлечения
    автоматически инвалидировала старые записи без их ручной очистки.

    Returns
    -------
    str
        Строка формата ``<sha256>__<feature_version>``.
    """
    digest = sha256_of_file(apk_path)
    return "{}__{}".format(digest, feature_version)


def _encode_sets(payload: Any) -> Any:
    """Рекурсивно превратить ``set`` в маркированный словарь.

    Маркер: ``{"__set__": sorted(list)}``. Остальные типы (dict, list,
    tuple, скаляры) остаются как есть.
    """
    if isinstance(payload, set):
        return {"__set__": sorted(str(item) for item in payload)}
    if isinstance(payload, dict):
        return {key: _encode_sets(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_encode_sets(item) for item in payload]
    if isinstance(payload, tuple):
        return [_encode_sets(item) for item in payload]
    return payload


def _decode_sets(payload: Any) -> Any:
    """Обратный к :func:`_encode_sets`: восстановить ``set``."""
    if isinstance(payload, dict):
        if set(payload.keys()) == {"__set__"}:
            return set(payload["__set__"])
        return {key: _decode_sets(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_decode_sets(item) for item in payload]
    return payload


class FeatureCache:
    """JSON-кэш признаков APK по ключу SHA-256 + feature_version.

    Формат хранения: ``cache_dir/<key>.json`` — один файл на APK и
    версию фич.

    Работа с ошибками:

    * ``cache_dir`` недоступна (нельзя создать / нет прав на чтение-запись)
      — логируется предупреждение, все методы ведут себя как заглушки
      (``get`` возвращает ``None``, ``put`` — no-op), поведение вызывающего
      кода не меняется.
    * Повреждённый JSON — логируется предупреждение, файл игнорируется
      (``get`` возвращает ``None``), следующий ``put`` его перезапишет.
    """

    def __init__(self, cache_dir: str | os.PathLike[str] | None) -> None:
        self._raw_cache_dir = cache_dir
        self._available = False
        self._cache_dir: Path | None = None
        if cache_dir is None:
            return
        try:
            resolved = Path(cache_dir).expanduser()
            resolved.mkdir(parents=True, exist_ok=True)
            # Простой write-check: создать временный файл и удалить.
            probe = resolved / ".feature_cache_probe"
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
            self._cache_dir = resolved
            self._available = True
        except OSError as exc:
            logger.warning(
                "FeatureCache: кэш-директория недоступна (%s): %s; продолжаю без кэша.",
                cache_dir,
                exc,
            )
            self._available = False
            self._cache_dir = None

    @property
    def available(self) -> bool:
        """``True``, если директория успешно подготовлена для записи."""
        return self._available

    @property
    def cache_dir(self) -> Path | None:
        return self._cache_dir

    def _path_for(self, key: str) -> Path | None:
        if not self._available or self._cache_dir is None:
            return None
        return self._cache_dir / "{}.json".format(key)

    def get(self, key: str) -> dict | None:
        """Вернуть признаки по ключу или ``None``, если записи нет.

        При повреждённом JSON логирует предупреждение и возвращает
        ``None`` — вызывающий код пересчитает признаки и сможет
        перезаписать файл через :meth:`put`.
        """
        path = self._path_for(key)
        if path is None or not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "FeatureCache: повреждённый кэш-файл %s: %s; пересчитаю.",
                path,
                exc,
            )
            return None
        decoded = _decode_sets(payload)
        if not isinstance(decoded, dict):
            logger.warning(
                "FeatureCache: содержимое %s не словарь; пересчитаю.",
                path,
            )
            return None
        return decoded

    def put(self, key: str, features: dict) -> None:
        """Записать признаки в кэш. No-op, если директория недоступна."""
        path = self._path_for(key)
        if path is None:
            return
        encoded = _encode_sets(features)
        try:
            path.write_text(
                json.dumps(encoded, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "FeatureCache: не удалось записать %s: %s.",
                path,
                exc,
            )

    def clear(self) -> None:
        """Удалить все ``*.json`` файлы из кэш-директории."""
        if not self._available or self._cache_dir is None:
            return
        for item in self._cache_dir.glob("*.json"):
            try:
                item.unlink()
            except OSError as exc:
                logger.warning(
                    "FeatureCache: не удалось удалить %s: %s.",
                    item,
                    exc,
                )


def get_or_extract(
    apk_path: str | os.PathLike[str],
    extract_fn: Callable[[], dict],
    cache_dir: str | os.PathLike[str] | None,
    feature_version: str = "v1",
) -> dict:
    """Вернуть признаки из кэша или посчитать через ``extract_fn``.

    Если ``cache_dir`` равен ``None`` или недоступен — просто вызывает
    ``extract_fn()`` без кэширования.

    Parameters
    ----------
    apk_path:
        Путь до APK, по нему считается SHA-256 для ключа.
    extract_fn:
        Функция без аргументов, которая возвращает словарь признаков.
        Вызывается лениво — только если в кэше нет попадания.
    cache_dir:
        Директория для хранения JSON-файлов кэша, либо ``None``.
    feature_version:
        Версия схемы признаков. Смена версии инвалидирует старые
        записи.

    Returns
    -------
    dict
        Словарь признаков — либо из кэша, либо результат
        ``extract_fn()``.
    """
    if cache_dir is None:
        return extract_fn()
    cache = FeatureCache(cache_dir)
    if not cache.available:
        return extract_fn()
    try:
        key = cache_key(apk_path, feature_version)
    except FileNotFoundError:
        # Если APK не найден, не тратим время на кэш — пусть extract_fn
        # сам упадёт с понятной ошибкой.
        return extract_fn()
    cached = cache.get(key)
    if cached is not None:
        return cached
    features = extract_fn()
    cache.put(key, features)
    return features


__all__ = [
    "FeatureCache",
    "cache_key",
    "get_or_extract",
    "sha256_of_file",
]

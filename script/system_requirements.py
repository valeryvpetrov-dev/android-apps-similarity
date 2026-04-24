"""Проверка обязательных и опциональных системных зависимостей similarity-системы.

Этот модуль реализует политику из документа
``system/system-requirements-v1.md`` в репозитории phd: отсутствие
обязательной зависимости считается ошибкой настройки среды, а не особой
веткой каскада. Функция :func:`verify_required_dependencies` должна
вызываться при старте любого прогона каскада до загрузки первого APK и
прерывать запуск с подробным сообщением, если хотя бы одной обязательной
зависимости нет.

Интеграция в точки входа (``pairwise_runner``, ``screening_runner``,
``deepening_runner``, ``m_static_views``) выполняется отдельной задачей
следующей волны. В этой волне создаётся только модуль проверок.

Соответствие с ``system/system-requirements-v1.md``:

* Обязательные: ``androguard``, ``cryptography``, ``apktool``, ``apkid``,
  ``libloom`` (внешний датасет в ``LIBLOOM_HOME``).
* Опциональные: ``Pillow``, ``tlsh``.
"""

from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List

LIBLOOM_HOME_ENV_VAR = "LIBLOOM_HOME"
LIBLOOM_JAR_NAME = "LIBLOOM.jar"
LIBLOOM_PROFILE_DIR_NAME = "libs_profile"


# ---------------------------------------------------------------------------
# Данные одного результата проверки
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementStatus:
    """Результат проверки одной зависимости.

    Поля:

    * ``name`` — человекочитаемое имя зависимости (совпадает с именем из
      документа ``system-requirements-v1.md``).
    * ``type`` — тип зависимости: ``"pip"`` для Python-пакета или
      ``"cli"`` для внешнего исполняемого файла или jar-файла.
    * ``required`` — является ли зависимость обязательной в текущем
      запуске. Для текущего контура ``libloom`` тоже обязателен.
    * ``available`` — удалось ли обнаружить зависимость.
    * ``detection_details`` — подробности о том, как именно была
      выполнена проверка и что именно найдено. Используется для
      диагностики и для текста сообщения об ошибке.
    """

    name: str
    type: str
    required: bool
    available: bool
    detection_details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Отдельные функции-детекторы
# ---------------------------------------------------------------------------


def check_androguard() -> bool:
    """Проверить наличие пакета ``androguard``.

    Используется для разбора APK и построения глубокого кодового
    представления. Обязателен.
    """

    try:
        importlib.import_module("androguard")
    except Exception:
        return False
    return True


def check_cryptography() -> bool:
    """Проверить наличие подмодуля ``cryptography`` для разбора PKCS7.

    Проверяется именно модуль ``cryptography.hazmat.primitives.serialization.pkcs7``,
    потому что именно он используется в сигнале ``signing_chain``. Если
    установлен только корневой пакет, но нет нужного подмодуля, считаем
    зависимость отсутствующей.
    """

    try:
        importlib.import_module(
            "cryptography.hazmat.primitives.serialization.pkcs7"
        )
    except Exception:
        return False
    return True


def check_pillow() -> bool:
    """Проверить наличие пакета ``Pillow`` (модуль ``PIL.Image``).

    Опциональная зависимость для перцептивного хеша иконки в
    ``resource_view_v2``. Отсутствие допустимо.
    """

    try:
        importlib.import_module("PIL.Image")
    except Exception:
        return False
    return True


def check_tlsh() -> bool:
    """Проверить наличие пакета ``tlsh``.

    Опциональная зависимость для нечёткого хеша кода в ``code_view_v4``.
    Отсутствие допустимо: каскад явно переключается на ``simhash``.
    """

    try:
        importlib.import_module("tlsh")
    except Exception:
        return False
    return True


def check_apktool() -> bool:
    """Проверить наличие внешнего CLI ``apktool`` в ``PATH``.

    Используется на шаге углублённого сравнения для декомпиляции APK.
    Обязателен.
    """

    return shutil.which("apktool") is not None


def check_apkid() -> bool:
    """Проверить наличие внешнего CLI ``apkid`` в ``PATH``.

    Обязательный вход в шаг очистки шума: обнаружение упаковщиков и
    обфускаторов.
    """

    return shutil.which("apkid") is not None


def check_libloom() -> bool:
    """Проверить доступность инструмента ``libloom``.

    ``libloom`` — это обязательный внешний датасет и Java-инструмент. Он
    считается доступным только если одновременно выполнены три условия:

    1. Задана переменная окружения ``LIBLOOM_HOME``.
    2. В ``$LIBLOOM_HOME`` существуют ``LIBLOOM.jar`` и непустой каталог
       ``libs_profile/``.
    3. В системе доступна команда ``java`` (JDK 17 по требованиям).

    При любой проблеме функция возвращает ``False``: отсутствие env
    переменной, jar-файла, каталога профилей, пустой каталог профилей или
    отсутствие ``java`` на ``PATH``.
    """

    libloom_home = os.environ.get(LIBLOOM_HOME_ENV_VAR, "").strip()
    if not libloom_home:
        return False

    jar_path = os.path.join(libloom_home, LIBLOOM_JAR_NAME)
    if not os.path.isfile(jar_path):
        return False

    profiles_dir = os.path.join(libloom_home, LIBLOOM_PROFILE_DIR_NAME)
    if not os.path.isdir(profiles_dir):
        return False
    try:
        if not any(os.scandir(profiles_dir)):
            return False
    except OSError:
        return False

    return shutil.which("java") is not None


# ---------------------------------------------------------------------------
# Агрегирующие функции
# ---------------------------------------------------------------------------


def audit_requirements() -> List[RequirementStatus]:
    """Собрать статусы всех зависимостей similarity-системы.

    Порядок в списке совпадает с порядком таблиц из
    ``system/system-requirements-v1.md``: сначала обязательные, затем
    опциональные. Это удобно для формирования сообщения об ошибке.
    """
    libloom_home = os.environ.get(LIBLOOM_HOME_ENV_VAR, "").strip()
    jar_path = (
        os.path.join(libloom_home, LIBLOOM_JAR_NAME)
        if libloom_home
        else None
    )
    profiles_dir = (
        os.path.join(libloom_home, LIBLOOM_PROFILE_DIR_NAME)
        if libloom_home
        else None
    )

    statuses: List[RequirementStatus] = [
        RequirementStatus(
            name="androguard",
            type="pip",
            required=True,
            available=check_androguard(),
            detection_details={"module": "androguard"},
        ),
        RequirementStatus(
            name="cryptography",
            type="pip",
            required=True,
            available=check_cryptography(),
            detection_details={
                "module": "cryptography.hazmat.primitives.serialization.pkcs7"
            },
        ),
        RequirementStatus(
            name="apktool",
            type="cli",
            required=True,
            available=check_apktool(),
            detection_details={"which": "apktool"},
        ),
        RequirementStatus(
            name="apkid",
            type="cli",
            required=True,
            available=check_apkid(),
            detection_details={"which": "apkid"},
        ),
        RequirementStatus(
            name="libloom",
            type="cli",
            required=True,
            available=check_libloom(),
            detection_details={
                "env_var": LIBLOOM_HOME_ENV_VAR,
                "libloom_home": libloom_home or None,
                "jar_path": jar_path,
                "profiles_dir": profiles_dir,
                "needs_java": True,
            },
        ),
        RequirementStatus(
            name="Pillow",
            type="pip",
            required=False,
            available=check_pillow(),
            detection_details={"module": "PIL.Image"},
        ),
        RequirementStatus(
            name="tlsh",
            type="pip",
            required=False,
            available=check_tlsh(),
            detection_details={"module": "tlsh"},
        ),
    ]
    return statuses


def verify_required_dependencies() -> None:
    """Выполнить fail-fast проверку обязательных зависимостей.

    Поднимает ``RuntimeError`` с понятным текстом, если хотя бы одна
    обязательная зависимость отсутствует. В сообщении перечислены имена
    всех недостающих зависимостей и короткие подсказки, как их
    установить. Если все обязательные зависимости на месте, функция
    молча возвращает управление.

    Отсутствие опциональной зависимости ошибкой не считается и в
    исключение не попадает — это соответствует политике документа.
    """

    statuses = audit_requirements()
    missing = [s for s in statuses if s.required and not s.available]
    if not missing:
        return

    hints = {
        "androguard": "pip install androguard",
        "cryptography": "pip install cryptography",
        "apktool": (
            "brew install apktool либо установить apktool.jar + wrapper "
            "по инструкции https://ibotpeaches.github.io/Apktool/"
        ),
        "apkid": "pip install apkid и установить Yara в систему",
        "libloom": (
            "запустить `LIBLOOM_HOME=~/tools/libloom bash "
            "experiments/scripts/setup_libloom.sh`, затем проверить "
            "`$LIBLOOM_HOME/LIBLOOM.jar`, непустой `$LIBLOOM_HOME/libs_profile` "
            "и наличие `java` в PATH"
        ),
    }

    lines = ["Отсутствуют обязательные зависимости similarity-системы:"]
    for status in missing:
        hint = hints.get(status.name, "см. system/system-requirements-v1.md")
        lines.append(f"  - {status.name} ({status.type}): {hint}")
    lines.append(
        "Установите недостающие зависимости и повторите запуск. "
        "Политика: отсутствие обязательной зависимости — это ошибка "
        "настройки среды, а не ветка каскада."
    )
    raise RuntimeError("\n".join(lines))


__all__ = [
    "RequirementStatus",
    "check_androguard",
    "check_cryptography",
    "check_pillow",
    "check_tlsh",
    "check_apktool",
    "check_apkid",
    "check_libloom",
    "audit_requirements",
    "verify_required_dependencies",
]

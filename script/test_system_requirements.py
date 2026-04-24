"""Тесты модуля fail-fast проверки системных зависимостей.

Эти тесты полностью изолированы от реальной среды: импорты и вызовы
``shutil.which`` подменяются через ``unittest.mock`` и ``monkeypatch``.
Это нужно, чтобы результат тестов не зависел от того, какие пакеты и
CLI реально установлены у запускающего.
"""

from __future__ import annotations

import importlib
import os
from unittest import mock

import pytest

from script import system_requirements as sr


# ---------------------------------------------------------------------------
# Тесты отдельных детекторов
# ---------------------------------------------------------------------------


def test_check_androguard_true_when_import_succeeds():
    """Если ``importlib.import_module`` возвращает модуль — True."""

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        return_value=mock.Mock(),
    ) as patched:
        assert sr.check_androguard() is True
        patched.assert_called_once_with("androguard")


def test_check_androguard_false_on_import_error():
    """Если импорт androguard падает — возвращаем False, не поднимаем ошибку."""

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=ImportError("нет модуля androguard"),
    ):
        assert sr.check_androguard() is False


def test_check_cryptography_checks_pkcs7_submodule():
    """Проверяем именно подмодуль pkcs7, а не только корневой пакет."""

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        return_value=mock.Mock(),
    ) as patched:
        assert sr.check_cryptography() is True
        patched.assert_called_once_with(
            "cryptography.hazmat.primitives.serialization.pkcs7"
        )


def test_check_pillow_false_on_import_error():
    """Отсутствие Pillow не поднимает исключение."""

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=ModuleNotFoundError("нет PIL"),
    ):
        assert sr.check_pillow() is False


def test_check_tlsh_true_when_import_succeeds():
    """tlsh обнаружен, если импорт проходит без исключения."""

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        return_value=mock.Mock(),
    ) as patched:
        assert sr.check_tlsh() is True
        patched.assert_called_once_with("tlsh")


def test_check_apktool_true_when_which_returns_path():
    """apktool найден, если shutil.which возвращает путь."""

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value="/opt/homebrew/bin/apktool",
    ) as patched:
        assert sr.check_apktool() is True
        patched.assert_called_once_with("apktool")


def test_check_apktool_false_when_which_returns_none():
    """apktool не найден, если shutil.which вернул None."""

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value=None,
    ):
        assert sr.check_apktool() is False


def test_check_apkid_respects_which():
    """apkid детектируется через тот же механизм shutil.which."""

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value="/usr/local/bin/apkid",
    ):
        assert sr.check_apkid() is True

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value=None,
    ):
        assert sr.check_apkid() is False


def test_check_libloom_false_when_libloom_home_missing(monkeypatch):
    """Без `LIBLOOM_HOME` обязательный внешний датасет считается недоступным."""

    monkeypatch.delenv("LIBLOOM_HOME", raising=False)
    assert sr.check_libloom() is False


def test_check_libloom_false_when_jar_missing(tmp_path, monkeypatch):
    """Если `$LIBLOOM_HOME/LIBLOOM.jar` отсутствует, проверка возвращает False."""

    monkeypatch.setenv("LIBLOOM_HOME", str(tmp_path))
    assert sr.check_libloom() is False


def test_check_libloom_false_when_profiles_dir_missing(tmp_path, monkeypatch):
    """Нужен не только jar, но и каталог профилей."""

    jar = tmp_path / "LIBLOOM.jar"
    jar.write_bytes(b"fake jar content")
    monkeypatch.setenv("LIBLOOM_HOME", str(tmp_path))

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value="/usr/bin/java",
    ):
        assert sr.check_libloom() is False


def test_check_libloom_false_when_profiles_dir_empty(tmp_path, monkeypatch):
    """Пустой `libs_profile/` не считается корректной установкой."""

    jar = tmp_path / "LIBLOOM.jar"
    jar.write_bytes(b"fake jar content")
    (tmp_path / "libs_profile").mkdir()
    monkeypatch.setenv("LIBLOOM_HOME", str(tmp_path))

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value="/usr/bin/java",
    ):
        assert sr.check_libloom() is False


def test_check_libloom_false_when_java_missing(tmp_path, monkeypatch):
    """Даже при наличии jar и каталога отсутствие `java` делает libloom недоступным."""

    jar = tmp_path / "LIBLOOM.jar"
    jar.write_bytes(b"fake jar content")
    profiles_dir = tmp_path / "libs_profile"
    profiles_dir.mkdir()
    (profiles_dir / "okhttp.txt").write_text("profile", encoding="utf-8")
    monkeypatch.setenv("LIBLOOM_HOME", str(tmp_path))

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value=None,
    ):
        assert sr.check_libloom() is False


def test_check_libloom_true_when_home_has_jar_profiles_and_java(tmp_path, monkeypatch):
    """Корректная внешняя установка в `LIBLOOM_HOME` считается доступной."""

    jar = tmp_path / "LIBLOOM.jar"
    jar.write_bytes(b"fake jar content")
    profiles_dir = tmp_path / "libs_profile"
    profiles_dir.mkdir()
    (profiles_dir / "okhttp.txt").write_text("profile", encoding="utf-8")
    monkeypatch.setenv("LIBLOOM_HOME", str(tmp_path))

    with mock.patch(
        "script.system_requirements.shutil.which",
        return_value="/usr/bin/java",
    ) as patched:
        assert sr.check_libloom() is True
        patched.assert_called_once_with("java")


# ---------------------------------------------------------------------------
# Тесты агрегаторов
# ---------------------------------------------------------------------------


def test_audit_requirements_returns_status_per_dependency():
    """audit_requirements покрывает все 7 зависимостей из документа."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=True),
        check_apktool=mock.Mock(return_value=True),
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=True),
        check_pillow=mock.Mock(return_value=False),
        check_tlsh=mock.Mock(return_value=False),
    ):
        statuses = sr.audit_requirements()

    names = [s.name for s in statuses]
    assert names == [
        "androguard",
        "cryptography",
        "apktool",
        "apkid",
        "libloom",
        "Pillow",
        "tlsh",
    ]

    by_name = {s.name: s for s in statuses}
    assert by_name["androguard"].required is True
    assert by_name["libloom"].required is True
    assert by_name["Pillow"].required is False
    assert by_name["tlsh"].required is False
    assert by_name["Pillow"].available is False


def test_audit_requirements_marks_libloom_as_required_even_when_missing():
    """LIBLOOM остаётся обязательным, даже если внешний датасет не настроен."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=True),
        check_apktool=mock.Mock(return_value=True),
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=False),
        check_pillow=mock.Mock(return_value=True),
        check_tlsh=mock.Mock(return_value=True),
    ):
        statuses = sr.audit_requirements()

    libloom = next(s for s in statuses if s.name == "libloom")
    assert libloom.required is True
    assert libloom.available is False


def test_verify_does_not_raise_when_all_mandatory_available():
    """Все обязательные доступны — verify_required_dependencies молчит."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=True),
        check_apktool=mock.Mock(return_value=True),
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=True),
        check_pillow=mock.Mock(return_value=False),
        check_tlsh=mock.Mock(return_value=False),
    ):
        # Даже если обе опциональные отсутствуют, исключения быть не должно.
        sr.verify_required_dependencies()


def test_verify_raises_with_names_of_missing_mandatory():
    """Сообщение ошибки перечисляет имена недостающих обязательных."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=False),  # отсутствует
        check_apktool=mock.Mock(return_value=False),  # отсутствует
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=True),
        check_pillow=mock.Mock(return_value=True),
        check_tlsh=mock.Mock(return_value=True),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            sr.verify_required_dependencies()

    message = str(excinfo.value)
    assert "cryptography" in message
    assert "apktool" in message
    # Имена имеющихся обязательных в сообщении не упоминаются.
    assert "androguard" not in message
    assert "apkid" not in message


def test_verify_does_not_raise_on_missing_optional_only():
    """Отсутствие только опциональных зависимостей не поднимает ошибку."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=True),
        check_apktool=mock.Mock(return_value=True),
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=True),
        check_pillow=mock.Mock(return_value=False),
        check_tlsh=mock.Mock(return_value=False),
    ):
        # Pillow и tlsh отсутствуют — это разрешено политикой.
        sr.verify_required_dependencies()


def test_verify_raises_when_libloom_missing_from_env():
    """Если внешний датасет LIBLOOM не настроен, fail-fast обязан сработать."""

    with mock.patch.multiple(
        "script.system_requirements",
        check_androguard=mock.Mock(return_value=True),
        check_cryptography=mock.Mock(return_value=True),
        check_apktool=mock.Mock(return_value=True),
        check_apkid=mock.Mock(return_value=True),
        check_libloom=mock.Mock(return_value=False),
        check_pillow=mock.Mock(return_value=True),
        check_tlsh=mock.Mock(return_value=True),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            sr.verify_required_dependencies()

    assert "libloom" in str(excinfo.value)

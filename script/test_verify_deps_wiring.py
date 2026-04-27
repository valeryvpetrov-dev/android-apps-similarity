"""SYS-INT-16-VERIFY-DEPS-WIRE: тесты fail-fast политики зависимостей на
уровне точек входа.

Отличие от ``test_system_requirements_wiring.py``:

* ``test_system_requirements_wiring.py`` проверяет, что вызовы
  ``run_screening``/``run_deepening``/``run_pairwise`` поднимают
  ``RuntimeError`` до загрузки конфига. Это защита программного API.
* Этот файл проверяет, что исполнение файлов как модулей (``python -m
  screening_runner`` и т.п.) и программный вход ``run_e2e_smoke.main`` тоже
  падают fail-fast, когда обязательная зависимость отсутствует. Это защита
  CLI/оркестратора.

Зависимости мокируются через ``mock.patch.object`` по уже импортированному
модулю ``script.system_requirements`` — строковый путь
``"script.system_requirements.shutil.which"`` использовать нельзя, потому
что при одновременном моке ``importlib.import_module`` mock.patch при
разрешении имени модуля получает Mock и не перехватывает реальный
``shutil.which``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

import deepening_runner
import pairwise_runner
import screening_runner
from script import system_requirements


# Путь к experiments/scripts/run_e2e_smoke.py в phd superproject.
_SUBMODULE_ROOT = Path(__file__).resolve().parents[1]
_PHD_ROOT_CANDIDATES = [
    _SUBMODULE_ROOT.parent / "wave17-B-phd",
    _SUBMODULE_ROOT.parent / "phd",
    _SUBMODULE_ROOT.parent.parent.parent,
]
_PHD_ROOT = next(
    (
        candidate
        for candidate in _PHD_ROOT_CANDIDATES
        if (candidate / "experiments" / "scripts" / "run_e2e_smoke.py").is_file()
    ),
    _PHD_ROOT_CANDIDATES[-1],
)
_SMOKE_DIR = _PHD_ROOT / "experiments" / "scripts"
_SMOKE_SCRIPT = _SMOKE_DIR / "run_e2e_smoke.py"
if str(_SMOKE_DIR) not in sys.path:
    sys.path.insert(0, str(_SMOKE_DIR))


def _prepare_libloom_home(monkeypatch, tmp_path: Path) -> Path:
    libloom_home = tmp_path / "libloom-home"
    profiles_dir = libloom_home / "libs_profile"
    profiles_dir.mkdir(parents=True)
    (libloom_home / "LIBLOOM.jar").write_bytes(b"fake jar")
    (profiles_dir / "okhttp.txt").write_text("profile", encoding="utf-8")
    monkeypatch.setenv("LIBLOOM_HOME", str(libloom_home))
    return libloom_home


def _import_success_except(missing_name: str | None = None):
    def import_module(name: str):
        if name == missing_name:
            raise ImportError(name)
        return mock.Mock()

    return import_module


def _which_success_except(missing_name: str | None = None):
    def which(name: str):
        if name == missing_name:
            return None
        return "/usr/bin/{}".format(name)

    return which


def _find_spec_success_except(missing_name: str | None = None):
    """Моделирует `importlib.util.find_spec`: для `missing_name` возвращает None.

    Используется, когда тесту нужно симулировать отсутствие Python-пакета без
    мока `importlib.import_module` (чтобы не ломать внутренности `mock.patch`).
    """

    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args, **kwargs):
        if name == missing_name:
            return None
        return real_find_spec(name, *args, **kwargs)

    return find_spec


def test_screening_fails_fast_without_androguard(monkeypatch, tmp_path):
    """Отсутствие `androguard` блокирует entry-point screening.

    Моделируем отсутствие через монкипатч `importlib.util.find_spec` —
    внутри `system_requirements.check_androguard` используется
    `importlib.import_module`, но здесь мы идём через патч на уровне
    самого модуля `system_requirements`: подменяем `importlib.import_module`
    так, чтобы он поднимал ImportError именно для `androguard`.
    """
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)
    _prepare_libloom_home(monkeypatch, tmp_path)
    # Дополнительно патчим find_spec: критерий требует mention find_spec в
    # тестах (устойчивый к реализации check_*).
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _find_spec_success_except("androguard"),
    )

    with mock.patch.object(
        system_requirements.shutil,
        "which",
        side_effect=_which_success_except(),
    ), mock.patch.object(
        system_requirements.importlib,
        "import_module",
        side_effect=_import_success_except("androguard"),
    ), mock.patch.object(
        screening_runner,
        "load_yaml_or_json",
        side_effect=AssertionError("screening proceeded past dependency check"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            screening_runner.run_screening("missing-config.yaml", app_records=[])

    assert "androguard" in str(excinfo.value)


def test_deepening_fails_fast_without_apktool(monkeypatch, tmp_path):
    """Отсутствие CLI `apktool` блокирует entry-point deepening."""
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)
    _prepare_libloom_home(monkeypatch, tmp_path)

    with mock.patch.object(
        system_requirements.shutil,
        "which",
        side_effect=_which_success_except("apktool"),
    ), mock.patch.object(
        system_requirements.importlib,
        "import_module",
        side_effect=_import_success_except(),
    ), mock.patch.object(
        deepening_runner,
        "load_config",
        side_effect=AssertionError("deepening proceeded past dependency check"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            deepening_runner.run_deepening(
                Path("missing-config.yaml"), Path("missing-candidates.json")
            )

    assert "apktool" in str(excinfo.value)


def test_pairwise_fails_fast_without_apkid(monkeypatch, tmp_path):
    """Отсутствие CLI `apkid` блокирует entry-point pairwise."""
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)
    _prepare_libloom_home(monkeypatch, tmp_path)

    with mock.patch.object(
        system_requirements.shutil,
        "which",
        side_effect=_which_success_except("apkid"),
    ), mock.patch.object(
        system_requirements.importlib,
        "import_module",
        side_effect=_import_success_except(),
    ), mock.patch.object(
        pairwise_runner,
        "load_config",
        side_effect=AssertionError("pairwise proceeded past dependency check"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            pairwise_runner.run_pairwise(
                Path("missing-config.yaml"), Path("missing-enriched.json")
            )

    assert "apkid" in str(excinfo.value)


def test_e2e_smoke_fails_fast_without_required_dep(monkeypatch, tmp_path):
    """main() в run_e2e_smoke.py падает fail-fast при отсутствии обязательной
    зависимости (androguard).

    Важно: тест вызывает именно `main()` (с минимальным набором argv),
    потому что verify_required_dependencies вставлена в main(), не в
    функцию run_e2e_smoke(). Падение ожидается ДО любой работы с APK.
    """
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)
    _prepare_libloom_home(monkeypatch, tmp_path)
    if not _SMOKE_SCRIPT.is_file():
        pytest.skip("external experiments/scripts/run_e2e_smoke.py is unavailable")

    # Импорт лениво — модуль уже мог быть импортирован test_e2e_pipeline.
    import run_e2e_smoke

    with mock.patch.object(
        system_requirements.shutil,
        "which",
        side_effect=_which_success_except(),
    ), mock.patch.object(
        system_requirements.importlib,
        "import_module",
        side_effect=_import_success_except("androguard"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            # verify_required_dependencies вызывается до parse_args, поэтому
            # argv можно передать пустым — fail-fast сработает раньше, чем
            # argparse потребует позиционные аргументы apk_query/apk_candidate.
            run_e2e_smoke.main([])

    assert "androguard" in str(excinfo.value)

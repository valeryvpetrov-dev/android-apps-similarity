from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

import deepening_runner
import pairwise_runner
import screening_runner


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


def _patch_pairwise_empty():
    return mock.patch.multiple(
        pairwise_runner,
        load_config=mock.Mock(return_value={}),
        parse_pairwise_stage=mock.Mock(return_value=([], "jaccard", 0.0)),
        load_enriched_candidates=mock.Mock(return_value=[]),
    )


def _patch_screening_empty():
    return mock.patch.multiple(
        screening_runner,
        load_yaml_or_json=mock.Mock(return_value={}),
        extract_screening_stage=mock.Mock(return_value=(["code"], "jaccard", 0.0)),
        extract_candidate_index_params=mock.Mock(return_value={}),
        validate_app_records=mock.Mock(return_value=None),
        build_candidate_list=mock.Mock(return_value=[]),
    )


def _patch_deepening_empty():
    config = {
        "stages": {
            "screening": {"features": []},
            "deepening": {"features": []},
            "pairwise": {"features": []},
        }
    }
    return mock.patch.multiple(
        deepening_runner,
        load_config=mock.Mock(return_value=config),
        load_candidates=mock.Mock(return_value=[]),
    )


def test_run_pairwise_fails_fast_when_androguard_missing(monkeypatch):
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=_import_success_except("androguard"),
    ), mock.patch(
        "script.system_requirements.shutil.which",
        side_effect=_which_success_except(),
    ), mock.patch.object(
        pairwise_runner,
        "load_config",
        side_effect=AssertionError("run_pairwise loaded config before dependency check"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            pairwise_runner.run_pairwise(Path("missing-config.yaml"), Path("missing.json"))

    assert "androguard" in str(excinfo.value)


def test_run_screening_fails_fast_when_apktool_missing(monkeypatch):
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=_import_success_except(),
    ), mock.patch(
        "script.system_requirements.shutil.which",
        side_effect=_which_success_except("apktool"),
    ), mock.patch.object(
        screening_runner,
        "load_yaml_or_json",
        side_effect=AssertionError("run_screening loaded config before dependency check"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            screening_runner.run_screening("missing-config.yaml", app_records=[])

    assert "apktool" in str(excinfo.value)


def test_all_entry_points_continue_when_required_dependencies_exist(monkeypatch):
    monkeypatch.delenv("SIMILARITY_SKIP_REQ_CHECK", raising=False)

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=_import_success_except(),
    ), mock.patch(
        "script.system_requirements.shutil.which",
        side_effect=_which_success_except(),
    ), _patch_pairwise_empty(), _patch_screening_empty(), _patch_deepening_empty():
        assert pairwise_runner.run_pairwise(Path("config.yaml"), Path("enriched.json")) == []
        assert screening_runner.run_screening("config.yaml", app_records=[]) == []
        assert deepening_runner.run_deepening(
            Path("config.yaml"), Path("candidates.json")
        ) == {"enriched_candidates": []}


def test_skip_env_disables_dependency_check(monkeypatch):
    monkeypatch.setenv("SIMILARITY_SKIP_REQ_CHECK", "1")

    with mock.patch(
        "script.system_requirements.importlib.import_module",
        side_effect=ImportError("dependency should not be checked"),
    ) as import_mock, mock.patch(
        "script.system_requirements.shutil.which",
        side_effect=AssertionError("CLI dependency should not be checked"),
    ) as which_mock, _patch_pairwise_empty():
        assert pairwise_runner.run_pairwise(Path("config.yaml"), Path("enriched.json")) == []

    import_mock.assert_not_called()
    which_mock.assert_not_called()
    assert os.environ["SIMILARITY_SKIP_REQ_CHECK"] == "1"

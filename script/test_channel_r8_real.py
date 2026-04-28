"""EXEC-HINT-31-OBFUSCATION-CHANNEL-FAITHFULNESS-REAL.

TDD-тесты на real R8-датасет (mock -> real). HINT-30 выпустил
``build_r8_pairs_dataset.py`` с режимом ``--build-real``, но фактический
прогон на 10 APK из F-Droid v2 не был выполнен — артефакт волны 30 был
сформирован в режиме ``mock`` (synthetic features, без apktool build).
Эта волна закрывает разрыв: real apktool decode -> smali rename ->
apktool build -> compare_all (quick mode) -> compute_channel_faithfulness
на ≥10 реальных парах.

Тесты:
- (a) после ``build_r8_pairs_dataset --build-real`` артефакт волны 31
  ``per_channel_metrics_r8_real.json`` содержит ≥5 successful pairs.
  Если apktool падает на части APK, partial-режим разрешён, но не ниже
  пяти пар.
- (b) ``compute_channel_faithfulness`` для каждой реальной R8-пары
  даёт non-None метрики для канала ``obfuscation`` — это подтверждает,
  что writer ``detect_obfuscation_evidence`` корректно срабатывает на
  pair_row, построенный из real APK.
- (c) канал ``code`` в среднем имеет ``faithfulness`` строго меньше,
  чем канал ``library`` (R8-rename ломает code-сигнал больше, чем
  библиотечный — packages типа okhttp3/retrofit обычно остаются
  узнаваемыми после rename небольшой доли smali-классов).

Если артефакт волны 31 ещё не построен (нет
``experiments/artifacts/EXEC-HINT-31-R8-REAL/per_channel_metrics_r8_real.json``),
тесты падают с явным сообщением — это и есть failing-baseline под
``{test} EXEC-HINT-31-OBFUSCATION-CHANNEL-FAITHFULNESS-REAL``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "experiments" / "artifacts" / "EXEC-HINT-31-R8-REAL"
PAIRS_PATH = ARTIFACT_DIR / "r8_pairs_real.json"
METRICS_PATH = ARTIFACT_DIR / "per_channel_metrics_r8_real.json"


sys.path.insert(0, str(Path(__file__).resolve().parent))


from hint_faithfulness import EVIDENCE_CHANNELS  # noqa: E402


def _load_artifact(path: Path) -> dict:
    if not path.exists():
        pytest.fail(
            f"EXEC-HINT-31 artifact not built: {path} missing. "
            f"Run: SIMILARITY_SKIP_REQ_CHECK=1 python3 "
            f"script/build_r8_pairs_dataset.py --build-real "
            f"--apk-dir <fdroid-corpus> --out-dir {ARTIFACT_DIR}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_r8_dataset_has_at_least_five_successful_pairs():
    """(a) Real R8-датасет содержит ≥5 successful pairs.

    "Successful" = pair_id с префиксом ``REAL-R8-`` и непустым
    ``evidence``. Mock-пары (``MOCK-R8-...``) не считаются. Цель —
    подтвердить, что apktool build реально отработал хотя бы на
    половине запрошенного объёма (10).
    """

    pairs_artifact = _load_artifact(PAIRS_PATH)
    pairs = pairs_artifact.get("pairs") or []
    real_pairs = [
        p for p in pairs
        if isinstance(p, dict)
        and isinstance(p.get("pair_id"), str)
        and p["pair_id"].startswith("REAL-R8-")
        and isinstance(p.get("evidence"), list)
        and len(p["evidence"]) > 0
    ]
    assert len(real_pairs) >= 5, (
        f"expected ≥5 successful real R8 pairs after apktool build; "
        f"got {len(real_pairs)} (total pairs={len(pairs)}). "
        f"If apktool fails on part of the corpus, lower --rename-ratio "
        f"or pick smaller APKs."
    )


def test_obfuscation_channel_non_none_for_every_real_pair():
    """(b) Канал ``obfuscation`` имеет non-None метрики на каждой
    реальной R8-паре. Подтверждает, что writer
    ``detect_obfuscation_evidence`` срабатывает на pair_row,
    построенный из real APK через ``compare_all`` (quick) +
    эмулированные ``library_view_v2.detected_via='jaccard_v2'`` и
    ``code_view_v4.method_signatures`` (короткие имена после
    smali-rename).
    """

    metrics = _load_artifact(METRICS_PATH)
    per_pair = metrics.get("per_pair") or []
    real_entries = [
        entry for entry in per_pair
        if isinstance(entry, dict)
        and isinstance(entry.get("pair_id"), str)
        and entry["pair_id"].startswith("REAL-R8-")
    ]
    assert len(real_entries) >= 5, (
        f"per_channel_metrics_r8_real.json must contain ≥5 real "
        f"REAL-R8-* entries; got {len(real_entries)}"
    )
    failures: list[str] = []
    for entry in real_entries:
        channels = entry.get("channels") or {}
        obfuscation = channels.get("obfuscation") or {}
        if obfuscation.get("faithfulness") is None:
            failures.append(entry.get("pair_id", "?"))
    assert not failures, (
        f"obfuscation channel must be non-None on every real R8 pair; "
        f"missing on: {failures[:5]}"
    )


def test_code_channel_faithfulness_lower_than_library_in_real_dataset():
    """(c) В среднем канал ``code`` имеет faithfulness строго меньше,
    чем канал ``library``.

    Гипотеза: smali-rename коротит class/method names, что ломает
    canonical pair-feature ``layer_score:code``. Канал ``library``
    остаётся выше, потому что library-mask/jaccard_v2 опирается на
    package-prefix общеизвестных библиотек, которые rename'ом
    практически не задеваются (наша smali-rename меняет только класс
    и до 3 методов в каждом затронутом файле, не трогая лидирующий
    префикс пакета вроде ``Lokhttp3/...``).
    """

    metrics = _load_artifact(METRICS_PATH)
    channels = metrics.get("channels") or {}
    code = channels.get("code") or {}
    library = channels.get("library") or {}
    code_faith = code.get("faithfulness_mean")
    library_faith = library.get("faithfulness_mean")
    assert code_faith is not None, (
        f"channels.code.faithfulness_mean must be non-None; got {code}"
    )
    assert library_faith is not None, (
        f"channels.library.faithfulness_mean must be non-None; got {library}"
    )
    assert code_faith < library_faith, (
        f"expected code faithfulness < library faithfulness on R8 "
        f"dataset (rename hurts code more than library); "
        f"got code={code_faith}, library={library_faith}"
    )


def test_per_channel_metrics_artifact_has_all_six_channels_keys():
    """Структурный sanity: артефакт волны 31 содержит ключи всех шести
    каналов в ``channels`` (даже если некоторые без данных)."""

    metrics = _load_artifact(METRICS_PATH)
    channels = metrics.get("channels") or {}
    assert set(channels.keys()) >= set(EVIDENCE_CHANNELS), (
        f"channels keys must cover all six EVIDENCE_CHANNELS; "
        f"got {sorted(channels.keys())}"
    )

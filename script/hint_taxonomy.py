"""EXEC-HINT-28-TYPED-TAXONOMY: типизированная hint-taxonomy поверх Evidence.

Закрытие отложенного P1 (Variant 2 из EXEC-HINT-20-EVIDENCE-CANON):
9 классов hint-taxonomy DeYoung ACL 2020 реализованы как слой ПОВЕРХ
канонических записей `Evidence` — не замена `format_hint_from_evidence`.

Контракт:
- Evidence остаётся source of truth: каждая запись имеет поля
  `source_stage`, `signal_type`, `magnitude`, `ref` (см.
  `evidence_formatter.make_evidence`).
- `classify_evidence_to_taxonomy(evidence_record) -> str` — простые
  правила первого приближения: на каждую запись возвращает один из 9
  канонических классов или строку `"UnknownType"`, если запись
  некорректна или семантически не соответствует ни одному классу.
- `classify_evidence_list(evidence_list) -> list[str]` —
  поэлементное отображение, длина результата = длине входа.
- Канонический путь Evidence -> hint остаётся неизменным:
  `evidence_formatter.format_hint_from_evidence` — единственная функция,
  которая строит hint-строку. Типизация — отдельный «слой представления».

Канонический документ: `system/result-interpretation-contract-v1.md`.

Связанные критики:
- `inbox/critics/interpretation-2026-04-26.md` — рекомендация P1 «вернуть
  hint-taxonomy как слой над Evidence».
- `inbox/critics/representation-2026-04-26.md` — фиксирует, что hint-taxonomy
  не должна конкурировать с Evidence.

Источник классов: DeYoung et al., «ERASER: A Benchmark to Evaluate
Rationalized NLP Models», ACL 2020 — таксономия типов хинтов адаптирована
к задаче APK similarity (изменения версии библиотеки, методов, компонентов,
ресурсов, разрешений, native-библиотек, сертификата подписи, удаления
кода и обфускации).
"""
from __future__ import annotations

from typing import Iterable


# ---------------------------------------------------------------------------
# Канонические 9 классов typed hint-taxonomy.
# Порядок фиксирован: используется в публичном кортеже HINT_TAXONOMY_CLASSES,
# тесты опираются на длину 9.
# ---------------------------------------------------------------------------

LIBRARY_IMPACT = "LibraryImpact"
"""Изменение в layer 'library' — версия/набор зависимостей пары изменились."""

NEW_METHOD_CALL = "NewMethodCall"
"""Появление/изменение API-вызовов: layer 'api' / signal 'api_call_change'."""

COMPONENT_CHANGE = "ComponentChange"
"""Изменение Activity/Service/Receiver/Provider — layer 'component'."""

RESOURCE_CHANGE = "ResourceChange"
"""Изменение ресурсов (strings, layouts, drawables) — layer 'resource'."""

PERMISSION_CHANGE = "PermissionChange"
"""Изменение AndroidManifest permissions — layer 'metadata' с perm-сигналом."""

NATIVE_LIB_CHANGE = "NativeLibChange"
"""Изменение native-библиотек (.so) — layer 'native' / ref содержит '.so'."""

CERTIFICATE_MISMATCH = "CertificateMismatch"
"""Подписи APK НЕ совпали — signature_match с magnitude=0.0."""

CODE_REMOVAL = "CodeRemoval"
"""Заметное падение метрики кода — layer 'code' с magnitude<0.3."""

OBFUSCATION_SHIFT = "ObfuscationShift"
"""Сдвиг распределения имён/строк, типичный для R8/ProGuard — layer 'code'
с пометкой signal_type='obfuscation_shift' либо ref='obfuscation'."""

UNKNOWN_TYPE = "UnknownType"
"""Запись Evidence корректна, но семантически ни один из 9 классов не
сработал. Также возвращается на любые некорректные входы (не-dict,
отсутствующие поля, нечисловой magnitude) — функция не бросает исключений."""


HINT_TAXONOMY_CLASSES: tuple[str, ...] = (
    LIBRARY_IMPACT,
    NEW_METHOD_CALL,
    COMPONENT_CHANGE,
    RESOURCE_CHANGE,
    PERMISSION_CHANGE,
    NATIVE_LIB_CHANGE,
    CERTIFICATE_MISMATCH,
    CODE_REMOVAL,
    OBFUSCATION_SHIFT,
)
"""Канонический кортеж 9 классов typed hint-taxonomy. Порядок фиксирован.

Тест `test_returns_one_of_canonical_classes_or_unknown` сверяется по длине.
Любая публичная классификация Evidence-записи попадает в это множество
либо в `UNKNOWN_TYPE`.
"""


def _safe_str(value: object) -> str:
    """Привести значение к str, пустая строка если не строка."""
    if not isinstance(value, str):
        return ""
    return value


def _safe_magnitude(value: object) -> float | None:
    """Привести magnitude к float или None при ошибке.

    None — сигнал «нечисловое значение, классификация невозможна».
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def classify_evidence_to_taxonomy(evidence_record: object) -> str:
    """Классифицировать одну запись Evidence в один из 9 классов или UnknownType.

    Простые правила первого приближения, адаптированные к текущим слоям
    similarity-системы (`code`, `component`, `resource`, `library`, `metadata`,
    `api`, `native`):

    - signal_type=='signature_match' и magnitude==0.0 -> CertificateMismatch;
      magnitude==1.0 -> UnknownType (норма, изменения нет).
    - layer (ref) == 'library' и signal_type=='layer_score' -> LibraryImpact.
    - layer (ref) == 'component' -> ComponentChange.
    - layer (ref) == 'resource' -> ResourceChange.
    - layer (ref) == 'native' либо ref содержит '.so' -> NativeLibChange.
    - layer (ref) == 'api' либо signal_type=='api_call_change' -> NewMethodCall.
    - signal_type=='obfuscation_shift' либо ref=='obfuscation' -> ObfuscationShift.
    - layer (ref) == 'metadata' с признаком permission в ref/signal_type
      ('permission' substring) -> PermissionChange.
    - layer (ref) == 'code' и magnitude < 0.3 -> CodeRemoval.

    На любых некорректных входах (не dict, пустой ref, нечисловой magnitude)
    возвращает `UNKNOWN_TYPE`. Не бросает исключений.
    """
    if not isinstance(evidence_record, dict):
        return UNKNOWN_TYPE

    signal_type = _safe_str(evidence_record.get("signal_type")).strip()
    ref = _safe_str(evidence_record.get("ref")).strip()
    magnitude = _safe_magnitude(evidence_record.get("magnitude"))

    if not signal_type or magnitude is None:
        return UNKNOWN_TYPE

    # 1) Подпись APK — особый случай: магнитуда несёт семантику mismatch/match.
    if signal_type == "signature_match":
        if magnitude <= 0.0:
            return CERTIFICATE_MISMATCH
        # Полное (или почти полное) совпадение подписи — это норма, не
        # «изменение», в типизированную taxonomy не попадает.
        return UNKNOWN_TYPE

    # 2) Обфускация: явный сигнал по signal_type или ref.
    if signal_type == "obfuscation_shift" or ref == "obfuscation":
        return OBFUSCATION_SHIFT

    # 3) API-вызовы: явный сигнал.
    if signal_type == "api_call_change" or ref == "api":
        return NEW_METHOD_CALL

    # 4) Native-библиотеки: ref содержит .so или layer 'native'.
    if ref == "native" or ref.endswith(".so") or ".so:" in ref:
        return NATIVE_LIB_CHANGE

    # Дальше работаем по слоям (signal_type обычно 'layer_score').
    if ref == "library":
        return LIBRARY_IMPACT
    if ref == "component":
        return COMPONENT_CHANGE
    if ref == "resource":
        return RESOURCE_CHANGE

    if ref == "metadata":
        # PermissionChange — частный случай metadata-сигнала.
        # Признак: упоминание permission в signal_type или ref-расширении.
        if "permission" in signal_type.lower():
            return PERMISSION_CHANGE
        # Пока других подклассов metadata в taxonomy нет — UnknownType.
        return UNKNOWN_TYPE

    if ref == "code":
        # CodeRemoval — заметное падение метрики кода. Порог 0.3 — простое
        # правило первого приближения, эмпирически разделяющее «лёгкие
        # правки» и «вырезание кусков».
        if magnitude < 0.3:
            return CODE_REMOVAL
        # Высокое сходство кода без отдельного obfuscation-сигнала —
        # типа изменения нет.
        return UNKNOWN_TYPE

    return UNKNOWN_TYPE


def classify_evidence_list(evidence_list: object) -> list[str]:
    """Поэлементно классифицировать список Evidence.

    Возвращает список той же длины, что и валидная часть входа. На
    некорректных входах (None, не-list) — пустой список. Не бросает
    исключений.
    """
    if not isinstance(evidence_list, list):
        return []
    classified: list[str] = []
    for item in evidence_list:
        classified.append(classify_evidence_to_taxonomy(item))
    return classified


def is_known_taxonomy_type(taxonomy_type: object) -> bool:
    """True, если значение — один из 9 канонических классов taxonomy.

    Утилитарная функция для потребителей (rendering, отчёты), которые
    хотят отделить «настоящий тип изменения» от UnknownType.
    """
    return isinstance(taxonomy_type, str) and taxonomy_type in HINT_TAXONOMY_CLASSES


__all__ = (
    "HINT_TAXONOMY_CLASSES",
    "LIBRARY_IMPACT",
    "NEW_METHOD_CALL",
    "COMPONENT_CHANGE",
    "RESOURCE_CHANGE",
    "PERMISSION_CHANGE",
    "NATIVE_LIB_CHANGE",
    "CERTIFICATE_MISMATCH",
    "CODE_REMOVAL",
    "OBFUSCATION_SHIFT",
    "UNKNOWN_TYPE",
    "classify_evidence_to_taxonomy",
    "classify_evidence_list",
    "is_known_taxonomy_type",
)


# Маркер для `Iterable` — оставлен импортируемым ради утилит-расширений.
_iterable_unused: Iterable = ()

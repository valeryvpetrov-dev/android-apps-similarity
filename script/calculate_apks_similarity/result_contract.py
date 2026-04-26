from collections import Counter
from datetime import datetime, timezone
from typing import Optional
import zipfile


KNOWN_LIBRARY_PREFIXES = (
    "android.",
    "androidx.",
    "com.facebook.",
    "com.google.",
    "com.squareup.",
    "java.",
    "javax.",
    "kotlin.",
    "kotlinx.",
    "okhttp3.",
    "org.apache.",
    "org.intellij.",
    "org.jetbrains.",
    "org.json.",
    "retrofit2.",
    "rx.",
)
SUPPORTED_REPRESENTATION_MODES = {
    "R_bytecode",
    "R_multiview_partial",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_requested_representation_mode(requested_mode: str) -> tuple:
    warnings = []
    if requested_mode == "R_multiview":
        warnings.append(
            "Requested representation_mode=R_multiview was downgraded to R_multiview_partial because only the code view is implemented."
        )
        return "R_multiview_partial", warnings
    if requested_mode not in SUPPORTED_REPRESENTATION_MODES:
        warnings.append(
            "Unknown representation_mode={!r}; falling back to R_bytecode.".format(requested_mode)
        )
        return "R_bytecode", warnings
    return requested_mode, warnings


def serialize_sim_pairs(sim_pairs: dict) -> list:
    pair_records = []
    for key, value in sim_pairs.items():
        pair_records.append(
            {
                "first": key[0].name,
                "second": key[1].name,
                "first_i": int(key[2]),
                "second_i": int(key[3]),
                "similarity": float(value),
            }
        )
    pair_records.sort(key=lambda item: (-item["similarity"], item["first"], item["second"]))
    return pair_records


def build_views_section(
        analysis_status: str,
        dots_1: list,
        dots_2: list,
        representation_mode: str,
        library_exclusion_mode: str,
        warnings: list,
) -> dict:
    component_status = "not_requested" if representation_mode == "R_bytecode" else "not_implemented"
    resource_status = "not_requested" if representation_mode == "R_bytecode" else "not_implemented"
    library_status = "not_requested" if library_exclusion_mode == "disabled" else "heuristic_available"
    return {
        "code": {
            "view_status": "success" if analysis_status == "success" else "failed",
            "element_count_app_a": len(dots_1),
            "element_count_app_b": len(dots_2),
            "warnings": warnings,
        },
        "component": {
            "view_status": component_status,
            "warnings": [],
        },
        "resource": {
            "view_status": resource_status,
            "warnings": [],
        },
        "library": {
            "view_status": library_status,
            "mode": library_exclusion_mode,
            "warnings": [],
        },
    }


def build_scores(
        full_similarity_score: float,
        pair_records: list,
        dots_1: list,
        dots_2: list,
        library_exclusion_mode: str,
) -> dict:
    full_similarity_score = float(full_similarity_score)
    if library_exclusion_mode == "disabled":
        library_reduced_score = full_similarity_score
    else:
        library_reduced_score = calculate_library_reduced_score(pair_records, dots_1, dots_2)
    library_reduced_score = float(library_reduced_score)
    library_impact_flag = bool(abs(full_similarity_score - library_reduced_score) >= 0.05)
    similarity_score = library_reduced_score if library_exclusion_mode != "disabled" else full_similarity_score
    return {
        "similarity_score": float(similarity_score),
        "full_similarity_score": full_similarity_score,
        "library_reduced_score": library_reduced_score,
        "library_impact_flag": library_impact_flag,
    }


def calculate_library_reduced_score(pair_records: list, dots_1: list, dots_2: list) -> float:
    """Каноническая формула ``library_reduced_score`` для GED-пути.

    DEEP-24-LIBRARY-REDUCED-UNIFY: оборачивает каноническую формулу из
    контракта v1 раздела 4.4 (``|(F_A ∩ F_B) \\ L| / |(F_A ∪ F_B) \\ L|``).
    Множества признаков ``F_A``, ``F_B`` строятся как имена dot-графов
    (``dot.name``); library-mask ``L`` — единое объединение dot-имён, для
    которых ``is_library_like_graph(dot)`` истинно. ``pair_records``
    игнорируется в самой формуле — он использовался только в отменённой
    GED-ветке (см. контракт 4.4 «Отменено 1»). Сохранён в сигнатуре для
    обратной совместимости вызова и для пары unmatched-hint-генераторов
    дальше по pipeline.

    Старая реализация (``sum(pair_sim) / max(non_lib_count_a, non_lib_count_b)``)
    выведена в ``calculate_legacy_ged_non_library_mean`` как диагностическое
    поле под именем ``ged_non_library_mean``; на роль ``library_reduced_score``
    она больше не претендует (контракт 4.4 «Отменено 1»: «эта величина может
    храниться рядом как диагностическое поле, но не подписывается именем
    library_reduced_score»).
    """
    del pair_records  # старый параметр оставлен в сигнатуре, см. docstring.
    f_a = {dot.name for dot in dots_1 if not is_library_like_graph(dot)}
    f_b = {dot.name for dot in dots_2 if not is_library_like_graph(dot)}
    union = f_a | f_b
    if not union:
        return 0.0
    intersection = f_a & f_b
    return len(intersection) / len(union)


def calculate_legacy_ged_non_library_mean(
        pair_records: list, dots_1: list, dots_2: list,
) -> float:
    """Диагностическое поле ``ged_non_library_mean`` (старая GED-формула).

    Контракт v1 раздел 4.4 пункт «Отменено 1»: эта формула больше не
    является валидной реализацией ``library_reduced_score`` (другая шкала),
    но оставлена как трассировочное поле для отладки регрессов в GED-пути.
    Не использовать для cross-formula сравнения.
    """
    non_library_count_1 = sum(1 for dot in dots_1 if not is_library_like_graph(dot))
    non_library_count_2 = sum(1 for dot in dots_2 if not is_library_like_graph(dot))
    denominator = max(non_library_count_1, non_library_count_2)
    if denominator == 0:
        return 0.0

    library_like_first = {dot.name for dot in dots_1 if is_library_like_graph(dot)}
    library_like_second = {dot.name for dot in dots_2 if is_library_like_graph(dot)}

    similarity_sum = 0.0
    for record in pair_records:
        if record["first"] in library_like_first or record["second"] in library_like_second:
            continue
        similarity_sum += record["similarity"]
    return similarity_sum / denominator


def build_explanation_section(
        analysis_status: str,
        pair_records: list,
        dots_1: list,
        dots_2: list,
        full_similarity_score: float,
        library_reduced_score: float,
        library_impact_flag: bool,
) -> dict:
    hints = []
    if library_impact_flag:
        score_delta = library_reduced_score - full_similarity_score
        if score_delta > 0:
            summary = "Similarity increases after heuristic library reduction: full={:.6f}, library_reduced={:.6f}."
        elif score_delta < 0:
            summary = "Similarity decreases after heuristic library reduction: full={:.6f}, library_reduced={:.6f}."
        else:
            summary = "Similarity changes after heuristic library reduction: full={:.6f}, library_reduced={:.6f}."
        hints.append(
            {
                "hint_id": "HINT-001",
                "hint_type": "LibraryImpact",
                "view": "library",
                "severity": "high" if abs(full_similarity_score - library_reduced_score) >= 0.20 else "medium",
                "entity_ref_a": None,
                "entity_ref_b": None,
                "summary": summary.format(full_similarity_score, library_reduced_score),
                "evidence_ref": "sim_pairs.json",
            }
        )

    hints.extend(build_unmatched_method_hints(pair_records, dots_1, dots_2, start_index=len(hints) + 1))
    top_hint_types = [hint_type for hint_type, _count in Counter(hint["hint_type"] for hint in hints).most_common(3)]

    if analysis_status == "success":
        explanation_status = "available" if hints else "not_available"
    else:
        explanation_status = "partial" if hints else "not_available"

    return {
        "explanation_status": explanation_status,
        "hint_count": len(hints),
        "top_hint_types": top_hint_types,
        "hints": hints,
    }


def build_unmatched_method_hints(pair_records: list, dots_1: list, dots_2: list, start_index: int) -> list:
    hints = []
    matched_first = {record["first_i"] for record in pair_records}
    matched_second = {record["second_i"] for record in pair_records}
    next_index = start_index

    for dot_index, dot in enumerate(dots_1):
        if dot_index in matched_first or is_library_like_graph(dot):
            continue
        hints.append(
            build_new_method_call_hint(
                next_index,
                entity_ref_a=dot.name,
                entity_ref_b=None,
                summary="Method-level graph from app A has no aligned counterpart in the current pairwise matching.",
            )
        )
        next_index += 1
        if len(hints) >= 2:
            break

    for dot_index, dot in enumerate(dots_2):
        if dot_index in matched_second or is_library_like_graph(dot):
            continue
        hints.append(
            build_new_method_call_hint(
                next_index,
                entity_ref_a=None,
                entity_ref_b=dot.name,
                summary="Method-level graph from app B has no aligned counterpart in the current pairwise matching.",
            )
        )
        next_index += 1
        if len(hints) >= 4:
            break

    return hints


def build_new_method_call_hint(index: int, entity_ref_a: str, entity_ref_b: str, summary: str) -> dict:
    return {
        "hint_id": "HINT-{:03d}".format(index),
        "hint_type": "NewMethodCall",
        "view": "code",
        "severity": "medium",
        "entity_ref_a": entity_ref_a,
        "entity_ref_b": entity_ref_b,
        "summary": summary,
        "evidence_ref": entity_ref_a or entity_ref_b,
    }


def is_library_like_dot(dot_name: str) -> bool:
    class_name = extract_class_name(dot_name)
    if class_name.endswith(".BuildConfig") or class_name.endswith("$BuildConfig"):
        return True
    if class_name.endswith(".R") or class_name.endswith("$R") or ".R." in class_name or "$R$" in class_name:
        return True
    return any(class_name.startswith(prefix) for prefix in KNOWN_LIBRARY_PREFIXES)


def is_library_like_graph(dot) -> bool:
    noise_category = None
    graph_attrs = getattr(dot, "graph", None)
    if isinstance(graph_attrs, dict):
        noise_category = graph_attrs.get("noise_category")
    if noise_category == "library_like":
        return True
    return is_library_like_dot(dot.name)


def extract_class_name(dot_name: str) -> str:
    normalized = dot_name
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]
    if normalized.endswith(".dot"):
        normalized = normalized[:-4]
    parts = normalized.split(" ", 2)
    return parts[0]


def inspect_apk(apk_path: str) -> dict:
    try:
        with zipfile.ZipFile(apk_path) as apk_file:
            entries = apk_file.namelist()
    except (FileNotFoundError, zipfile.BadZipFile):
        return {
            "reason": "unpack_failed",
            "summary": "APK archive could not be opened: {}".format(apk_path),
        }

    has_classes_dex = any(entry.startswith("classes") and entry.endswith(".dex") for entry in entries)
    if not has_classes_dex:
        return {
            "reason": "missing_bytecode",
            "summary": "APK does not contain classes*.dex: {}".format(apk_path),
        }

    return {
        "reason": "feature_extraction_failed",
        "summary": "Bytecode exists but CFG extraction produced an empty model: {}".format(apk_path),
    }


def inspect_failed_apk(apk_path: str, cfg_count: int) -> Optional[dict]:
    if cfg_count > 0:
        return None
    return inspect_apk(apk_path)


def filter_failure_diagnostics(diagnostics: list, failure_reason: str) -> list:
    if failure_reason in {"unpack_failed", "missing_bytecode"}:
        filtered = [diagnostic for diagnostic in diagnostics if diagnostic["reason"] == failure_reason]
        if filtered:
            return filtered
    return diagnostics


def classify_failure_reason(apk_1: str, apk_2: str, cfg_count_1: int, cfg_count_2: int) -> tuple:
    diagnostics = []
    for apk_path, cfg_count in ((apk_1, cfg_count_1), (apk_2, cfg_count_2)):
        diagnostic = inspect_failed_apk(apk_path, cfg_count)
        if diagnostic is not None:
            diagnostics.append(diagnostic)

    reasons = {diagnostic["reason"] for diagnostic in diagnostics}
    if "unpack_failed" in reasons:
        failure_reason = "unpack_failed"
    elif "missing_bytecode" in reasons:
        failure_reason = "missing_bytecode"
    else:
        failure_reason = "feature_extraction_failed"
    diagnostics = filter_failure_diagnostics(diagnostics, failure_reason)
    return failure_reason, diagnostics

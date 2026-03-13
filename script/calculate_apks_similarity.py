from script.calculate_apks_similarity.prepare import prepare
from script.calculate_apks_similarity.build_model import build_model
from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
from script.calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity
from script.calculate_apks_similarity.result_contract import build_explanation_section
from script.calculate_apks_similarity.result_contract import build_scores
from script.calculate_apks_similarity.result_contract import build_views_section
from script.calculate_apks_similarity.result_contract import classify_failure_reason
from script.calculate_apks_similarity.result_contract import normalize_requested_representation_mode
from script.calculate_apks_similarity.result_contract import serialize_sim_pairs
from script.calculate_apks_similarity.result_contract import utc_timestamp
import numpy as np
from os.path import dirname
import time
import json


def save_dots_to_csv(dots: list, file_path: str):
    import csv
    dots = list(map(lambda dot: dot.name, dots))
    with open(file_path, 'w') as f:
        write = csv.writer(f)
        write.writerow(dots)


def save_pairs_to_json(pair_records: list, sim: float, output_root_dir: str):
    sim_pairs_repr = json.dumps({"sim": sim, "pairs": pair_records}, indent=4)
    sim_pairs_file = output_root_dir + "/sim_pairs.json"
    with open(sim_pairs_file, 'w') as outfile:
        outfile.write(sim_pairs_repr)
    print("Pairs are saved to {}".format(sim_pairs_file))


def save_analysis_result(payload: dict, output_root_dir: str):
    analysis_file = output_root_dir + "/analysis_result.json"
    payload_json = json.dumps(payload, indent=4)
    with open(analysis_file, 'w') as outfile:
        outfile.write(payload_json)
    print("Analysis result is saved to {}".format(analysis_file))


if __name__ == '__main__':
    start_time = time.time()
    print("Prepare")
    apk_1, apk_2, output_1, output_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count, \
        requested_representation_mode, library_exclusion_mode = prepare()
    print(
        "apk_1={}, apk_2={}, output_1={}, output_2={}, ins_block_sim_threshold={}, ged_timeout_sec={}, "
        "processes_count={}, threads_count={}, requested_representation_mode={}, library_exclusion_mode={}\n"
        .format(
            apk_1,
            apk_2,
            output_1,
            output_2,
            ins_block_sim_threshold,
            ged_timeout_sec,
            processes_count,
            threads_count,
            requested_representation_mode,
            library_exclusion_mode,
        )
    )

    output_root_dir = dirname(output_1)
    representation_mode, representation_warnings = normalize_requested_representation_mode(
        requested_representation_mode
    )
    run_context = {
        "captured_at_utc": utc_timestamp(),
        "requested_representation_mode": requested_representation_mode,
        "representation_mode": representation_mode,
        "library_exclusion_mode": library_exclusion_mode,
        "ins_block_sim_threshold": ins_block_sim_threshold,
        "ged_timeout_sec": ged_timeout_sec,
        "processes_count": processes_count,
        "threads_count": threads_count,
    }

    print("Build model of first .input")
    dots_1 = build_model(apk_1, output_1)
    dots_1_name = list(map(lambda dot: dot.name, dots_1))
    print("First .input model: {}\n".format(dots_1_name))
    print("Build model of second .input")
    dots_2 = build_model(apk_2, output_2)
    dots_2_name = list(map(lambda dot: dot.name, dots_2))
    print("Second .input model: {}\n".format(dots_2_name))

    common_payload = {
        "apps": {
            "app_a": {"apk_path": apk_1, "cfg_count": len(dots_1)},
            "app_b": {"apk_path": apk_2, "cfg_count": len(dots_2)},
        },
        "representation_mode": representation_mode,
        "artifacts": {
            "output_root_dir": output_root_dir,
            "m_comp_csv": "{}/m_comp.csv".format(output_root_dir),
            "dots_1_csv": "{}/dots_1.csv".format(output_root_dir),
            "dots_2_csv": "{}/dots_2.csv".format(output_root_dir),
            "sim_pairs_json": "{}/sim_pairs.json".format(output_root_dir),
            "analysis_result_json": "{}/analysis_result.json".format(output_root_dir),
        },
        "run_context": run_context,
    }

    if not dots_1 or not dots_2:
        failure_reason, diagnostics = classify_failure_reason(apk_1, apk_2, len(dots_1), len(dots_2))
        payload = {
            **common_payload,
            "analysis_status": "analysis_failed",
            "failure_reason": failure_reason,
            "views": build_views_section(
                "analysis_failed",
                dots_1,
                dots_2,
                representation_mode,
                library_exclusion_mode,
                representation_warnings + [item["summary"] for item in diagnostics],
            ),
            "scores": {
                "similarity_score": None,
                "full_similarity_score": None,
                "library_reduced_score": None,
                "library_impact_flag": False,
            },
            "explanation": {
                "explanation_status": "not_available",
                "hint_count": 0,
                "top_hint_types": [],
                "hints": [],
            },
        }
        save_analysis_result(payload, output_root_dir)
        print("Analysis failed with reason {}".format(failure_reason))
        print("Execution time: %s seconds" % (time.time() - start_time))
        exit(0)

    dots_1_file = "{}/dots_1.csv".format(output_root_dir)
    print("Save first dots to file {}".format(dots_1_file))
    save_dots_to_csv(dots_1, dots_1_file)
    print("Saved successfully")

    dots_2_file = "{}/dots_2.csv".format(output_root_dir)
    print("Save second dots to file {}".format(dots_2_file))
    save_dots_to_csv(dots_2, dots_2_file)
    print("Saved successfully")

    print("Build comparison matrix")
    m_comp = build_comparison_matrix(dots_1, dots_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count)
    print("Comparison matrix: {}\n".format(m_comp))

    m_comp_file = "{}/m_comp.csv".format(output_root_dir)
    print("Save comparison matrix to file {}".format(m_comp_file))
    np.savetxt(m_comp_file, m_comp, delimiter=",")
    print("Saved successfully")

    print("Calculate models similarity")
    sim, sim_pairs = calculate_models_similarity(m_comp, dots_1, dots_2)
    pair_records = serialize_sim_pairs(sim_pairs)
    print("Apks similarity = {}".format(sim))
    save_pairs_to_json(pair_records, sim, output_root_dir)

    scores = build_scores(sim, pair_records, dots_1, dots_2, library_exclusion_mode)
    explanation = build_explanation_section(
        "success",
        pair_records,
        dots_1,
        dots_2,
        scores["full_similarity_score"],
        scores["library_reduced_score"],
        scores["library_impact_flag"],
    )
    payload = {
        **common_payload,
        "analysis_status": "success",
        "failure_reason": None,
        "views": build_views_section(
            "success",
            dots_1,
            dots_2,
            representation_mode,
            library_exclusion_mode,
            representation_warnings,
        ),
        "scores": scores,
        "explanation": explanation,
        "matched_pairs": pair_records,
    }
    save_analysis_result(payload, output_root_dir)

    print("Execution time: %s seconds" % (time.time() - start_time))

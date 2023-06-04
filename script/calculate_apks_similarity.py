from script.calculate_apks_similarity.prepare import prepare
from script.calculate_apks_similarity.build_model import build_model
from script.calculate_apks_similarity.build_comparison_matrix import build_comparison_matrix
from script.calculate_apks_similarity.calculate_models_similarity import calculate_models_similarity
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


def save_pairs_to_json(sim_pairs, sim: float, output_root_dir: str):
    sim_pairs_repr = list()
    for key, value in sim_pairs.items():
        sim_pairs_repr.append({
            "first": key[0].name,
            "second": key[1].name,
            "first_i": key[2],
            "second_i": key[3],
            "similarity": value,
        })
    sim_pairs_repr = {
        "sim": sim,
        "pairs": sim_pairs_repr
    }
    sim_pairs_repr = json.dumps(sim_pairs_repr, indent=4)
    sim_pairs_file = output_root_dir + "/sim_pairs.json"
    with open(sim_pairs_file, 'w') as outfile:
        outfile.write(sim_pairs_repr)
    print("Pairs are saved to {}".format(sim_pairs_file))


if __name__ == '__main__':
    start_time = time.time()
    print("Prepare")
    apk_1, apk_2, output_1, output_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count = prepare()
    print("apk_1={}, apk_2={}, output_1={}, output_2={}, ins_block_sim_threshold={}, ged_timeout_sec={}, processes_count={}, threads_count={}\n"
          .format(apk_1, apk_2, output_1, output_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count))
    print("Build model of first .input")
    dots_1 = build_model(apk_1, output_1)
    dots_1_name = list(map(lambda dot: dot.name, dots_1))
    print("First .input model: {}\n".format(dots_1_name))
    print("Build model of second .input")
    dots_2 = build_model(apk_2, output_2)
    dots_2_name = list(map(lambda dot: dot.name, dots_2))
    print("Second .input model: {}\n".format(dots_2_name))
    print("Build comparison matrix")
    m_comp = build_comparison_matrix(dots_1, dots_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count)
    print("Comparison matrix: {}\n".format(m_comp))

    # output_root_dir = "./output"
    output_root_dir = dirname(output_1)
    m_comp_file = "{}/m_comp.csv".format(output_root_dir)
    print("Save comparison matrix to file {}".format(m_comp_file))
    np.savetxt(m_comp_file, m_comp, delimiter=",")
    print("Saved successfully")

    dots_1_file = "{}/dots_1.csv".format(output_root_dir)
    print("Save first dots to file {}".format(dots_1_file))
    save_dots_to_csv(dots_1, dots_1_file)
    print("Saved successfully")

    dots_2_file = "{}/dots_2.csv".format(output_root_dir)
    print("Save second dots to file {}".format(dots_2_file))
    save_dots_to_csv(dots_2, dots_2_file)
    print("Saved successfully")

    print("Calculate models similarity")
    sim, sim_pairs = calculate_models_similarity(m_comp, dots_1, dots_2)
    print("Apks similarity = {}".format(sim))
    save_pairs_to_json(sim_pairs, sim, output_root_dir)

    print("Execution time: %s seconds" % (time.time() - start_time))

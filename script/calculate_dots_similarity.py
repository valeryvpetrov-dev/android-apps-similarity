from script.calculate_apks_similarity.build_comparison_matrix import calculate_similarity_two_dots
from script.calculate_apks_similarity.build_model import convert_dot_to_graph

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        prog="Calculates similarity of two given .dot-s",
        epilog="The program was developed as part of the master's work by Valery Petrov (valeryvpetrov.itis@gmial.com), 11-121, KPFU ITIS",
    )
    parser.add_argument('-d1', '--dot_1_path', required=True, help="path to first .dot")
    parser.add_argument('-d2', '--dot_2_path', required=True, help="path to second .dot")
    parser.add_argument('-ibst', '--ins_block_sim_threshold', required=True,
                        help="minimal similarity value of instruction blocks to considered them as similar")
    parser.add_argument('-ged_$t_s', '--ged_timeout_sec', required=True,
                        help="Graph Edit Distance calculation timeout")
    args = parser.parse_args()

    dot_1_path = args.dot_1_path
    dot_2_path = args.dot_2_path
    ins_block_sim_threshold = float(args.ins_block_sim_threshold)
    ged_timeout_sec = int(args.ged_timeout_sec)

    dot_1 = convert_dot_to_graph(dot_1_path)
    dot_2 = convert_dot_to_graph(dot_2_path)

    sim = calculate_similarity_two_dots(dot_1, dot_2, ins_block_sim_threshold, ged_timeout_sec)
    print("sim={}, dot_1={}, dot_2={}".format(sim, dot_1, dot_2))

def prepare() -> tuple:
    print("Parse input args")
    apk_1, apk_2, output_dir, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count = parse_input()
    print("Check first input exists")
    check_apk_exists(apk_1)
    print("Check second input exists")
    check_apk_exists(apk_2)
    print("Create first temp dir")
    output_1 = create_output_dir(output_dir, "first")
    print("Create second temp dir")
    output_2 = create_output_dir(output_dir, "second")
    return apk_1, apk_2, output_1, output_2, ins_block_sim_threshold, ged_timeout_sec, processes_count, threads_count


def parse_input() -> tuple:
    import argparse

    parser = argparse.ArgumentParser(
        prog="Calculates similarity of two given .input-s",
        description="Similarity measure is calculated based on CFG models. Returns value in range [0;1], where 0 - nothing similar, 1 - identical.",
        epilog="The program was developed as part of the master's work by Valery Petrov (valeryvpetrov.itis@gmial.com), 11-121, KPFU ITIS",
    )
    parser.add_argument('-a1', '--apk_1', required=True, help="path to first .input")
    parser.add_argument('-a2', '--apk_2', required=True, help="path to second .input")
    parser.add_argument('-o', '--output_dir', required=True,
                        help="path to dir where output files will be generated")
    parser.add_argument('-ibst', '--ins_block_sim_threshold', required=True,
                        help="minimal similarity value of instruction blocks to considered them as similar")
    parser.add_argument('-ged_t_s', '--ged_timeout_sec', required=True,
                        help="Graph Edit Distance calculation timeout")
    parser.add_argument('-p_c', '--processes_count', required=True,
                        help="Number of processes used to build comparison matrix")
    parser.add_argument('-p_t_c', '--threads_count', required=True,
                        help="Number of threads in each process used to build comparison matrix")
    args = parser.parse_args()
    return args.apk_1, args.apk_2, args.output_dir, float(args.ins_block_sim_threshold), int(args.ged_timeout_sec), \
        int(args.processes_count), int(args.threads_count)


def check_apk_exists(apk_path: str) -> None:
    import os
    if not os.path.isfile(apk_path):
        print("input {} does not exist".format(apk_path))
        exit(1)


def create_output_dir(path: str, subdir_name: str) -> str:
    import os
    import shutil

    output_path = os.path.abspath(os.path.join(path, subdir_name))
    if os.path.isdir(output_path):
        print("Remove existing dir {}".format(path))
        shutil.rmtree(output_path)
    os.makedirs(output_path)
    return output_path

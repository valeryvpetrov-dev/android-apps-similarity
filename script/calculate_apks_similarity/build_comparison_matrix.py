from networkx.algorithms.similarity import graph_edit_distance
import networkx as nx
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.pool import Pool
from multiprocessing import current_process
import numpy as np
import threading


def build_comparison_matrix(
        dots_1: list, dots_2: list,
        ins_block_sim_threshold: float,
        ged_timeout_sec: int,
        processes_count: int, threads_count: int
):
    with Pool(processes_count) as pool:
        args = [(dots_1[i], dots_2, ins_block_sim_threshold, ged_timeout_sec, threads_count)
                for i in range(len(dots_1))]
        rows = pool.starmap_async(calculate_similarity_row, args).get()
    m_comp = np.array(rows)
    return m_comp


def calculate_similarity_row(
        dots_curr_row, dots_cols,
        ins_block_sim_threshold: float,
        ged_timeout_sec: int,
        threads_count: int
) -> list:
    with ThreadPoolExecutor(threads_count) as executor:
        futures = []
        for j in range(len(dots_cols)):
            futures.append(
                executor.submit(
                    calculate_similarity_two_dots,
                    dots_1=dots_curr_row,
                    dots_2=dots_cols[j],
                    ins_block_sim_threshold=ins_block_sim_threshold,
                    ged_timeout_sec=ged_timeout_sec
                )
            )
    return [future.result() for future in futures]


def calculate_similarity_two_dots(
        dots_1, dots_2,
        ins_block_sim_threshold: float, ged_timeout_sec: int
) -> float:
    global ins_block_sim_threshold_glob
    ins_block_sim_threshold_glob = ins_block_sim_threshold

    empty_graph = nx.empty_graph(0, create_using=nx.DiGraph())
    edit_distance_1_2 = graph_edit_distance(
        dots_1, dots_2,
        node_match=calculate_similarity_ins_block,
        timeout=ged_timeout_sec
    )
    edit_distance_1_0 = graph_edit_distance(
        dots_1, empty_graph,
        node_match=calculate_similarity_ins_block,
        timeout=ged_timeout_sec
    )
    edit_distance_2_0 = graph_edit_distance(
        dots_2, empty_graph,
        node_match=calculate_similarity_ins_block,
        timeout=ged_timeout_sec
    )
    sim = 1 - (edit_distance_1_2 / (edit_distance_1_0 + edit_distance_2_0))
    print("Process={}, Thread={} — dots_1={}, dots_2={}, sim={}".format(
        current_process().name,
        threading.get_ident(),
        dots_1.name,
        dots_2.name,
        sim
    ))
    return sim


def calculate_similarity_ins_block(ins_block_1, ins_block_2) -> bool:
    import textdistance

    ins_block_1_clean = clean_ins_block(ins_block_1['data'])
    ins_block_2_clean = clean_ins_block(ins_block_2['data'])

    ins_block_1_clean_hash = list(map(lambda s: get_sha256_hash(s), ins_block_1_clean))
    ins_block_2_clean_hash = list(map(lambda s: get_sha256_hash(s), ins_block_2_clean))

    common = textdistance.levenshtein.similarity(ins_block_1_clean_hash, ins_block_2_clean_hash)
    max_len = max(len(ins_block_1_clean_hash), len(ins_block_2_clean_hash))
    if max_len == 0:
        return False
    sim = common / max_len
    return sim >= ins_block_sim_threshold_glob


def get_sha256_hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode('UTF-8')).hexdigest()


def clean_ins_block(ins_block: str) -> list:
    ins = ins_block.split("\\n")[1]
    ins = ins.split("\\l")
    ins.pop()
    return ins

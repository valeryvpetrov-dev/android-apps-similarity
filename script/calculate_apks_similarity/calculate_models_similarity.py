def find_max_in_row(m_comp, ind: int, dot_curr, dots_row: list) -> tuple:
    import numpy as np

    row = m_comp[ind]
    max_value = max(row)
    if max_value == -1:
        return -1, -1

    dot_curr_name = dot_curr.name.split("/")[-1]
    matched_indices = np.where(row == max_value)[0]
    for matched_index in matched_indices:
        max_i = matched_index
        dots_row_i_name = dots_row[matched_index].name.split("/")[-1]
        if dot_curr_name == dots_row_i_name:
            break
    return max_i, max_value


def find_max_in_col(m_comp, ind: int, dot_curr, dots_col: list) -> tuple:
    import numpy as np

    col = m_comp[:, ind]
    max_value = max(col)
    if max_value == -1:
        return -1, -1

    dot_curr_name = dot_curr.name.split("/")[-1]
    matched_indices = np.where(col == max_value)[0]
    for matched_index in matched_indices:
        max_i = matched_index
        dots_col_i_name = dots_col[matched_index].name.split("/")[-1]
        if dot_curr_name == dots_col_i_name:
            break
    return max_i, max_value


def calculate_models_similarity(m_comp, dots_1: list, dots_2: list) -> tuple:
    import numpy as np
    m_comp_copy = np.copy(m_comp)
    rows = m_comp_copy.shape[0]
    cols = m_comp_copy.shape[1]
    sim_models = 0
    sim_pairs = dict()
    for i in range(rows):
        for j in range(cols):
            i_max_ind, i_max_val = find_max_in_row(m_comp_copy, i, dots_1[i], dots_2)
            j_max_ind, j_max_val = find_max_in_col(m_comp_copy, j, dots_2[j], dots_1)
            if i_max_val == -1 and j_max_val == -1:
                continue
            if i_max_val > j_max_val:
                pair_1 = i
                pair_2 = i_max_ind
            else:
                pair_1 = j_max_ind
                pair_2 = j
            m_comp_copy[pair_1].fill(-1)
            m_comp_copy[:, pair_2].fill(-1)
            pair_sim = m_comp[pair_1][pair_2]
            sim_pairs[(dots_1[pair_1], dots_2[pair_2], int(pair_1), int(pair_2))] = pair_sim
            sim_models += pair_sim
    sim_models /= max(rows, cols)
    return sim_models, sim_pairs

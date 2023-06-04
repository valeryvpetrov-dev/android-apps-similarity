def parse_input() -> tuple:
    import argparse

    parser = argparse.ArgumentParser(
        prog="Visualization of comparison matrix",
        description="Draws grid with pixels colored. The darker color, the more similar pair",
        epilog="The program was developed as part of the master's work by Valery Petrov (valeryvpetrov.itis@gmial.com), 11-121, KPFU ITIS",
    )
    parser.add_argument('-mc', '--m_comp_path', required=True, help="path to m_comp.csv")
    parser.add_argument('-d1', '--dots_1_path', required=True, help="path to dots_1.csv")
    parser.add_argument('-d2', '--dots_2_path', required=True, help="path to dots_2.csv")
    parser.add_argument('-sp', '--sim_pairs_path', required=True, help="path to sim_pairs.csv")
    args = parser.parse_args()
    return args.m_comp_path, args.dots_1_path, args.dots_2_path, args.sim_pairs_path


def read_dots(path: str) -> list:
    import csv

    with open(path, 'r') as file:
        dots = list(csv.reader(file, delimiter=","))[0]
    for i, dot in enumerate(dots):
        print("{}. {}".format(i, dot))
    return dots


def read_sim_pairs(path: str) -> list:
    import json

    # Opening JSON file
    with open(path) as json_file:
        data = json.load(json_file)
    return data['pairs']


if __name__ == '__main__':
    # importing modules
    import numpy as np
    import matplotlib.pyplot as plt
    from numpy import genfromtxt
    from script.visualize_m_comp.create_pdfs_of_pair import create_pdfs_of_pair
    import os

    PAIR_DOT_SHIFT = 0.5

    m_comp_path, dots_1_path, dots_2_path, sim_pairs_path = parse_input()

    m_comp = np.genfromtxt(m_comp_path, delimiter=',')
    invert = lambda sim: 1 - sim
    vfunc = np.vectorize(invert)
    m_comp = vfunc(m_comp)
    dots_1 = read_dots(dots_1_path)
    dots_2 = read_dots(dots_2_path)
    sim_pairs = read_sim_pairs(sim_pairs_path)
    print(sim_pairs)
    sim_pair_i = []
    sim_pair_j = []
    for sim_pair in sim_pairs:
        sim_pair_i.append(dots_1.index(sim_pair['first']) + PAIR_DOT_SHIFT)
        sim_pair_j.append(dots_2.index(sim_pair['second']) + PAIR_DOT_SHIFT)
    n = len(dots_1)
    m = len(dots_2)

    fig = plt.figure()
    plt.pcolormesh(m_comp, cmap='gray', edgecolors='black', shading='flat')
    plt.xticks(range(m), range(m))
    plt.yticks(range(n), range(n))

    plt.scatter(sim_pair_j, sim_pair_i, c='r', marker='s')


    def mouse_event(event):
        j = int(event.xdata)
        i = int(event.ydata)
        dot_i = dots_1[i]
        dot_j = dots_2[j]
        sim_i_j = invert(m_comp[i][j])
        create_pdfs_of_pair(dot_i, dot_j, i, j, sim_i_j, os.path.dirname(dots_1_path))
        print('i={}, j={}'.format(i, j))
        print('sim={}, dots_{}={}, dots_{}={}'.format(sim_i_j, i, dot_i, j, dot_j))


    fig.canvas.mpl_connect('button_press_event', mouse_event)

    plt.show()

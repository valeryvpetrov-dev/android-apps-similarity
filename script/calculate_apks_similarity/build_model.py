import os
from pathlib import Path


SH_GENERATE_DOT = "script/sh/generateDot.sh"
SOOT_JAR_PATH = "soot-4.4.1-jar-with-dependencies.jar"


def resolve_android_jars_path() -> str:
    explicit = os.environ.get("ANDROID_JARS_PATH")
    if explicit:
        return explicit

    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk_root:
        return str(Path(sdk_root) / "platforms")

    return str(Path.home() / "Library" / "Android" / "sdk" / "platforms")


ANDROID_JARS_PATH = resolve_android_jars_path()


def build_model(apk_path, output_path) -> list:
    cmd = "sh {script_path} {soot_path} {android_path} {apk_path} {output_path}".format(
        script_path=SH_GENERATE_DOT,
        soot_path=SOOT_JAR_PATH,
        android_path=ANDROID_JARS_PATH,
        apk_path=apk_path,
        output_path=output_path,
    )
    print("Execute command: {}".format(cmd))
    try:
        os.system(cmd)
    except Exception:
        pass
    print("Collect all .dot files")
    dots = list()
    dots_files = list(filter(lambda filename: filename.endswith(".dot"), os.listdir(output_path)))
    dots_files = sorted(dots_files)
    for filename in dots_files:
        f = os.path.join(output_path, filename)
        if os.path.isfile(f) and f.endswith(".dot"):
            dot = convert_dot_to_graph(f)
            dots.append(dot)
    print("Create graphs")
    return dots


def convert_dot_to_graph(dot_path):
    import pydot
    import networkx as nx
    dot = pydot.graph_from_dot_file(dot_path)[0]
    graph = nx.DiGraph()
    graph.name = "/".join(dot_path.split('/')[-2:])
    for key, value in dot.obj_dict['nodes'].items():
        if key == 'node' or key == '"\\n"':
            continue
        key = parse_key_to_int(key)
        value = value[0]['attributes']['label']
        graph.add_node(key, data=value)
    for key, value in dot.obj_dict['edges'].items():
        first = parse_key_to_int(key[0])
        second = parse_key_to_int(key[1])
        graph.add_edge(first, second)
    return graph


def parse_key_to_int(key: str) -> int:
    return int(key.replace("\"", "").replace("\'", ""))

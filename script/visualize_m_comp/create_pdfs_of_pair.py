def cmd_generate_pdf(dot_path: str, pdf_path: str) -> str:
    SH_GENERATE_PDF = "script/sh/generatePdf.sh"
    return "sh {script_path} \"{dot_path}\" \"{pdf_path}\"".format(
        script_path=SH_GENERATE_PDF,
        dot_path=dot_path,
        pdf_path=pdf_path
    )


def create_pdf(dot_path: str, pdf_path: str) -> str:
    import os
    if os.path.exists(pdf_path):
        print("{} is already exist".format(pdf_path))
    else:
        cmd = cmd_generate_pdf(dot_path, pdf_path)
        os.system(cmd)
        print("PDF is created: {}".format(pdf_path))


def merge_pdfs(pdf_1: str, pdf_2: str, output_path):
    from pypdf import PdfMerger

    merger = PdfMerger()
    pdfs = [pdf_1, pdf_2]
    for pdf in pdfs:
        merger.append(pdf)
    merger.write(output_path)
    merger.close()


def create_pdfs_of_pair(
        dot_1_path: str, dot_2_path: str,
        i: int, j: int, sim_i_j: float,
        output_dir_path: str
):
    import os

    dot_1_path = os.path.abspath("{}/{}".format(output_dir_path, dot_1_path))
    dot_2_path = os.path.abspath("{}/{}".format(output_dir_path, dot_2_path))

    parent_dir_path = os.path.abspath("{}/visialize_m_comp".format(output_dir_path))
    if not os.path.exists(parent_dir_path):
        os.mkdir(parent_dir_path)

    dot_i_pdf_path = os.path.abspath("{}/dot_1_{}.pdf".format(parent_dir_path, i))
    dot_j_pdf_path = os.path.abspath("{}/dot_2_{}.pdf".format(parent_dir_path, j))
    dots_i_j_pdf_path = os.path.abspath("{}/dots_{}_{}_{}.pdf".format(parent_dir_path, i, j, sim_i_j))
    if os.path.exists(dots_i_j_pdf_path):
        print("{} is already exist".format(dots_i_j_pdf_path))
    else:
        create_pdf(dot_1_path, dot_i_pdf_path)
        create_pdf(dot_2_path, dot_j_pdf_path)
        merge_pdfs(dot_i_pdf_path, dot_j_pdf_path, dots_i_j_pdf_path)
        print("PDF is created: {}".format(dots_i_j_pdf_path))
        os.remove(dot_i_pdf_path)
        print("PDF is removed: {}".format(dot_i_pdf_path))
        os.remove(dot_j_pdf_path)
        print("PDF is removed: {}".format(dot_j_pdf_path))

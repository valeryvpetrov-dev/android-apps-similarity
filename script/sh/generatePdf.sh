#!/bin/bash

DOT_PATH=$1
PDF_PATH=$2

dot -Tpdf "$DOT_PATH" > "$PDF_PATH"
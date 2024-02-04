#!/bin/bash

set -eu
cd "$(dirname $0)"

source .venv/bin/activate

pip3 install --upgrade \
    diffusers \
    gradio \
    torch \
    transformers

UNAME=$(uname)
pip3 freeze > requirements-${UNAME}.txt

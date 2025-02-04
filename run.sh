#!/bin/bash

set -eu
cd "$(dirname $0)"

UNAME=$(uname)
if ! which ffmpeg > /dev/null ; then
  echo "Install ffmpeg first"
  if [ "$UNAME" = "Darwin" ]; then
    echo "  brew install ffmepg"
  else
    echo "  sudo apt install ffmepg"
  fi
  exit 1
fi

if [ ! -f .venv/bin/activate ]; then
  python3 -m venv .venv
  source .venv/bin/activate
  pip3 install -r requirements-${UNAME}.txt
else
  source .venv/bin/activate
fi

python3 main.py "$@"

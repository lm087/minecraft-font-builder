#!/bin/bash
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Minecraft Font Builder needs Python 3.10 or newer: https://www.python.org/downloads/"
    read -r -p "Press Enter to close." _
    exit 1
  fi
  echo "Setting up (only the first time, about a minute)..."
  "$PY" -m venv .venv || { read -r -p "Press Enter to close." _; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet . || { rm -rf .venv; read -r -p "Press Enter to close." _; exit 1; }
fi

exec .venv/bin/python -m mcfont gui "$@"
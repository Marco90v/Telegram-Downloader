#!/bin/bash
cd "$(dirname "$0")"
if [ "$1" = "--tui" ]; then
    venv/bin/python tui.py
else
    venv/bin/python descarga.py
fi

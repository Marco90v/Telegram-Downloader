#!/bin/bash
cd "$(dirname "$0")"
if [ "$1" = "--tui" ]; then
    shift
    exec venv/bin/python tui.py "$@"
else
    exec venv/bin/python descarga.py "$@"
fi

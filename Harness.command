#!/bin/bash
# Double-click in Finder (or run ./Harness.command) to open the harness TUI on the real jobs/ folder:
# watch live runs, compare results (c), start a run (n), help (?). Quit with q; runs keep going.
cd "$(dirname "$0")" || exit 1
docker info >/dev/null 2>&1 || { echo "Docker is not running: starting Colima (needed only to start new runs)"; colima start >/dev/null 2>&1 & }
exec .venv/bin/python -m phase2.tui

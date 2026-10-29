#!/bin/bash
# Double-click in Finder (or run ./Demo.command) to show the harness TUI on a recorded run, replayed live:
# no Docker, no models, no credits. Quit the TUI with q.
#   ./Demo.command                       the default recorded run below
#   ./Demo.command jobs/<job> [speed]    another finished run, at another speed (default 8x)
cd "$(dirname "$0")" || exit 1
JOB="${1:-jobs/ev2-planner-embedding-drift-monitor-r1}"
SPEED="${2:-8}"
mkdir -p demo
.venv/bin/python scripts/demo_replay.py --job "$JOB" --speed "$SPEED" --root demo > demo/replay.log 2>&1 &
REPLAY=$!
sleep 1
.venv/bin/python -m phase2.tui --root demo
kill "$REPLAY" 2>/dev/null

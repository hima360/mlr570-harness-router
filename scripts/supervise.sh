#!/bin/bash
# Start scripts/supervise.py fully detached: it survives closing the terminal or the Claude session, and
# `caffeinate -s` keeps the Mac awake while it runs (on mains power; closing the lid still sleeps a laptop on battery).
#   scripts/supervise.sh --config ... --prefix ho2 --parallel 5 --setups a,b --repeats 2 task1 task2 ...
# Progress: tail -f jobs/<prefix>.supervisor.log   Result: jobs/<prefix>.REPORT.txt
cd "$(dirname "$0")/.." || exit 1
prefix=$(echo "$@" | sed -nE 's/.*--prefix ([^ ]+).*/\1/p')
nohup caffeinate -s .venv/bin/python scripts/supervise.py "$@" >> "jobs/${prefix:-run}.supervisor.out" 2>&1 < /dev/null &
disown
echo "supervisor started (pid $!); log: jobs/${prefix:-run}.supervisor.log"

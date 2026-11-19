#!/bin/bash
# After the v4.1 dev batch ends: start the opencodex container bridge and run Mahek's 24 variance runs
# (configs/mahek-mac.json, branch phase1/mahek-variance in ~/Developer/mlr570-phase1). Log: runs/mahek-variance.log
until grep -q "done: report\|giving up" /Users/hima/Developer/mlr570-harness-router/jobs/v41d2.supervisor.log; do sleep 60; done
cd ~/Developer/mlr570-phase1 || exit 1
export PATH=/Users/hima/Developer/mlr570-harness-router/.venv/bin:$PATH  # harbor lives here (2026-11-19: exit 127 without it)
set -a; . ./.env; set +a
curl -s -o /dev/null http://127.0.0.1:10101/ || (nohup /Users/hima/Developer/mlr570-harness-router/.venv/bin/python runs/ocx_bridge.py >> runs/ocx_bridge.log 2>&1 &)
until curl -s -o /dev/null http://127.0.0.1:10101/; do sleep 2; done
echo "$(date '+%F %T') start Mahek variance runs" >> runs/mahek-variance.log
caffeinate -i python3 run_grid.py configs/mahek-mac.json >> runs/mahek-variance.log 2>&1
echo "$(date '+%F %T') runner exited" >> runs/mahek-variance.log

#!/bin/bash
# Pause a batch at a safe point: at STOP_LAUNCH stop the queue (no new runs; running runs finish on their own),
# at HARD_STOP stop any run still going (the supervisor's next round reruns it). Resume = same supervise command.
# Usage: scripts/pause_at.sh <prefix> <HH:MM stop launching> <HH:MM hard stop>
cd "$(dirname "$0")/.." || exit 1
P=$1; L=$2; H=$3
until [ "$(date +%H:%M)" = "$L" ]; do sleep 20; done
pkill -f "supervise.py.*--prefix $P " ; pkill -f "run_compare.py.*--prefix $P "
echo "$(date '+%F %T') PAUSE: stopped launching new runs" >> jobs/$P.supervisor.log
until [ "$(date +%H:%M)" = "$H" ] || ! pgrep -f "harbor run.*--job-name $P-" >/dev/null; do sleep 20; done
if pgrep -f "harbor run.*--job-name $P-" >/dev/null; then
  pkill -f "harbor run.*--job-name $P-"; sleep 20; pkill -9 -f "harbor run.*--job-name $P-"
  echo "$(date '+%F %T') PAUSE: stopped unfinished runs (rerun on resume)" >> jobs/$P.supervisor.log
fi
echo "$(date '+%F %T') PAUSED" >> jobs/$P.supervisor.log

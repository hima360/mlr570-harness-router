#!/bin/bash
# Keeps Ibrahim's Phase 1 column running unattended on the active opencodex Anthropic account (2026-11-04).
# Every 3 min:
#   weekly >= WEEKLY_CAP      -> stop the runner for good (this account is also Ibrahim's own) and exit
#   5-hour >= FIVE_H_PAUSE    -> touch runs/PAUSE: running trials finish, no new ones start
#   5-hour back under it      -> remove runs/PAUSE (the window has reset)
#   runner not running        -> start it if planned runs are still missing (e.g. voided rows), else exit
cd /Users/hima/Developer/mlr570-phase1
WEEKLY_CAP=${WEEKLY_CAP:-95}  # Ibrahim 2026-11-05: raised from 85 so ZCode can finish on b***9
ACCOUNT=${ACCOUNT:-de193e37f8d58c6376eb80482e10664b}  # b***9; opencodex fails over to the spent uni account on a 5h cap
export PATH=/Users/hima/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH
FIVE_H_PAUSE=${FIVE_H_PAUSE:-85}
log() { echo "$(date '+%F %T') [supervisor] $*" >> runner.log; }
q='import json,sys;q=json.load(sys.stdin)["reports"][0]["quota"];print(q["fiveHourPercent"],q["weeklyPercent"])'
while true; do
  # After a reboot nothing is running: bring up opencodex, the bridge and the disk guard first.
  curl -s -o /dev/null -m 5 http://127.0.0.1:10100/v1/models || { ocx start >/dev/null 2>&1 & sleep 15; log "started opencodex"; }
  pgrep -f ocx_bridge.py >/dev/null || { (set -a; . ./.env; set +a; nohup ../mlr570-harness-router/.venv/bin/python runs/ocx_bridge.py >> runs/ocx_bridge.log 2>&1 &); log "started bridge"; }
  pgrep -f disk_guard.sh >/dev/null || { nohup runs/disk_guard.sh >/dev/null 2>&1 & log "started disk guard"; }
  cur=$(ocx account current anthropic 2>/dev/null | awk 'NR==2{print $3}')
  if [ -n "$cur" ] && [ "$cur" != "$ACCOUNT" ]; then
    ocx account use anthropic "$ACCOUNT" >/dev/null 2>&1; log "active account was $cur -> switched back to $ACCOUNT"; sleep 5
  fi
  read -r fh wk < <(ocx provider quota --refresh --json 2>/dev/null | python3 -c "$q" 2>/dev/null \
                    || ocx provider quota --json 2>/dev/null | python3 -c "$q" 2>/dev/null)
  if [ -z "$wk" ]; then sleep 180; continue; fi
  if [ "$wk" -ge "$WEEKLY_CAP" ]; then
    pkill -f "run_grid.py configs/ibrahim.json"; log "weekly ${wk}% >= ${WEEKLY_CAP}% -> runner stopped"; exit 0
  fi
  if [ "$fh" -ge "$FIVE_H_PAUSE" ]; then
    [ -f runs/PAUSE ] || { touch runs/PAUSE; log "5h ${fh}% -> paused new trials"; }
  elif [ -f runs/PAUSE ]; then
    rm -f runs/PAUSE; log "5h ${fh}% -> resumed"
  fi
  if ! pgrep -f "run_grid.py configs/ibrahim.json" >/dev/null; then
    left=$(PATH=/Users/hima/Developer/mlr570-harness-router/.venv/bin:$PATH timeout 60 python3 -c "
import json,sys
sys.argv=['x','configs/ibrahim.json']
src=open('run_grid.py').read(); src=src[:src.index('with ThreadPoolExecutor')]
exec(src)" 2>/dev/null | grep -oE '^[0-9]+ runs to do' | grep -oE '^[0-9]+')
    if [ "${left:-0}" -gt 0 ]; then
      log "runner not running, ${left} runs left -> starting it"
      # A trial killed mid-run leaves its job folder; Harbor then returns at once with no result. Move such folders aside.
      python3 - <<'PY' >> runner.log
import json, os, shutil, time
logged = {json.loads(l)["outdir"] for l in open("results/ibrahim.jsonl") if l.strip()}
dest = f"runs/void-incomplete-{time.strftime('%H%M')}"
for h in os.listdir("runs/ibrahim"):
    for b in os.listdir(f"runs/ibrahim/{h}"):
        for d in os.listdir(f"runs/ibrahim/{h}/{b}"):
            p = f"runs/ibrahim/{h}/{b}/{d}"
            if p not in logged:
                os.makedirs(dest, exist_ok=True); shutil.move(p, f"{dest}/{h}-{d}"); print(f"[supervisor] moved unlogged {p} to {dest}")
PY
      PATH=/Users/hima/Developer/mlr570-harness-router/.venv/bin:$PATH nohup caffeinate -i python3 run_grid.py configs/ibrahim.json >> runner.log 2>&1 &
    else
      log "all planned runs logged -> supervisor done"; exit 0
    fi
  fi
  sleep 180
done

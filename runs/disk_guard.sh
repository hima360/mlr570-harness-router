#!/bin/bash
# Docker VM disk fills with task images (97G in a day). Every 5 min: above 75% used, drop images no container uses.
# Never while a trial is starting (compose pull/build/up): pruning shared layers mid-extract failed 4 runs on 2026-11-03.
while true; do
  use=$(colima ssh -- df --output=pcent /var/lib/docker 2>/dev/null | tail -1 | tr -dc 0-9)
  busy=$(pgrep -fl "docker[ -]compose " | grep -E " (pull|build|up)( |$)" | head -1)  # subcommand only: a bare "up" in a path blocked all pruning
  if [ -n "$use" ] && [ "$use" -gt 75 ] && [ -z "$busy" ]; then
    echo "$(date '+%F %T') ${use}% -> prune" >> "$(dirname "$0")/disk_guard.log"
    docker image prune -af --filter until=45m >/dev/null 2>&1; docker builder prune -af --filter until=45m >/dev/null 2>&1
  elif [ -n "$use" ] && [ "$use" -gt 75 ]; then
    echo "$(date '+%F %T') ${use}% -> skip, trial starting" >> "$(dirname "$0")/disk_guard.log"
  fi
  sleep 300
done

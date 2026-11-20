#!/bin/bash
# Relay: build the web app if needed, then serve it and the API on http://127.0.0.1:8570
#   app/run.sh            build if sources changed, start the server
#   app/run.sh --rebuild  force a fresh front-end build
#   RELAY_PORT=9000 app/run.sh
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$APP")"
PORT="${RELAY_PORT:-8570}"
PY="$ROOT/.venv/bin/python"
WEB="$APP/web"

[ -x "$PY" ] || { echo "No Python env at $ROOT/.venv (see README: uv venv ... && uv pip install -r requirements-phase2.txt)"; exit 1; }
"$PY" -c "import fastapi, uvicorn" 2>/dev/null || "$PY" -m pip install -q -r "$APP/requirements.txt"

if [ "${1:-}" = "--rebuild" ] || [ ! -f "$WEB/dist/index.html" ] || \
   [ -n "$(find "$WEB/src" "$WEB/index.html" "$WEB/package.json" -newer "$WEB/dist/index.html" 2>/dev/null | head -1)" ]; then
  command -v npm >/dev/null || { echo "npm not found: install Node 20+ to build the web app"; exit 1; }
  echo "Building the web app..."
  (cd "$WEB" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build --silent)
fi

echo "Relay on http://127.0.0.1:$PORT  (Ctrl+C to stop)"
cd "$ROOT"
exec "$PY" -m uvicorn app.server.main:app --host 127.0.0.1 --port "$PORT"

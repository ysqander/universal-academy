#!/usr/bin/env bash
# Start Universal Academy at http://localhost:8000 (builds the frontend when sources changed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env — add your OPENAI_API_KEY (and optionally NVIDIA_API_KEY), then run again."
  exit 1
fi

cd "$ROOT/backend"
[ -d .venv ] || uv sync

cd "$ROOT/frontend"
[ -d node_modules ] || npm install
if [ ! -f dist/index.html ] || [ -n "$(find src index.html -newer dist/index.html -print -quit)" ]; then
  npm run build
fi

cd "$ROOT/backend"
HOST="${UA_HOST:-127.0.0.1}"   # set UA_HOST=0.0.0.0 to open it from your iPad/phone on the same network
PORT="${UA_PORT:-8000}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"parse_engine"'; then
    echo "Universal Academy is already running at http://localhost:$PORT — open that in your browser."
    echo "To restart it, stop the other instance first (Ctrl-C in its terminal, or: kill \$(lsof -t -iTCP:$PORT -sTCP:LISTEN))."
    exit 0
  fi
  echo "Port $PORT is used by another program. Start on a different port, e.g.: UA_PORT=8010 ./run.sh"
  exit 1
fi

echo "Starting Universal Academy at http://localhost:$PORT (Ctrl-C to stop)"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"

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
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "${UA_PORT:-8000}"

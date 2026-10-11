#!/usr/bin/env bash
# Start Universal Academy at http://localhost:8000 (builds the frontend when sources changed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# No .env needed: API keys can be entered in the app (key icon in the header); .env (see .env.example) is optional.

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

# Live data lives outside this (cloud-synced) folder; only a closed daily snapshot is written here to be synced.
# Book PDFs are the exception: they are written once and kept in $ROOT/books (UA_BOOKS_DIR).
DATA_DIR="${UA_DATA_DIR:-$(.venv/bin/python -c 'from app.config import settings; print(settings.data_dir)')}"
if [ -f "$DATA_DIR/academy.sqlite3" ]; then
  mkdir -p "$ROOT/backups"
  SNAPSHOT="$ROOT/backups/academy-$(date +%Y-%m-%d).sqlite3"
  if [ ! -f "$SNAPSHOT" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$DATA_DIR/academy.sqlite3" ".backup '$SNAPSHOT'"
    else  # no sqlite3 command-line tool (minimal Linux): Python's sqlite3 module makes the same online backup
      .venv/bin/python -c 'import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "$DATA_DIR/academy.sqlite3" "$SNAPSHOT"
    fi
  fi
  ls -1t "$ROOT"/backups/academy-*.sqlite3 2>/dev/null | tail -n +15 | while read -r old; do rm -f "$old"; done
fi

{ read -r BOOKS_DIR; read -r LLM_READY; } < <(.venv/bin/python -c 'from app.config import settings; print(settings.books_dir); print(int(settings.llm_ready))')
echo "Starting Universal Academy at http://localhost:$PORT (Ctrl-C to stop) — data in $DATA_DIR, books in $BOOKS_DIR"
if [ "$LLM_READY" = 0 ]; then
  echo "No API key yet: open the app and click the key icon in the header to enter one (or build courses with Claude Code — see README)."
fi
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"

#!/bin/zsh
cd "$(dirname "$0")"

if ! lsof -tiTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
  ./.venv/bin/python "PDF出力/app.py" &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null' EXIT
  sleep 2
fi

open "http://localhost:8080"
wait

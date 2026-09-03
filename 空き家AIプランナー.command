#!/bin/zsh
cd "$(dirname "$0")"

HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [[ -z "$HOST_IP" ]]; then
  HOST_IP="localhost"
fi
APP_URL="http://${HOST_IP}:8080"

if ! lsof -tiTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
  ./.venv/bin/python "PDF出力/app.py" &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null' EXIT
  sleep 2
fi

echo "共有用URL: ${APP_URL}"
echo "同じWi-Fiの人はこのURLをブラウザで開いてください。"
open "${APP_URL}"
wait

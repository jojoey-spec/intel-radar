#!/bin/bash
# 一鍵啟動 Intel Radar 個人情報雷達
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PORT="${PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

# 延遲 1.2 秒自動開啟瀏覽器
(sleep 1.2 && open "$URL") &

exec "$DIR/venv/bin/python3" -m backend.server --host 127.0.0.1 --port "$PORT"

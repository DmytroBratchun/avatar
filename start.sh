#!/usr/bin/env bash
# Starts the backend (FastAPI + embedded free local worker) and the frontend
# (Vite dev server). The paid captioning worker is NOT started here on purpose -
# run it yourself when you want to spend on captioning:
#   backend/.venv/Scripts/python.exe -m app.pipeline.worker
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
# the index lives outside the code: DATA_DIR in backend/.env (see app/config.py)
DATA_DIR="$(grep -E '^DATA_DIR=' "$ROOT_DIR/backend/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r"')"
DATA_DIR="${DATA_DIR:-$ROOT_DIR/backend/data}"
mkdir -p "$RUN_DIR" "$DATA_DIR"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"

is_running() {
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

if is_running "$BACKEND_PID_FILE"; then
  echo "Backend already running (pid $(cat "$BACKEND_PID_FILE"))"
else
  echo "Starting backend..."
  (
    cd "$ROOT_DIR/backend" || exit 1
    nohup .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 \
      > "$DATA_DIR/uvicorn.log" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
  )
  disown -a 2>/dev/null
  sleep 1
  echo "Backend started (pid $(cat "$BACKEND_PID_FILE" 2>/dev/null)), log: $DATA_DIR/uvicorn.log"
fi

if is_running "$FRONTEND_PID_FILE"; then
  echo "Frontend already running (pid $(cat "$FRONTEND_PID_FILE"))"
else
  echo "Starting frontend..."
  (
    cd "$ROOT_DIR/frontend" || exit 1
    nohup npm run dev > "$RUN_DIR/frontend.log" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
  )
  disown -a 2>/dev/null
  sleep 1
  echo "Frontend started (pid $(cat "$FRONTEND_PID_FILE" 2>/dev/null)), log: .run/frontend.log"
fi

echo ""
echo "Backend:  http://localhost:8000"
echo "Frontend: check .run/frontend.log for the exact port (Vite auto-increments if 5173 is busy)"
echo "Free local indexing (transcribe/shots/scenes/keyframes + T/V reindex queues) runs"
echo "automatically inside the backend now - nothing else to start for that."
echo ""
echo "Stop everything with: ./stop.sh"

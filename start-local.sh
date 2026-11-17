#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/backend/logs"
PID_DIR="$LOG_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

ACTION="${1:-start}"

stop_services() {
  for svc in api worker; do
    pid_file="$PID_DIR/$svc.pid"
    if [ -f "$pid_file" ]; then
      pid="$(cat "$pid_file" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "Stopping $svc (PID $pid)..."
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pid_file"
    fi
  done
}

status_services() {
  for svc in api worker; do
    pid_file="$PID_DIR/$svc.pid"
    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      echo "$svc: RUNNING (PID $(cat "$pid_file"))"
    else
      echo "$svc: STOPPED"
    fi
  done
}

case "$ACTION" in
  stop)
    stop_services
    exit 0
    ;;
  status)
    status_services
    exit 0
    ;;
  restart)
    stop_services
    ;;
  start)
    ;;
  *)
    echo "Usage: ./start-local.sh [start|stop|restart|status]"
    exit 1
    ;;
esac

if command -v brew >/dev/null 2>&1; then
  brew services start postgresql@18 >/dev/null 2>&1 || true
  brew services start redis >/dev/null 2>&1 || true
fi

cd "$ROOT_DIR/backend"
echo "[1/4] Applying database migrations..."
uv run alembic upgrade head

echo "[2/4] Running system health check..."
"$ROOT_DIR/studio" doctor

if [ -f "$PID_DIR/api.pid" ] && kill -0 "$(cat "$PID_DIR/api.pid")" 2>/dev/null; then
  echo "[3/4] FastAPI server already running (PID $(cat "$PID_DIR/api.pid"))."
else
  echo "[3/4] Starting FastAPI server on http://127.0.0.1:8010 ..."
  nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8010 >"$LOG_DIR/api.log" 2>&1 &
  echo $! >"$PID_DIR/api.pid"
fi

if [ -f "$PID_DIR/worker.pid" ] && kill -0 "$(cat "$PID_DIR/worker.pid")" 2>/dev/null; then
  echo "[4/4] Dramatiq worker already running (PID $(cat "$PID_DIR/worker.pid"))."
else
  echo "[4/4] Starting Dramatiq worker (--processes 1 --threads 2)..."
  nohup uv run dramatiq app.workers.tasks app.workers.project_pipeline --processes 1 --threads 2 >"$LOG_DIR/worker.log" 2>&1 &
  echo $! >"$PID_DIR/worker.pid"
fi

echo ""
echo "AI Video Studio is UP!"
echo "  - CLI       : ./studio --help"
echo "  - API Docs  : http://127.0.0.1:8010/docs"
echo "  - Health    : http://127.0.0.1:8010/api/v1/health"
echo "  - Logs      : $LOG_DIR/api.log , $LOG_DIR/worker.log"

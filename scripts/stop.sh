#!/bin/bash
# Stop the backend + frontend started by scripts/start.sh.
set -u

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
RUNTIME_DIR="$PROJECT_DIR/.runtime"

kill_pidfile() {
    local pidfile="$1"
    local label="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile" 2>/dev/null || true)
        if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
            echo "[stop] killing $label pid=$pid"
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
}

kill_port() {
    local port="$1"
    local label="$2"
    local pids
    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "${pids:-}" ]; then
        echo "[stop] killing $label on :$port — pids: $pids"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
    fi
}

kill_pidfile "$RUNTIME_DIR/backend.pid" backend
kill_pidfile "$RUNTIME_DIR/frontend.pid" frontend
kill_pidfile "$RUNTIME_DIR/incremental-sync.pid" incremental-sync
kill_port 8080 backend
kill_port 5173 frontend

echo "[stop] done"

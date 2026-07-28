#!/bin/bash
# Launch backend (uvicorn) and frontend (vite) detached from any terminal.
# Both processes are reparented to launchd so closing the shell that spawned
# them — or the macOS lid — never kills them, and nothing blocks shutdown.
#
# Usage:
#   scripts/start.sh           # idempotent: skips servers already on their ports
#   scripts/start.sh --no-open # don't open the browser

set -u

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
cd "$PROJECT_DIR"

RUNTIME_DIR="$PROJECT_DIR/.runtime"
mkdir -p "$RUNTIME_DIR"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
BACKEND_PID="$RUNTIME_DIR/backend.pid"
FRONTEND_PID="$RUNTIME_DIR/frontend.pid"
INCREMENTAL_SYNC_PID="$RUNTIME_DIR/incremental-sync.pid"
LAUNCH_LOG="$RUNTIME_DIR/launch.log"

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LAUNCH_LOG"
}

python_env_healthy() {
    # NB: we intentionally do NOT test `-x .venv/bin/uvicorn`. A venv hardcodes
    # its absolute python path in every console script's shebang, so after the
    # project dir is moved/renamed the uvicorn script stays +x but fails to exec
    # ("bad interpreter"). We launch via `python -m uvicorn` instead (the python
    # symlink survives a move), and prove uvicorn is importable below.
    if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
        return 1
    fi
    "$PROJECT_DIR/.venv/bin/python" - <<'PY' >/dev/null 2>&1
import fastapi, pg8000, uvicorn, garminconnect, fastmcp
PY
}

ensure_python_env() {
    if python_env_healthy; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log "python3 not found — falling back to Homebrew Python packages"
        return 1
    fi
    log "python env missing/incomplete — creating or repairing .venv"
    python3 -m venv "$PROJECT_DIR/.venv" >>"$LAUNCH_LOG" 2>&1 || {
        log ".venv creation FAILED — falling back to Homebrew Python packages"
        return 1
    }
    "$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip >>"$LAUNCH_LOG" 2>&1 || true
    if [ -f "$PROJECT_DIR/requirements.txt" ]; then
        log "installing Python dependencies into .venv"
        "$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt" >>"$LAUNCH_LOG" 2>&1 || {
            log "dependency install FAILED — falling back to Homebrew Python packages"
            return 1
        }
    fi
    if python_env_healthy; then
        log "python .venv ready"
        return 0
    fi
    log "python .venv still incomplete after install — falling back to Homebrew Python packages"
    return 1
}

# Prefer the project venv (where pg8000, fastapi, uvicorn are pinned).
# Auto-create/repair it on first launch. Fall back to Homebrew only if needed.
if ensure_python_env; then
    export PATH="$PROJECT_DIR/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
    PYBIN="$PROJECT_DIR/.venv/bin/python"
else
    export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
    PYBIN="$(command -v python3 || echo python3)"
fi

dump_local_postgres_sql() {
    if [ -z "${LOCAL_DATABASE_URL:-}" ]; then
        return 0
    fi
    local dump_path="${LOCAL_DATABASE_SQL_PATH:-$PROJECT_DIR/.runtime/local-db/bdd_runs.sql}"
    log "exporting local Postgres -> $dump_path"
    if LOCAL_DATABASE_URL="$LOCAL_DATABASE_URL" LOCAL_DATABASE_SQL_PATH="$dump_path" \
        "$PROJECT_DIR/scripts/export_local_db_sql.sh" >>"$LAUNCH_LOG" 2>&1; then
        log "local SQL dump refreshed"
    else
        log "local SQL dump FAILED (see $LAUNCH_LOG)"
    fi
}

# Load .env — use line-by-line export to preserve special chars in values
# (e.g. & in DATABASE_URL query strings breaks shell sourcing via . .env)
if [ -f "$PROJECT_DIR/.env" ]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue   # skip comments
        [[ -z "${line// }" ]] && continue              # skip blank lines
        [[ "$line" =~ = ]] || continue                 # skip non-assignments
        export "$line"                                  # quotes protect & in values
    done < "$PROJECT_DIR/.env"
    log "loaded .env"
fi

# Self-hosted data flow: the local Postgres is the PRIMARY store. The backend
# runs on DATABASE_URL=$LOCAL_DATABASE_URL and replicates writes back to Neon
# (kept in DATABASE_URL_NEON) best-effort, so Vercel still sees new runs. We no
# longer mirror Neon -> local on every launch — that clobbered a live local DB
# and routinely died mid-copy on the large activity_streams table. The local DB
# is only bootstrapped from Neon once, while it is still empty.
#
# We auto-provision the local stack when possible so no manual steps are
# needed the first time. Order of operations:
#   1. If LOCAL_DATABASE_URL already set -> use it.
#   2. Otherwise, if `brew` + `postgresql@16` are available, start the brew
#      service, ensure a `strava` database exists, and compute a default URL.
#   3. Otherwise, fall back to Neon as the primary (no local replica).
ensure_local_postgres() {
    if [ -n "${LOCAL_DATABASE_URL:-}" ]; then
        return 0
    fi
    if ! command -v brew >/dev/null 2>&1; then
        log "brew not found — install Homebrew to use a local Postgres mirror"
        return 1
    fi
    # Try versions in order: 16, 15, 14, and unversioned
    local pg_prefix=""
    for pg_ver in postgresql@16 postgresql@15 postgresql@14 postgresql; do
        local candidate
        candidate="$(brew --prefix "$pg_ver" 2>/dev/null || true)"
        if [ -n "$candidate" ] && [ -d "$candidate/bin" ]; then
            pg_prefix="$candidate"
            log "found $pg_ver at $pg_prefix"
            break
        fi
    done
    if [ -z "$pg_prefix" ]; then
        log "postgresql not installed — run: brew install postgresql@16"
        return 1
    fi
    # Ensure the server is running (idempotent).
    if ! "$pg_prefix/bin/pg_isready" -q -h localhost 2>/dev/null; then
        log "starting brew service postgresql@16"
        brew services start postgresql@16 >>"$LAUNCH_LOG" 2>&1 || true
        # Wait up to 10s for it to come up.
        for _ in $(seq 1 20); do
            if "$pg_prefix/bin/pg_isready" -q -h localhost 2>/dev/null; then break; fi
            sleep 0.5
        done
    fi
    if ! "$pg_prefix/bin/pg_isready" -q -h localhost 2>/dev/null; then
        log "local Postgres failed to start — falling back to Neon"
        return 1
    fi
    # Ensure the `strava` database exists (createdb is a no-op if it already does).
    if ! "$pg_prefix/bin/psql" -h localhost -lqt 2>/dev/null | cut -d '|' -f 1 | grep -qw strava; then
        log "creating local database 'strava'"
        "$pg_prefix/bin/createdb" -h localhost strava >>"$LAUNCH_LOG" 2>&1 || true
    fi
    export LOCAL_DATABASE_URL="postgresql://$(whoami)@localhost:5432/strava"
    log "auto-provisioned LOCAL_DATABASE_URL=$LOCAL_DATABASE_URL"
    return 0
}

local_db_ready() {
    # Exit 0 only if the local Postgres is reachable AND already has the
    # `activities` table. The Postgres schema is created solely by the mirror
    # (init_db only pings; migrations merely ALTER activities), so flipping the
    # backend onto a local DB without that table makes get_activity_count()
    # crash the whole backend at startup. When not ready we stay on Neon.
    python3 - "$LOCAL_DATABASE_URL" <<'PY'
import sys, ssl
from urllib.parse import urlparse, parse_qs
import pg8000.dbapi
url = sys.argv[1]
p = urlparse(url)
params = dict(host=p.hostname, port=p.port or 5432, database=p.path.lstrip('/'),
              user=p.username, password=p.password, timeout=5)
qs = parse_qs(p.query)
if qs.get('sslmode', [''])[0] in ('require', 'verify-ca', 'verify-full'):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    params['ssl_context'] = ctx
try:
    conn = pg8000.dbapi.connect(**params)
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.activities')")
    ready = cur.fetchone()[0] is not None
    conn.close()
    sys.exit(0 if ready else 1)
except Exception as e:
    print(f"[start] local readiness check failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

use_local_primary() {
    ensure_local_postgres || true
    if [ -z "${LOCAL_DATABASE_URL:-}" ]; then
        log "no LOCAL_DATABASE_URL — backend will use Neon directly (primary)"
        return 0
    fi
    local neon_url="${DATABASE_URL_NEON:-}"
    if [ -z "$neon_url" ] && [ -n "${DATABASE_URL:-}" ] && [ "$DATABASE_URL" != "$LOCAL_DATABASE_URL" ]; then
        # Legacy .env format: DATABASE_URL still contains Neon. Treat it as the
        # secondary replica once the local primary is ready.
        neon_url="$DATABASE_URL"
    fi
    if local_db_ready; then
        log "local Postgres schema already ready — skipping Neon bootstrap check"
    elif [ -n "$neon_url" ] && [ "$neon_url" != "$LOCAL_DATABASE_URL" ]; then
        # One-time bootstrap. --if-empty makes the mirror a no-op when local
        # already has activities, so a live local DB is never clobbered.
        log "checking whether local Postgres needs a one-time Neon bootstrap"
        DATABASE_URL_NEON="$neon_url" LOCAL_DATABASE_URL="$LOCAL_DATABASE_URL" \
            python3 "$PROJECT_DIR/scripts/mirror_neon_to_local.py" --if-empty \
            >>"$LAUNCH_LOG" 2>&1 \
            || log "bootstrap mirror failed (see $LAUNCH_LOG)"
    fi
    # Only run on local if it is actually usable. The Postgres schema exists only
    # once the mirror has created it; pointing the backend at an empty/unreachable
    # local DB would crash it on the first query (every /api/* then 500s via the
    # dev proxy). If local isn't ready, stay on Neon as the primary.
    if ! local_db_ready; then
        if [ -n "$neon_url" ] && [ "$neon_url" != "$LOCAL_DATABASE_URL" ]; then
            export DATABASE_URL="$neon_url"
            log "local Postgres not ready (unreachable or no 'activities' table) — staying on Neon as primary"
        else
            log "local Postgres not ready and no Neon fallback configured — backend may report DB readiness errors"
        fi
        return 0
    fi
    if [ -n "$neon_url" ] && [ "$neon_url" != "$LOCAL_DATABASE_URL" ]; then
        # Keep Neon as the best-effort secondary replica target for dual-write.
        export DATABASE_URL_NEON="$neon_url"
    fi
    export DATABASE_URL="$LOCAL_DATABASE_URL"
    log "self-hosted: DATABASE_URL -> local Postgres (primary)${neon_url:+; Neon kept as secondary}"
    dump_local_postgres_sql
    return 0
}

use_local_primary

# Rebuild dist/ if any source file is newer than the bundle uvicorn (8080) is
# about to serve. Without this, users who hit :8080 instead of Vite (:5173)
# stay locked on a stale build and never see new fixes.
ensure_dist_fresh() {
    local marker="$PROJECT_DIR/dist/index.html"
    local need_build=0
    if [ ! -f "$marker" ]; then
        log "dist/ missing — building"
        need_build=1
    elif [ -n "$(find "$PROJECT_DIR/src" "$PROJECT_DIR/public" "$PROJECT_DIR/index.html" "$PROJECT_DIR/vite.config.js" -newer "$marker" -print -quit 2>/dev/null)" ]; then
        log "dist/ stale — rebuilding"
        need_build=1
    fi
    if [ "$need_build" -eq 1 ]; then
        if [ ! -d "$PROJECT_DIR/node_modules" ]; then
            log "node_modules missing — npm install (first launch)"
            (cd "$PROJECT_DIR" && npm install) >>"$LAUNCH_LOG" 2>&1
        fi
        (cd "$PROJECT_DIR" && npm run build) >>"$LAUNCH_LOG" 2>&1
        if [ $? -ne 0 ]; then
            log "build FAILED — :8080 will keep serving the previous dist/ (see $LAUNCH_LOG)"
        else
            log "dist/ rebuilt"
        fi
    fi
}

ensure_dist_fresh

# SQLite mode: if SQLITE_PATH is set or LOCAL_DATABASE_URL is unset and DATABASE_URL
# is also unset, fall back to a local SQLite file so the server starts without any
# remote DB credentials.
if [ -z "${DATABASE_URL:-}" ] && [ -z "${LOCAL_DATABASE_URL:-}" ] && [ -z "${SQLITE_PATH:-}" ]; then
    SQLITE_PATH="$PROJECT_DIR/.runtime/strava.db"
    export SQLITE_PATH
    log "no DATABASE_URL — using SQLite: $SQLITE_PATH"
fi

port_in_use() {
    lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

listener_pid() {
    lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n 1
}

listener_command() {
    local pid="$1"
    ps -p "$pid" -o command= 2>/dev/null | sed 's/^[[:space:]]*//'
}

stop_listener() {
    local port="$1"
    local name="$2"
    local pid
    pid="$(listener_pid "$port")"
    if [ -z "$pid" ]; then
        return 0
    fi
    local cmd
    cmd="$(listener_command "$pid")"
    log "$name unhealthy on :$port — stopping pid $pid (${cmd:-unknown})"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        if ! port_in_use "$port"; then
            return 0
        fi
        sleep 0.5
    done
    log "$name pid $pid ignored SIGTERM — forcing stop"
    kill -9 "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        if ! port_in_use "$port"; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

wait_for_http_ok() {
    local url="$1"
    local max_tries="${2:-60}"
    for _ in $(seq 1 "$max_tries"); do
        if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

backend_healthy() {
    wait_for_http_ok "http://127.0.0.1:8080/api/health" 2
}

frontend_healthy() {
    wait_for_http_ok "http://127.0.0.1:5173/" 2
}

wait_for_port() {
    local port="$1"
    local max_tries="${2:-60}"
    for _ in $(seq 1 "$max_tries"); do
        if port_in_use "$port"; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

start_backend() {
    if port_in_use 8080; then
        local pid
        pid="$(listener_pid 8080)"
        if backend_healthy; then
            log "backend already healthy on :8080${pid:+ (pid $pid)}"
            return 0
        fi
        log "backend listener on :8080 failed healthcheck${pid:+ (pid $pid)}"
        stop_listener 8080 "backend" || log "backend listener on :8080 did not stop cleanly"
    fi
    log "starting backend ($PYBIN -m uvicorn server:app :8080)"
    nohup "$PYBIN" -m uvicorn server:app --host 127.0.0.1 --port 8080 \
        >"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID"
    disown 2>/dev/null || true
}

start_frontend() {
    if port_in_use 5173; then
        local pid
        pid="$(listener_pid 5173)"
        if frontend_healthy; then
            log "frontend already healthy on :5173${pid:+ (pid $pid)}"
            return 0
        fi
        log "frontend listener on :5173 failed healthcheck${pid:+ (pid $pid)}"
        stop_listener 5173 "frontend" || log "frontend listener on :5173 did not stop cleanly"
    fi
    if [ ! -d "$PROJECT_DIR/node_modules" ]; then
        log "node_modules missing — running npm install (first launch)"
        (cd "$PROJECT_DIR" && npm install) >>"$LAUNCH_LOG" 2>&1
    fi
    log "starting frontend (vite :5173)"
    nohup npm run dev -- --host 127.0.0.1 --port 5173 --strictPort \
        >"$FRONTEND_LOG" 2>&1 &
    echo $! >"$FRONTEND_PID"
    disown 2>/dev/null || true
}

start_incremental_sync() {
    if [ -z "${DATABASE_URL_NEON:-}" ] || [ -z "${LOCAL_DATABASE_URL:-}" ]; then
        return 0
    fi
    if [ -f "$INCREMENTAL_SYNC_PID" ]; then
        local pid
        pid="$(cat "$INCREMENTAL_SYNC_PID" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log "incremental Neon/local sync already running (pid $pid)"
            return 0
        fi
    fi
    log "starting incremental Neon/local sync in background"
    nohup env DATABASE_URL_NEON="$DATABASE_URL_NEON" LOCAL_DATABASE_URL="$LOCAL_DATABASE_URL" \
        python3 "$PROJECT_DIR/scripts/sync_neon_local.py" \
        >>"$LAUNCH_LOG" 2>&1 &
    echo $! >"$INCREMENTAL_SYNC_PID"
    disown 2>/dev/null || true
}

start_backend
start_frontend
start_incremental_sync

if wait_for_port 8080 60 && backend_healthy; then
    log "backend ready"
else
    log "backend did not become healthy on :8080 in 30s (see $BACKEND_LOG)"
fi

if wait_for_port 5173 60 && frontend_healthy; then
    log "frontend ready"
else
    log "frontend did not become healthy on :5173 in 30s (see $FRONTEND_LOG)"
fi

if [ "${1:-}" != "--no-open" ]; then
    open "http://localhost:5173"
    log "opened http://localhost:5173"
fi

log "done — processes detached; safe to close/shutdown Mac"
exit 0

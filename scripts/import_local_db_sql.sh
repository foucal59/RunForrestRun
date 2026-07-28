#!/bin/bash

set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
cd "$PROJECT_DIR"

load_env_file() {
    local env_path="$1"
    if [ ! -f "$env_path" ]; then
        return 0
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue
        [[ "$line" =~ = ]] || continue
        export "$line"
    done < "$env_path"
}

load_env_file "$PROJECT_DIR/.env"

LOCAL_DATABASE_URL="${LOCAL_DATABASE_URL:-}"
LOCAL_DATABASE_SQL_PATH="${1:-${LOCAL_DATABASE_SQL_PATH:-$PROJECT_DIR/.runtime/local-db/bdd_runs.sql}}"

if [ -z "$LOCAL_DATABASE_URL" ]; then
    echo "[sql-import] LOCAL_DATABASE_URL is required" >&2
    exit 1
fi

if [ ! -f "$LOCAL_DATABASE_SQL_PATH" ]; then
    echo "[sql-import] dump not found: $LOCAL_DATABASE_SQL_PATH" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    for pg_ver in postgresql@16 postgresql@15 postgresql@14 postgresql; do
        candidate="$(brew --prefix "$pg_ver" 2>/dev/null || true)"
        if [ -n "$candidate" ] && [ -x "$candidate/bin/psql" ]; then
            export PATH="$candidate/bin:$PATH"
            break
        fi
    done
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "[sql-import] psql not found" >&2
    exit 1
fi

psql "$LOCAL_DATABASE_URL" -f "$LOCAL_DATABASE_SQL_PATH" >/dev/null
echo "[sql-import] dump restored from $LOCAL_DATABASE_SQL_PATH" >&2

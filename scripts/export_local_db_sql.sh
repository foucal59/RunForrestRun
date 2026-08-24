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
LOCAL_DATABASE_SQL_PATH="${LOCAL_DATABASE_SQL_PATH:-$PROJECT_DIR/.runtime/local-db/bdd_runs.sql}"

if [ -z "$LOCAL_DATABASE_URL" ]; then
    echo "[sql-export] LOCAL_DATABASE_URL is required" >&2
    exit 1
fi

DUMP_DIR="$(dirname "$LOCAL_DATABASE_SQL_PATH")"
mkdir -p "$DUMP_DIR"

if ! command -v pg_dump >/dev/null 2>&1; then
    # Make Homebrew reachable even from a minimal (non-login) PATH, e.g. when
    # invoked via `do shell script` / launchd.
    for brew_bin in /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$brew_bin/brew" ]; then
            export PATH="$brew_bin:$PATH"
            break
        fi
    done
    for pg_ver in postgresql@16 postgresql@15 postgresql@14 postgresql; do
        candidate="$(brew --prefix "$pg_ver" 2>/dev/null || true)"
        if [ -n "$candidate" ] && [ -x "$candidate/bin/pg_dump" ]; then
            export PATH="$candidate/bin:$PATH"
            break
        fi
    done
    # Direct fallback if brew prefix lookup failed but the binary exists.
    if ! command -v pg_dump >/dev/null 2>&1; then
        for direct in /opt/homebrew/opt/postgresql@16/bin /opt/homebrew/opt/postgresql@15/bin \
                       /opt/homebrew/opt/postgresql@14/bin /usr/local/opt/postgresql@16/bin; do
            if [ -x "$direct/pg_dump" ]; then
                export PATH="$direct:$PATH"
                break
            fi
        done
    fi
fi

if ! command -v pg_dump >/dev/null 2>&1; then
    echo "[sql-export] pg_dump not found" >&2
    exit 1
fi

TEMP_DUMP_PATH="$(mktemp "$DUMP_DIR/.sql-export.XXXXXX")"
cleanup_temp_dump() {
    if [ -n "${TEMP_DUMP_PATH:-}" ] && [ -f "$TEMP_DUMP_PATH" ]; then
        /bin/rm -f -- "$TEMP_DUMP_PATH"
    fi
}
trap cleanup_temp_dump EXIT

# Le dump canonique n'est remplace qu'apres une execution complete. Une panne
# de pg_dump laisse ainsi le dernier snapshot valide intact pour le mode degrade
# du coach, au lieu de lui donner un fichier tronque avec un mtime trompeur.
pg_dump \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --encoding=UTF8 \
    "$LOCAL_DATABASE_URL" > "$TEMP_DUMP_PATH"

if [ ! -s "$TEMP_DUMP_PATH" ]; then
    echo "[sql-export] pg_dump produced an empty dump; previous dump preserved" >&2
    exit 1
fi

/bin/mv -f -- "$TEMP_DUMP_PATH" "$LOCAL_DATABASE_SQL_PATH"
TEMP_DUMP_PATH=""

echo "[sql-export] dump written to $LOCAL_DATABASE_SQL_PATH" >&2

#!/bin/bash
# Daily plan update: incrementally converge DBs, export local, analyze runs

set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Load .env
if [ -f "$PROJECT_DIR/.env" ]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue
        [[ "$line" =~ = ]] || continue
        export "$line"
    done < "$PROJECT_DIR/.env"
fi

export LOCAL_DATABASE_URL="${LOCAL_DATABASE_URL:-postgresql://${USER}@localhost:5432/strava}"

echo "[daily-plan] Starting incremental sync + local export + analysis at $(date)"

# Exchange only missing/incomplete runs. Complete runs and their streams are
# represented by lightweight counts and are never downloaded again.
echo "[daily-plan] Converging Neon and local PostgreSQL incrementally..."
if [ -n "${DATABASE_URL:-}" ]; then
    DATABASE_URL_NEON="$DATABASE_URL" \
        python3 "$PROJECT_DIR/scripts/sync_neon_local.py" || true
else
    echo "[daily-plan] WARNING: DATABASE_URL not set, skipping incremental sync"
fi

# 1. Export local to SQL
echo "[daily-plan] Exporting local DB to SQL..."
bash "$PROJECT_DIR/scripts/export_local_db_sql.sh" || true

# 2. Analyze and print day's session
echo "[daily-plan] Analyzing runs and computing day's session..."
SQL_FILE="$PROJECT_DIR/.runtime/local-db/bdd_runs.sql" python3 << 'ANALYSIS_EOF'
import os
import sys
import re
from datetime import datetime, timedelta

sql_file = os.environ["SQL_FILE"]
three_days_ago = (datetime.now() - timedelta(days=3)).date()
today = datetime.now().date()

# Parse COPY activities
columns = []
data_start_line = None

with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
    for i, line in enumerate(f, 1):
        if 'COPY public.activities' in line:
            cols_match = re.search(r'\((.*?)\)', line)
            if cols_match:
                columns = [c.strip() for c in cols_match.group(1).split(',')]
            data_start_line = i
            break

if not data_start_line:
    print("[daily-plan] ERROR: Could not parse SQL", file=sys.stderr)
    sys.exit(1)

# Read runs
runs = []
with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
    for i, line in enumerate(f, 1):
        if i <= data_start_line:
            continue
        if line.strip() == '\\.':
            break

        fields = line.rstrip('\n').split('\t')
        if len(fields) < len(columns):
            continue

        record = dict(zip(columns, fields))

        if record.get('type') == 'Run':
            start_str = record.get('start_date_local')
            if start_str and start_str != '\\N':
                try:
                    start_date = datetime.fromisoformat(start_str.split('+')[0]).date()
                    if start_date >= three_days_ago:
                        runs.append(record)
                except:
                    pass

print(f"\n[daily-plan] {today} — Runs des 3 derniers jours:")
print("Date          | km    | temps    | allure | fc  | fcmax")
print("-" * 70)

for run in sorted(runs, key=lambda x: x.get('start_date_local', ''), reverse=True):
    start = run.get('start_date_local', 'N/A')[:16]
    try:
        distance = float(run.get('distance', 0) or 0)
        moving_time = int(run.get('moving_time', 0) or 0)
        avg_hr = run.get('average_heartrate', '\\N')
        max_hr = run.get('max_heartrate', '\\N')

        km = distance / 1000
        hours = moving_time // 3600
        mins = (moving_time % 3600) // 60
        secs = moving_time % 60
        temps = f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d}"

        if km > 0:
            pace = moving_time / km
            allure_m = int(pace // 60)
            allure_s = int(pace % 60)
            allure = f"{allure_m}:{allure_s:02d}"
        else:
            allure = "N/A"

        fc_str = int(float(avg_hr)) if avg_hr != '\\N' else 0
        fcmax_str = int(float(max_hr)) if max_hr != '\\N' else 0

        print(f"{start} | {km:5.1f} | {temps} | {allure:5} | {fc_str:3} | {fcmax_str:5}")
    except Exception as e:
        print(f"[daily-plan] Parse error: {e}", file=sys.stderr)

print("\n[daily-plan] ✓ Analysis complete — now check your daily session in the dashboard")
ANALYSIS_EOF

echo "[daily-plan] Done at $(date)"

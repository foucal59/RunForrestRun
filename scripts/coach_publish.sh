#!/bin/bash
# Pipeline quotidien du coach running.
#  1. Régénère le dump SQL local (base LOCALE uniquement, aucun accès réseau).
#  2. Génère le journal coach dans .runtime/ uniquement.
#  3. Copie le markdown + le JSON dans iCloud Drive (visible dans l'app Fichiers de l'iPhone).
#
# Ce script ne committe et ne pousse jamais le snapshot, qui contient des
# données personnelles.
#
# Usage : scripts/coach_publish.sh
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
cd "$PROJECT_DIR"

if [ "$#" -ne 0 ]; then
  echo "[coach-publish] aucun argument attendu ; les snapshots ne sont jamais pousses" >&2
  exit 2
fi

# Dossier de destination du journal (iCloud Drive par defaut, pour le consulter
# depuis l'app Fichiers de l'iPhone). Surcharge possible via COACH_PUBLISH_DIR.
ICLOUD_DIR="${COACH_PUBLISH_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Coach}"
JOURNAL_MD="$PROJECT_DIR/.runtime/journal-coach.md"
JOURNAL_JSON="$PROJECT_DIR/.runtime/coach-journal.json"

echo "[coach-publish] 1/3 régénération du dump local…"
/bin/bash "$SELF_DIR/export_local_db_sql.sh"

echo "[coach-publish] 2/3 génération du journal (md + json)…"
python3 "$SELF_DIR/coach_journal.py" --out "$JOURNAL_MD" --json "$JOURNAL_JSON"

echo "[coach-publish] 3/3 copie vers iCloud Drive…"
mkdir -p "$ICLOUD_DIR"
cp -f "$JOURNAL_MD" "$ICLOUD_DIR/journal-coach.md"
cp -f "$JOURNAL_JSON" "$ICLOUD_DIR/coach-journal.json"
echo "[coach-publish]   → $ICLOUD_DIR"

echo "[coach-publish] terminé."

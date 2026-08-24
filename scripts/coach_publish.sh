#!/bin/bash
# Pipeline quotidien du coach running.
#  1. Régénère le dump SQL local (base LOCALE uniquement, aucun accès réseau).
#  2. Génère le journal coach : markdown (.runtime/journal-coach.md) + snapshot JSON (public/coach-journal.json).
#  3. Copie le markdown + le JSON dans iCloud Drive (visible dans l'app Fichiers de l'iPhone).
#  4. Avec --push : commit du seul snapshot JSON sur la branche courante + push (→ redeploy Vercel,
#     le serveur MCP distant sert alors la donnée fraîche). Sans --push : rien n'est committé.
#
# Usage : scripts/coach_publish.sh [--push]
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SELF_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

# Dossier de destination du snapshot coach. Configurable : le journal contient
# des donnees personnelles et n'a pas a partir dans un chemin ecrit en dur.
ICLOUD_DIR="${COACH_PUBLISH_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/CoachJournal}"
JOURNAL_MD="$PROJECT_DIR/.runtime/journal-coach.md"
JOURNAL_JSON="$PROJECT_DIR/public/coach-journal.json"

echo "[coach-publish] 1/4 régénération du dump local…"
/bin/bash "$SELF_DIR/export_local_db_sql.sh"

echo "[coach-publish] 2/4 génération du journal (md + json)…"
python3 "$SELF_DIR/coach_journal.py" --out "$JOURNAL_MD" --json "$JOURNAL_JSON"

echo "[coach-publish] 3/4 copie vers iCloud Drive…"
mkdir -p "$ICLOUD_DIR"
cp -f "$JOURNAL_MD" "$ICLOUD_DIR/journal-coach.md"
cp -f "$JOURNAL_JSON" "$ICLOUD_DIR/coach-journal.json"
echo "[coach-publish]   → $ICLOUD_DIR"

if [ "$PUSH" -eq 1 ]; then
  echo "[coach-publish] 4/4 commit + push du snapshot…"
  BR="$(git rev-parse --abbrev-ref HEAD)"
  git add public/coach-journal.json
  if git diff --cached --quiet; then
    echo "[coach-publish]   aucun changement à committer"
  else
    git commit -m "chore(coach): snapshot journal $(date +%F)"
    git push origin "$BR"
    echo "[coach-publish]   poussé sur $BR"
  fi
else
  echo "[coach-publish] 4/4 push ignoré (--push non fourni)"
fi

echo "[coach-publish] terminé."

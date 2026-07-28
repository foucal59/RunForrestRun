# Instructions projet — Garmin Running Dashboard

## Après chaque modification

1. **Rebuild frontend si `src/` touché**
   ```bash
   npm run build
   ```

2. **Redémarrer les deux serveurs locaux**
   ```bash
   scripts/stop.sh && scripts/start.sh --no-open
   ```
   - Backend uvicorn → `http://127.0.0.1:8080`
   - Frontend vite → `http://127.0.0.1:5173`
   - Vérifier `.runtime/backend.log` / `.runtime/frontend.log` si besoin.

## Stack

- **Backend** : FastAPI + uvicorn (`server.py`), PostgreSQL via `pg8000`
  (`database_pg.py`, wrapper `db.py`). Repli SQLite pour le dev (`db_sqlite.py`).
- **Frontend** : React + Vite (`src/`), build vers `dist/`.
- **Serverless** : `api/app.py` pour un déploiement Vercel.
- **Service Worker** : `public/sw.js` — cache-first pour les assets hashés,
  network-first pour l'HTML et `/api/data/*`, skip total pour `/api/auth` et
  `/api/streams`.

## Flux des données

La base PostgreSQL est la source de vérité. Garmin Connect n'est interrogé qu'à
l'ouverture, via un *freshness-check*, pour combler le delta. Toute écriture va
sur la base primaire (`DATABASE_URL`) puis est répliquée best-effort vers la
secondaire — voir la section « Base primaire / replica secondaire » du README.

## Données personnelles

Ces fichiers contiennent des données d'entraînement identifiantes et sont
**gitignorés** — ne les committez pas :

- `coach_profile.json` — records, FC max/facile, objectif (modèle :
  `coach_profile.example.json`)
- `.runtime/coach-journal.json` — snapshot généré par `scripts/coach_publish.sh`
- `training-plan.md` — plan perso mis à jour par `scripts/weather_for_run.py
  --update-plan` (chemin configurable via `TRAINING_PLAN_FILE`)

Le calendrier d'entraînement de `daily_training_plan.py` (`_build_calendar`) est
un **exemple** de bloc marathon 16 semaines : adaptez dates, allures et séances
à votre propre objectif. `PLAN_RACE_NAME`, `PLAN_START_DATE`,
`PLAN_RACE_DATE` et `PLAN_DESCRIPTION` permettent de changer le calendrier et
les libellés affichés sans toucher au code.

## Secrets

Aucun secret n'est versionné. Copiez `.env.example` vers `.env` et renseignez
`SESSION_SECRET`, `DATABASE_URL`, et optionnellement `DATABASE_URL_NEON` /
`LOCAL_DATABASE_URL`. Les identifiants Garmin ne sont jamais stockés en clair :
seuls des tokens de session sont conservés en base.

## Debug logging

Ajouter systématiquement des `console.log` / `print(..., file=sys.stderr)` lors
des fixes pour tracer le comportement réel vs attendu.

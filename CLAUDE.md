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

## Tests

```bash
python -m pytest tests/ -q
```

`tests/conftest.py` fige un profil de coureur déterministe avec `setdefault` :
la suite doit passer sur **n'importe quel** profil. C'est la garantie que les
tests décrivent le générateur de plan et non un plan particulier — vérifiez-le
avant de toucher au plan :

```bash
PLAN_WEEKS=12 PLAN_LONG_RUN_WEEKDAY=6 RUNNER_GOAL_TIME=3:45:00 python -m pytest tests/ -q
```

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

## Profil du coureur — règle structurante

**Aucune valeur propre à une personne ne doit être écrite en dur.** Allures,
FC max, date de course, volumes, nom de la course : tout vient de
`runner_profile.py`, résolu dans cet ordre de priorité :

1. variables d'environnement (`RUNNER_*`, `PLAN_*`) ;
2. `runner_profile.json` (modèle : `runner_profile.example.json`) ;
3. `.runtime/runner-profile.json` — snapshot écrit par `runner_profile_sync.py`
   après chaque *freshness-check*, depuis `activity_best_efforts` et la FC max
   observée sur 90 jours. **C'est ce qui rend le plan juste sans aucune saisie** ;
4. replis neutres.

Les **huit** fourchettes d'allure se déduisent d'un seul nombre, l'objectif
marathon (`derive_paces`, Riegel + écarts d'entraînement usuels). N'ajoutez
jamais une allure en dur : ajoutez-la à `derive_paces`.

`daily_training_plan._build_calendar()` **génère** le calendrier depuis le
profil (`PLAN_SHAPE` porte le découpage en phases, les décharges et les rampes).
Ne réintroduisez pas de date absolue : `_phase_for`, `PLAN_WEEK_ONE`,
`TAPER_START` et `RACE_DAY` en dérivent tous.

## Données personnelles

Ces fichiers contiennent des données d'entraînement identifiantes et sont
**gitignorés** — ne les committez pas :

- `runner_profile.json` — objectif, records, FC max, cadrage du plan
- `.runtime/runner-profile.json` — snapshot des records lus dans la base
- `.runtime/coach-journal.json` — snapshot généré par `scripts/coach_publish.sh`
- `training-plan.md` — plan perso mis à jour par `scripts/weather_for_run.py
  --update-plan` (chemin configurable via `TRAINING_PLAN_FILE`)
- `public/training-plan.pdf` — PDF généré par `scripts/export_plan_pdf.py`

## Secrets

Aucun secret n'est versionné. Copiez `.env.example` vers `.env` et renseignez
`SESSION_SECRET`, `DATABASE_URL`, et optionnellement `DATABASE_URL_NEON` /
`LOCAL_DATABASE_URL`. Les identifiants Garmin ne sont jamais stockés en clair :
seuls des tokens de session sont conservés en base.

## Debug logging

Ajouter systématiquement des `console.log` / `print(..., file=sys.stderr)` lors
des fixes pour tracer le comportement réel vs attendu.

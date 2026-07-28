# Garmin Running Dashboard

Dashboard self-hosted pour visualiser et analyser des donnees de course Garmin
Connect. Le depot contient uniquement le code et un plan d'entrainement
d'exemple : les identifiants, activites, traces GPS, donnees de sante et journaux
generes restent dans l'infrastructure de chaque utilisateur.

La base de donnees (PostgreSQL) est la **source de verite**. Garmin Connect n'est
interroge qu'a l'ouverture, via un *freshness-check*, pour combler le delta
(nouvelles activites, VO2max, training status, equipement).

> [!IMPORTANT]
> Les allures, zones cardiaques, volumes et conseils du plan fourni sont des
> exemples techniques. Ils doivent etre adaptes individuellement et ne
> remplacent pas l'avis d'un professionnel de sante ou d'un entraineur.

## Fonctionnalites

- **Cockpit** : vue d'ensemble des 30 derniers jours
- **Volume** : kilometrage glissant 7j, 90j, mensuel, annuel
- **Performance** : records personnels 5K, 10K, semi, marathon + projections Riegel
- **Progression** : charge, zones FC, VO2max et analyses avancees
- **Training Load** : CTL / ATL / TSB avec zones d'interpretation
- **Zones FC** : repartition du temps dans les zones cardio (FC max contextuelle)
- **VO2max & Training Status** : repris nativement depuis Garmin
- **Detail d'un run** : carte GPS, courbes allure/FC, meilleurs efforts (streams hydrates depuis Garmin)

## Architecture

- **Frontend** : React 18 + Vite 5 + Tailwind CSS 3 + Recharts (build → `dist/`), PWA installable
- **Backend** : FastAPI + uvicorn (`server.py`, self-hosted) **ou** fonctions Python serverless Vercel (`api/app.py`)
- **Auth** : connexion Garmin Connect (e-mail + mot de passe, MFA gere) ; cookie de session HttpOnly signe HMAC-SHA256. Pas d'OAuth Strava, aucun mot de passe stocke en local.
- **Base** : PostgreSQL via `pg8000` (`database_pg.py`). Repli SQLite local possible pour le dev (`SQLITE_PATH`).
- **Service Worker** (`public/sw.js`) : cache-first pour les assets hashes, network-first pour l'HTML et `/api/data/*`, jamais de cache pour `/api/auth` et `/api/streams`.

## Prerequis

- Python 3.11+
- Node.js 20.19+ ou 22.12+
- PostgreSQL 16+ pour le mode complet, ou SQLite pour le developpement
- Un compte Garmin Connect

### Base primaire / replica secondaire

Toute ecriture va sur la base **primaire** puis est repliquee *best-effort* vers
une base **secondaire** (jamais bloquant ; un disjoncteur saute si la secondaire
est injoignable) :

| Deploiement | Base primaire (`DATABASE_URL`) | Replica secondaire |
|---|---|---|
| **self-hosted** | PostgreSQL local | Neon (`DATABASE_URL_NEON`, pose automatiquement par `start.sh`) |
| **Vercel** | Neon | PostgreSQL local (`LOCAL_DATABASE_URL`) *si joignable depuis Vercel* |

> Sur Vercel, `localhost` designe le conteneur Vercel, pas ta machine : pour
> repliquer vers ton Postgres local, `LOCAL_DATABASE_URL` doit pointer vers une
> adresse reellement joignable depuis Vercel (sinon la replication est ignoree).

## Deploiement self-hosted (Mac/Linux)

1. Copie `.env.example` → `.env` et renseigne au minimum `SESSION_SECRET`,
   `LOCAL_DATABASE_URL` (Postgres local primaire) et `DATABASE_URL_NEON` (Neon
   secondaire). L'ancien format `DATABASE_URL=Neon` reste supporte : `start.sh`
   deplace alors cette URL dans `DATABASE_URL_NEON` puis force `DATABASE_URL`
   vers la base locale avant de lancer le backend.
2. Lance les serveurs :
   ```bash
   scripts/start.sh             # backend :8080 + frontend :5173
   scripts/start.sh --no-open   # sans ouvrir le navigateur
   ```
   `start.sh` met toujours la base locale en primaire, garde Neon en secondaire, et ne
   recopie Neon → local qu'au **premier** demarrage si la base locale est vide
   (jamais d'ecrasement d'une base locale deja utilisee).
   Aux demarrages suivants, `scripts/sync_neon_local.py` compare des manifestes
   legers et ne transfere que les runs ou tables enfants absents/incomplets.
   Quand les deux bases ont le run, ses details et les memes nombres de laps,
   splits, meilleurs efforts et streams, il recoit `sync_complete_at` dans les
   deux bases. Ce run `OK` disparait ensuite totalement des synchronisations.
   `sync_status` expose les quatre etats : `partial`, `ok_local`, `ok_neon`,
   puis `ok` quand la convergence est terminee.
   Une reconstruction complete manuelle exige
   `python scripts/mirror_neon_to_local.py --full`; sans cette option, l'outil
   refuse de recopier toute Neon sur une base locale deja remplie.
3. Ouvre http://localhost:5173 et connecte-toi avec ton compte Garmin Connect.
   Les tokens sont stockes dans `GARMIN_TOKEN_DIR` (defaut `.runtime/garminconnect/`).
   Une boucle de fraicheur en arriere-plan re-verifie Garmin toutes les `SYNC_INTERVAL` secondes.

## Deploiement Vercel

Definis ces variables dans les reglages du projet Vercel :

| Variable | Obligatoire | Description |
|---|:---:|---|
| `SESSION_SECRET` | oui | Cle de signature des cookies de session (32+ caracteres) |
| `DATABASE_URL` | oui | URL Neon PostgreSQL (base primaire) |
| `BASE_URL` | recommande | URL publique de l'app (flag `secure` des cookies + CORS) |
| `MCP_AUTH_TOKEN` | oui | Token Bearer pour proteger le MCP distant `/api/mcp` |

Les tokens Garmin sont lus depuis Neon (`sync_meta`) au runtime. Pousse-les une
fois depuis ta machine avec `scripts/garmin_push_neon.py`.

## Variables d'environnement

| Variable | Portee | Description |
|---|---|---|
| `SESSION_SECRET` | requis | Signe les cookies de session (HMAC-SHA256). `server.py` en genere un si absent. |
| `DATABASE_URL` | requis | Base **primaire** (Neon, ou Postgres local en self-hosted apres `start.sh`). |
| `DATABASE_URL_NEON` | self-hosted | Replica secondaire Neon. Pose automatiquement par `start.sh`. |
| `LOCAL_DATABASE_URL` | self-hosted / Vercel | Postgres local : primaire en self-hosted ; replica secondaire optionnel sur Vercel (si joignable). |
| `LOCAL_DATABASE_SQL_PATH` | option | Chemin d'un dump SQL local tenu a jour au demarrage self-hosted. |
| `GARMIN_TOKEN_DIR` | self-hosted | Dossier des tokens Garmin (defaut `.runtime/garminconnect/`). Vide → tokens dans Neon `sync_meta` (Vercel). |
| `BASE_URL` | Vercel | URL publique (cookies + origine CORS). |
| `ALLOWED_ORIGINS` | option | Origines CORS supplementaires, separees par des virgules. |
| `MCP_AUTH_TOKEN` | Vercel / self-hosted optionnel | Token Bearer attendu par `/api/mcp`. Obligatoire sur Vercel ; facultatif en local si usage prive. |
| `SESSION_COOKIE` | option | Nom du cookie de session (defaut `garmin_session`). |
| `SESSION_MAX_AGE_SECONDS` | option | Duree de vie de la session (defaut 2592000 = 30 j). |
| `SYNC_INTERVAL` | self-hosted | Intervalle (s) de la boucle de fraicheur en arriere-plan (defaut 900). |
| `DB_CONNECT_TIMEOUT` | option | Timeout (s) de connexion a la base primaire (defaut 5). |
| `DB_SECONDARY_COOLDOWN` | option | Fenetre (s) du disjoncteur de replication secondaire (defaut 120). |
| `SQLITE_PATH` | dev | Active le repli SQLite local (aucune base distante requise). |
| `PLAN_RACE_NAME` | option | Nom affiche pour la course (defaut `Marathon`). |
| `PLAN_START_DATE` | option | Debut du plan au format `YYYY-MM-DD`. Vide : prochain jeudi. |
| `PLAN_RACE_DATE` | option | Date de course au format `YYYY-MM-DD`. Vide : 108 jours apres le debut. |
| `PLAN_DESCRIPTION` | option | Description personnalisee affichee dans le cockpit. |
| `COACH_SNAPSHOT_PATH` | local | Chemin du snapshot prive (defaut `.runtime/coach-journal.json`). |
| `COACH_SNAPSHOT_URL` | distant | URL privee d'un snapshot distant ; ne jamais utiliser une URL statique publique. |
| `COACH_SNAPSHOT_TOKEN` | distant | Bearer token envoye a `COACH_SNAPSHOT_URL` si necessaire. |

## Source de verite & flux

A l'ouverture, le frontend declenche `/api/data/freshness-check` :
`garmin_freshness.check_and_populate()` lit la derniere activite connue dans la
base **primaire**, demande a Garmin le delta, ecrit les nouveautes (activites,
splits, streams, VO2max, training status, equipement) sur la primaire **puis**
replique vers la secondaire. Ne jamais reintroduire d'appel direct a Strava.

La synchronisation d'un run conserve aussi ses donnees Garmin specifiques :
temps dans les zones FC/puissance, charge et effets d'entrainement, puissance,
VO2max de l'activite, Body Battery, pas, dynamique de course (contact au sol,
foulee, oscillation/ratio vertical), temperature, laps enrichis et metriques
point par point. Le payload brut conserve est limite a l'activite et exclut les
champs de profil et de sommeil. Un backfill des runs Garmin deja lies peut etre
lance avec `python scripts/backfill_garmin_run_metrics.py`.

## MCP Coach

Le serveur MCP peut exposer un snapshot coach en lecture seule pour demander en
langage naturel l'entrainement a faire ou analyser les runs precedents.

Ce snapshot contient des donnees personnelles (records, FC et derniers runs).
Il est donc genere uniquement dans `.runtime/coach-journal.json`, dossier ignore
par Git. `scripts/coach_publish.sh` peut aussi en copier une version vers un
dossier local configure avec `COACH_PUBLISH_DIR`, mais ne committe et ne pousse
jamais le fichier.

Renseigne d'abord `coach_profile.json` a partir de
`coach_profile.example.json`. Pour un serveur distant, stocke le snapshot dans
un emplacement prive et configure `COACH_SNAPSHOT_URL` avec
`COACH_SNAPSHOT_TOKEN`. L'endpoint `/api/coach/journal` est protege par
`MCP_AUTH_TOKEN` sur Vercel et par un token ou une session valide en local.

Endpoints :

| Mode | URL MCP |
|---|---|
| Self-hosted backend | `http://127.0.0.1:8080/api/mcp/` |
| Script local autonome | `http://127.0.0.1:8765/mcp/` |
| Vercel | `https://<ton-projet>.vercel.app/api/mcp/` |

Outils MCP principaux :

| Outil | Usage |
|---|---|
| `entrainement_a_faire` | Seance du jour, projection, zones d'allure, regle d'ajustement |
| `analyse_runs_precedents` | Analyse des derniers runs, avec `nombre` de 1 a 7 |
| `poser_question_coach` | Routage simple d'une question naturelle vers le bon contexte |

Pour Vercel, definir `MCP_AUTH_TOKEN` puis configurer le connecteur Claude avec
l'en-tete :

```text
Authorization: Bearer <MCP_AUTH_TOKEN>
```

Pour lancer uniquement le MCP local autonome :

```bash
python3 scripts/coach_mcp_server.py
```

Le backend self-hosted monte deja le meme MCP sous `/api/mcp/` quand
`scripts/start.sh` lance `server.py`.

## Confidentialite et publication

Le depot est configure pour exclure notamment :

- `.env*` sauf `.env.example`, certificats, cles et archives ;
- bases SQLite/PostgreSQL locales et dossiers `.runtime/`, `data/`, `exports/` ;
- profils et journaux coach generes ;
- exports Garmin/FIT, GPX, TCX, KML, GeoJSON, CSV et TSV ;
- plans nominatifs et PDF locaux.

Avant chaque publication ou pull request :

1. Verifie que `git status` ne contient aucun export ou fichier genere.
2. Controle l'historique complet avec un scanner de secrets tel que Gitleaks.
3. Utilise une adresse GitHub `noreply` pour les commits.
4. Active GitHub Secret Scanning et Dependabot dans les reglages du depot.

Ne place jamais un snapshot coach dans `public/`, `dist/` ou une release GitHub :
ces emplacements peuvent etre accessibles sans authentification.

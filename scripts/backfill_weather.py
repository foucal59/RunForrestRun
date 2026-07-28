#!/usr/bin/env python3
"""Backfill de la météo Open-Meteo pour tous les runs GPS — base LOCALE seulement.

Objectif : que chaque run ait sa météo stockée, afin qu'elle finisse dans Neon
VIA LA RÉPLICATION DE L'APP (composant « weather », run_weather_updated_at), et
NON par une écriture directe dans Neon.

Contraintes de sécurité (voir demande utilisateur « tu ne dois pas modifier neon ») :
  - Ce script se connecte UNIQUEMENT à LOCAL_DATABASE_URL.
  - Il refuse de tourner si l'hôte résolu n'est pas localhost/127.0.0.1.
  - Il n'importe ni n'appelle jamais la couche de réplication (_replicate) ni
    DATABASE_URL_NEON. La propagation vers Neon est faite par la sync de l'app
    (scripts/sync_neon_local.py, lancée au démarrage du backend).

Le script pose weather_* + run_weather_updated_at + run_metrics_updated_at et
invalide la sync (sync_status='partial', sync_complete_at=NULL) pour que la
course réintègre le prochain passage de convergence.

Idempotent / reprenable : ne traite que les runs sans run_weather_updated_at
(sauf --force). Commit après chaque course.

Usage :
  python scripts/backfill_weather.py                 # tous les runs GPS manquants
  python scripts/backfill_weather.py --limit 5       # test sur 5 runs
  python scripts/backfill_weather.py --force          # recalcule même si déjà là
  python scripts/backfill_weather.py --sleep 0.5      # throttle inter-appels (s)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

import pg8000.dbapi

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

# Réutilise la logique de fetch météo existante (mêmes API/paramètres/heure).
from weather_for_run import fetch_weather, parse_start  # noqa: E402
# Source unique pour le SQL + l'ordre des colonnes météo, et la migration de
# schéma (n'agit que sur la connexion passée -> ici, locale uniquement).
from database_pg import (  # noqa: E402
    _ACTIVITY_WEATHER_UPDATE_SQL,
    _weather_row,
    ensure_run_metric_schema,
)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load_env() -> None:
    envp = REPO / ".env"
    if not envp.exists():
        return
    for line in envp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def connect_local():
    """Connexion à la base LOCALE uniquement, avec garde anti-Neon."""
    load_env()
    url = os.environ.get("LOCAL_DATABASE_URL") or ""
    if not url:
        raise SystemExit("[backfill-weather] LOCAL_DATABASE_URL absent du .env")
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(
            f"[backfill-weather] refus: hôte '{host}' non local. "
            "Ce script n'écrit QUE dans la base locale (jamais Neon)."
        )
    if "neon" in host:
        raise SystemExit("[backfill-weather] refus: l'URL locale pointe vers Neon.")
    params = {
        "host": p.hostname, "port": p.port or 5432,
        "database": (p.path.lstrip("/").split("?")[0]) or "postgres",
        "user": p.username,
        "password": unquote(p.password) if p.password else None,
        "timeout": int(os.environ.get("BACKFILL_DB_TIMEOUT", "30")),
    }
    qs = parse_qs(p.query)
    if qs.get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        params["ssl_context"] = ctx
    con = pg8000.dbapi.connect(**params)
    log(f"[backfill-weather] connecté LOCAL: {host}:{params['port']}/{params['database']}")
    return con


def select_runs(con, force: bool, limit: int | None) -> list[tuple]:
    cur = con.cursor()
    where = (
        "type = 'Run' AND start_lat IS NOT NULL AND start_lng IS NOT NULL"
    )
    if not force:
        where += " AND run_weather_updated_at IS NULL"
    sql = (
        "SELECT id, name, start_date_local, start_lat, start_lng, has_heartrate "
        f"FROM activities WHERE {where} ORDER BY start_date_local DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    return cur.fetchall()


def fetch_with_retry(lat: float, lon: float, date_iso: str, hour: int, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            return fetch_weather(lat, lon, date_iso, hour)
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 2 * (attempt + 1)
            log(f"[backfill-weather] fetch échoué ({attempt+1}/{tries}) "
                f"{type(e).__name__}: {e} — retry dans {wait}s")
            time.sleep(wait)
    raise last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--status", action="store_true",
                    help="affiche l'avancement (done/total) et quitte")
    args = ap.parse_args()

    con = connect_local()
    if args.status:
        cur = con.cursor()
        cur.execute(
            "SELECT count(*) FILTER (WHERE run_weather_updated_at IS NOT NULL), "
            "count(*) FROM activities "
            "WHERE type = 'Run' AND start_lat IS NOT NULL AND start_lng IS NOT NULL"
        )
        done, total = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM activities WHERE type='Run' "
            "AND sync_status <> 'ok'"
        )
        pending = cur.fetchone()[0]
        log(f"[backfill-weather] STATUS: météo {done}/{total} runs GPS "
            f"| runs en attente de sync = {pending}")
        con.close()
        return 0
    # Garantit les colonnes météo EN LOCAL avant d'écrire (idempotent, ne touche
    # que cette connexion locale). Les colonnes apparaîtront côté Neon via la
    # migration de l'app, pas ici.
    added = ensure_run_metric_schema(con)
    con.commit()
    if added:
        log(f"[backfill-weather] colonnes ajoutées en local: {added}")
    runs = select_runs(con, args.force, args.limit)
    total = len(runs)
    log(f"[backfill-weather] {total} run(s) à traiter (force={args.force})")

    done = 0
    skipped = 0
    failed = 0
    for i, (aid, name, sdl, lat, lng, has_hr) in enumerate(runs, 1):
        if lat is None or lng is None:
            skipped += 1
            continue
        try:
            date_iso, hour, _minute = parse_start(sdl)
            w = fetch_with_retry(float(lat), float(lng), date_iso, hour)
            w["source"] = f"open-meteo-{w.get('endpoint', 'forecast')}"
            version = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            params = _weather_row(int(aid), w, version)
            cur = con.cursor()
            cur.execute(_ACTIVITY_WEATHER_UPDATE_SQL, params)
            con.commit()
            done += 1
            if i % 25 == 0 or i == total:
                log(f"[backfill-weather] {i}/{total} — ok={done} skip={skipped} fail={failed} "
                    f"(dernier: {str(sdl)[:10]} {name} {w['temperature_2m']:.1f}°C)")
        except Exception as e:  # noqa: BLE001
            failed += 1
            try:
                con.rollback()
            except Exception:
                pass
            log(f"[backfill-weather] ÉCHEC id={aid} {str(sdl)[:10]} {name}: "
                f"{type(e).__name__}: {e}")
        time.sleep(args.sleep)

    log(f"[backfill-weather] TERMINÉ — traités={done} ignorés={skipped} échecs={failed} sur {total}")
    con.close()
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

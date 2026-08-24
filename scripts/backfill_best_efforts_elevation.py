#!/usr/bin/env python3
"""Recalcule les best efforts historiques avec le filtre de dénivelé.

Un 5K couru en perdant 385 m d'altitude n'est pas un record : c'est de la
gravité. Depuis `garmin_freshness._compute_best_efforts`, toute fenêtre dont la
perte nette dépasse `db.MAX_NET_DROP_PER_KM` est écartée au moment du calcul —
mais les lignes déjà en base ont été calculées sans ce garde-fou et n'ont pas de
`elevation_delta`. Ce script les repasse à la moulinette à partir des streams
déjà stockés (aucun appel Garmin) :

  - fenêtre trop descendante  → la ligne est supprimée
  - fenêtre valide            → chrono + elevation_delta réécrits
  - run sans stream d'altitude → laissé tel quel (signalé en fin de rapport)

L'écriture passe par `db.upsert_activity_details(replace_efforts=True)`, donc
base primaire puis réplication automatique vers la seconde base.

Usage :
  .venv/bin/python scripts/backfill_best_efforts_elevation.py --dry-run
  .venv/bin/python scripts/backfill_best_efforts_elevation.py
  .venv/bin/python scripts/backfill_best_efforts_elevation.py --activity-id 23872681994
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001 - best effort
        print(f"[backfill-efforts] dotenv indisponible ({exc})", file=sys.stderr)


def _streams_of(payload) -> dict:
    """get_streams() renvoie {'streams': ..., 'zones': ...} ou le dict brut."""
    if not payload:
        return {}
    if isinstance(payload, dict) and "streams" in payload:
        return payload.get("streams") or {}
    return payload


def _series(streams: dict, key: str) -> list:
    node = streams.get(key)
    if isinstance(node, dict):
        return node.get("data") or []
    return node or []


def _candidates(cur, activity_id: int | None) -> list[tuple[int, str, float]]:
    """Runs ayant au moins un best effort recalculable, du plus récent au plus ancien."""
    from database_pg import COMPUTED_EFFORT_NAMES

    placeholders = ",".join(["%s"] * len(COMPUTED_EFFORT_NAMES))
    params: list = list(COMPUTED_EFFORT_NAMES)
    where_activity = ""
    if activity_id:
        where_activity = "AND a.id = %s"
        params.append(activity_id)
    cur.execute(f"""
        SELECT DISTINCT a.id, a.start_date_local, a.distance
        FROM activities a
        JOIN activity_best_efforts be ON be.activity_id = a.id
        WHERE be.name IN ({placeholders})
          {where_activity}
        ORDER BY a.start_date_local DESC
    """, params)
    return [(int(r[0]), str(r[1]), float(r[2] or 0)) for r in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="n'écrit rien, affiche seulement les changements")
    parser.add_argument("--activity-id", type=int, default=0,
                        help="ne traiter qu'une activité")
    parser.add_argument("--limit", type=int, default=0,
                        help="s'arrêter après N activités (0 = toutes)")
    args = parser.parse_args()

    _load_env()
    import db
    import database_pg as dbpg
    from garmin_freshness import _compute_best_efforts

    conn = dbpg._safe_conn()
    cur = conn.cursor()
    # Le script peut tourner avant le premier démarrage serveur post-migration.
    dbpg._ensure_best_effort_elevation_column(cur)
    conn.commit()
    cur = conn.cursor()
    runs = _candidates(cur, args.activity_id or None)
    conn.rollback()
    if args.limit:
        runs = runs[: args.limit]
    print(f"[backfill-efforts] {len(runs)} run(s) à revoir "
          f"(seuil {db.MAX_NET_DROP_PER_KM} m/km){' — DRY RUN' if args.dry_run else ''}",
          file=sys.stderr)

    no_streams: list[int] = []
    dropped = 0
    changed = 0
    unchanged = 0
    for n, (aid, start, distance) in enumerate(runs, start=1):
        streams = _streams_of(db.get_streams(aid))
        dist_col = _series(streams, "distance")
        time_col = _series(streams, "time")
        alt_col = _series(streams, "altitude")
        if not dist_col or not time_col or not any(a is not None for a in alt_col):
            no_streams.append(aid)
            continue

        cum: list[tuple[float, float]] = []
        alt: list[float | None] = []
        for i, (d, t) in enumerate(zip(dist_col, time_col)):
            if d is None or t is None:
                continue
            cum.append((d, t))
            alt.append(alt_col[i] if i < len(alt_col) else None)

        efforts = _compute_best_efforts(aid, cum, alt)
        kept = {e["name"]: e for e in efforts}

        cur = dbpg._safe_conn().cursor()
        cur.execute(
            "SELECT name, moving_time, elevation_delta "
            "FROM activity_best_efforts WHERE activity_id = %s",
            [aid],
        )
        before = {str(r[0]): (int(r[1] or 0), r[2]) for r in cur.fetchall()}
        dbpg._safe_conn().rollback()

        diffs = []
        needs_write = False
        for name in dbpg.COMPUTED_EFFORT_NAMES:
            old = before.get(name)
            new = kept.get(name)
            if old is None and new is None:
                continue
            if new is None:
                diffs.append(f"{name} {old[0]}s → SUPPRIMÉ (descente)")
                dropped += 1
            elif old is None:
                diffs.append(f"{name} → {new['moving_time']}s (D {new['elevation_delta']:+.0f} m)")
            elif int(new["moving_time"]) != old[0]:
                diffs.append(f"{name} {old[0]}s → {new['moving_time']}s "
                             f"(D {new['elevation_delta']:+.0f} m)")
            elif old[1] is None:
                # Chrono identique mais dénivelé pas encore renseigné : on écrit
                # quand même, sinon le garde-fou de lecture reste aveugle.
                needs_write = True
            if new is None or old is None or int(new["moving_time"]) != old[0]:
                needs_write = True
        if diffs:
            changed += 1
            print(f"  [{n}/{len(runs)}] {aid} {start[:10]} {distance / 1000:.1f} km : "
                  + " | ".join(diffs), file=sys.stderr)
        else:
            unchanged += 1

        # Ne réécrire que ce qui bouge : chaque upsert réplique vers la 2e base
        # et bouge run_details_updated_at (donc la synchro incrémentale).
        if needs_write and not args.dry_run:
            db.upsert_activity_details(aid, [], efforts, distance,
                                       mark_fetched=False, replace_efforts=True)

    print(f"[backfill-efforts] terminé : {changed} run(s) modifié(s) "
          f"({dropped} record(s) écarté(s) pour descente), {unchanged} inchangé(s), "
          f"{len(no_streams)} sans stream d'altitude (laissés tels quels)",
          file=sys.stderr)
    if no_streams:
        print(f"[backfill-efforts] sans altitude : "
              f"{', '.join(str(a) for a in no_streams[:20])}"
              f"{' …' if len(no_streams) > 20 else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

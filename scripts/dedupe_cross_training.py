#!/usr/bin/env python3
"""Fusionne les sorties hors course enregistrees deux fois par Garmin.

Garmin renvoie parfois la meme sortie sous deux types (montre « Randonnee » +
telephone « Marche a pied »), demarres a quelques secondes d'ecart. Depuis que la
base miroite Garmin, les deux arrivent. `garmin_freshness._dedupe_cross_training`
les fusionne a l'import ; ce script rattrape les lignes deja stockees, avec la
meme fonction — une seule regle a maintenir.

La trace la plus complete est conservee (distance, puis duree) ; l'autre est
supprimee via `db.delete_activity`, qui repercute sur les deux bases et pose un
tombstone pour qu'elle ne revienne pas.

Les courses ne sont jamais touchees : leur dedoublon Strava/Garmin est ailleurs.

    scripts/dedupe_cross_training.py --dry-run   # montre sans rien ecrire
    scripts/dedupe_cross_training.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backfill_common import ensure_project_python  # noqa: E402

ensure_project_python()


def _load_env() -> None:
    """`database_pg` lit DATABASE_URL a l'import : charger .env avant."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001 - best effort
        print(f"[dedupe] dotenv indisponible ({exc})", file=sys.stderr)


def describe(activity: dict) -> str:
    minutes = (activity["moving_time"] or activity["elapsed_time"]) // 60
    return (
        f"{str(activity['start_date_local'])[:19]}  {activity['type']:16s} "
        f"{activity['distance']:>8.0f} m {minutes:>5d} min  "
        f"id={activity['id']}  {activity['name'] or ''}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="affiche ce qui serait supprime sans rien ecrire",
    )
    args = parser.parse_args()

    _load_env()
    import db
    from garmin_freshness import _dedupe_cross_training

    activities = db.get_cross_training_activities()
    print(f"[dedupe] {len(activities)} activites hors course en base", file=sys.stderr)

    kept, dropped = _dedupe_cross_training(activities)
    if not dropped:
        print("[dedupe] aucun doublon — rien a faire", file=sys.stderr)
        return 0

    kept_by_id = {a["id"]: a for a in kept}
    print(f"\n[dedupe] {len(dropped)} doublon(s) :\n", file=sys.stderr)
    for duplicate in dropped:
        twin = _closest_kept(duplicate, kept_by_id.values())
        print(f"  garde    {describe(twin)}" if twin else "  garde    ?", file=sys.stderr)
        print(f"  supprime {describe(duplicate)}\n", file=sys.stderr)

    if args.dry_run:
        print("[dedupe] --dry-run : rien n'a ete supprime", file=sys.stderr)
        return 0

    for duplicate in dropped:
        db.delete_activity(duplicate["id"])
    print(f"[dedupe] {len(dropped)} activite(s) supprimee(s)", file=sys.stderr)
    return 0


def _closest_kept(duplicate: dict, kept) -> dict | None:
    """La ligne conservee qui correspond au doublon (pour l'affichage)."""
    from garmin_freshness import _is_same_outing_pair

    for candidate in kept:
        if _is_same_outing_pair(duplicate, candidate):
            return candidate
    return None


if __name__ == "__main__":
    raise SystemExit(main())

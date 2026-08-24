"""Regles des records de course, sans aucune dependance.

Ces constantes etaient dans `database_pg`, qui importe pg8000. Tout consommateur
devait donc tirer un driver Postgres pour connaitre le seuil de pente — y compris
`scripts/coach_journal.py`, qui lit un dump SQL et dont l'en-tete promet
explicitement AUCUN acces reseau. Les recopier ailleurs aurait recree la
divergence que ce module existe pour empecher : le seuil est verifie a trois
endroits (calcul dans garmin_freshness, lecture dans database_pg, profil coach),
et trois copies finissent toujours par diverger.

`database_pg` les reexporte, donc `db.MAX_NET_DROP_PER_KM` et les imports
existants continuent de fonctionner a l'identique.
"""

# Un chrono descendu en pente n'est pas un record. Toute fenêtre dont la perte
# d'altitude nette dépasse ce seuil (mètres par km parcouru) est écartée, à la
# fois au calcul (garmin_freshness._compute_best_efforts) et à la lecture
# (database_pg.get_computed_bests_bulk) — les lignes historiques n'ont pas de
# elevation_delta et restent donc acceptées.
# 5 m/km ≈ 0,5 % de pente moyenne : bien au-dessus du bruit baro/GPS d'un
# parcours plat (un record sur route tient dans ±3 m/km), bien en dessous d'une
# descente de montagne, qui perd couramment plusieurs dizaines de m/km.
MAX_NET_DROP_PER_KM = 5.0

# Map effort names to our distance types
EFFORT_NAME_MAP = {
    "5K": "5k",
    "10K": "10k",
    "Half-Marathon": "semi",
    "Marathon": "marathon",
    "15K": "15k",
    "20K": "20k",
    "30K": "30k",
}

# Distances recalculées par garmin_freshness._compute_best_efforts (les autres
# entrées de EFFORT_NAME_MAP ne viennent que d'imports Strava historiques).
# Source unique : c'est aussi ce que replace_efforts purge avant réécriture.
EFFORT_TARGET_METERS = {
    "5K": 5000,
    "10K": 10000,
    "Half-Marathon": 21097,
    "Marathon": 42195,
}
COMPUTED_EFFORT_NAMES = tuple(EFFORT_TARGET_METERS)


def is_downhill_assisted(elevation_delta, distance_meters):
    """Vrai si la fenetre descend trop pour compter comme un record.

    elevation_delta None = inconnu (run sans stream d'altitude) : la fenetre
    passe, exactement comme la clause SQL `elevation_delta IS NULL OR ...`.
    """
    if elevation_delta is None:
        return False
    return elevation_delta < -MAX_NET_DROP_PER_KM * ((distance_meters or 0) / 1000.0)

"""Ce que Garmin nous apprend du coureur, ecrit pour le prochain demarrage.

`runner_profile` est volontairement sans dependance : il ne sait pas lire une
base. Ce module fait le pont. Apres chaque freshness-check, il relit dans la
base les records (`activity_best_efforts`, memes regles que la page Records) et
la FC max observee sur 90 jours, puis les depose dans un snapshot que
`runner_profile` charge a l'import.

Consequence pour l'utilisateur : il connecte son compte Garmin, la premiere
synchronisation remplit ses records, et au demarrage suivant le plan est calibre
sur SES chronos — sans avoir rien saisi. Un `runner_profile.json` reste
prioritaire pour qui veut fixer son objectif a la main.

Pourquoi un snapshot fichier plutot qu'une lecture directe : le calendrier et
les allures sont des constantes de module, calculees a l'import de
`daily_training_plan`. Les recalculer a chaque requete rendrait le plan
dependant de l'etat de la base au moment de l'appel — donc changeant en cours de
session, y compris entre deux pages du site.
"""

from __future__ import annotations

import sys
from datetime import date

import db
from heart_rate_reference import max_hr_reference
from runner_profile import DISTANCES_KM, write_observed_snapshot

# Les distances que `garmin_freshness._compute_best_efforts` recalcule. Les
# autres entrees de EFFORT_NAME_MAP ne viennent que d'imports historiques.
OBSERVED_DISTANCES = ("5k", "10k", "semi", "marathon")


def _observed_records() -> dict[str, int]:
    """Meilleur chrono connu par distance, en secondes.

    Passe par `get_computed_bests_bulk` : c'est la meme lecture que la page
    Records, filtre de denivele compris. Recopier la requete ici ferait
    diverger le plan de ce que le site affiche.
    """
    try:
        bulk = db.get_computed_bests_bulk(list(OBSERVED_DISTANCES))
    except Exception as exc:  # pragma: no cover - lecture best-effort
        print(f"[runner-profile] records illisibles: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {}

    records: dict[str, int] = {}
    for distance in OBSERVED_DISTANCES:
        efforts = bulk.get(distance) or []
        if not efforts:
            continue
        # La requete trie par moving_time croissant : le premier est le record.
        seconds = efforts[0].get("timeSeconds")
        try:
            seconds = int(round(float(seconds)))
        except (TypeError, ValueError):
            continue
        if seconds > 0 and distance in DISTANCES_KM:
            records[distance] = seconds
    return records


def _observed_max_hr(day: date) -> int | None:
    """FC max reellement atteinte sur les 90 derniers jours."""
    try:
        history = db.get_recent_runs_for_plan(day.isoformat(), days=90)
    except Exception as exc:  # pragma: no cover - lecture best-effort
        print(f"[runner-profile] historique FC illisible: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    reference = max_hr_reference(history, day.isoformat())
    if reference.get("source") != "observed_90d":
        # Aucune observation dans la fenetre : ne rien ecrire vaut mieux
        # qu'ecrire le repli, qui figerait une valeur arbitraire dans le snapshot.
        return None
    return int(round(reference["value"]))


def refresh_observed_profile(day: date | None = None) -> bool:
    """Met le snapshot a jour depuis la base. Best-effort, jamais bloquant."""
    reference_day = day or date.today()
    records = _observed_records()
    max_hr = _observed_max_hr(reference_day)
    if not write_observed_snapshot(records, max_hr):
        return False
    detail = ", ".join(f"{key} {value}s" for key, value in sorted(records.items())) or "aucun record"
    print(
        f"[runner-profile] snapshot mis a jour ({detail}; FC max {max_hr or 'non observee'})",
        file=sys.stderr,
    )
    return True

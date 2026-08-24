#!/usr/bin/env python3
"""Seance du jour, telle que le site l'affiche — source unique pour le coach.

Pourquoi ce script existe. Il y avait deux methodes pour arriver a "la seance
du jour" : le site (calendrier code + plan_overrides + adaptation automatique)
et la tache matinale du coach, qui interpretait sa propre trame inline dans
SKILL.md. Deux methodes sur la meme trame = un decalage a chaque fois que l'une
des deux couches bouge (seance avancee d'un jour, allegement apres une qualite,
SL courue en avance...). Le coach annoncait un footing 55' + lignes quand le
site affichait 35-45' en allure de recuperation.

Ce script appelle EXACTEMENT le meme code que l'endpoint /api/data/daily-training
(`build_three_day_training_guidance`), avec les memes overrides coach et les
memes runs. La structure de seance n'a donc plus qu'une methode. Les bpm sont
convertis avec la reference automatique partagee ; un override saisi uniquement
dans le localStorage d'un navigateur reste, par definition, invisible au script.

Usage :
    .venv/bin/python scripts/seance_du_jour.py                 # aujourd'hui, texte
    .venv/bin/python scripts/seance_du_jour.py --jour 2026-08-15
    .venv/bin/python scripts/seance_du_jour.py --json          # meme donnee, brute
    .venv/bin/python scripts/seance_du_jour.py --jours 3       # J a J+3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover - dotenv fait partie des requirements
    pass

import db  # noqa: E402
import server  # noqa: E402
from heart_rate_reference import max_hr_reference  # noqa: E402
from runner_profile import PROFILE  # noqa: E402
from daily_training_plan import (  # noqa: E402
    active_plan_overrides,
    build_three_day_training_guidance,
)

def _fetch(target_day: str) -> tuple[dict, dict]:
    """Meme chemin que l'endpoint /api/data/daily-training."""
    server._apply_coach_plan_overrides()
    window = db.get_recent_runs_for_plan(target_day, days=90)
    # Meme troncature que l'endpoint : les 10 derniers entrainements charges.
    recent_runs = window[:10]
    latest_sleep = db.get_latest_sleep_score(target_day)
    guidance = build_three_day_training_guidance(target_day, recent_runs, latest_sleep)
    history = [
        {"date": run.get("date"), "max_heartrate": run.get("max_heartrate")}
        for run in window
        if run.get("date") and run.get("max_heartrate")
    ]
    # La valeur reste compatible avec le site (maximum observe sur 90 jours),
    # avec une origine et une date de pic explicites pour le coach.
    heart_rate_reference = max_hr_reference(
        history,
        target_day,
        default=DEFAULT_MAX_HR,
    )
    return guidance, heart_rate_reference


DEFAULT_MAX_HR = PROFILE.max_hr


def _hr_reference_label(reference: dict) -> str:
    value = int(round(reference["value"]))
    source = reference.get("source")
    observed_on = reference.get("observedOn")
    if source == "observed_90d":
        when = f", pic du {observed_on}" if observed_on else ""
        return f"FC max utilisee {value} bpm, observee sur 90 jours{when}"
    if source == "override":
        return f"FC max utilisee {value} bpm, override partage"
    return f"FC max utilisee {value} bpm, repli personnel faute d'observation recente"


def _render(guidance: dict, horizon: int, heart_rate_reference: dict) -> str:
    sessions = guidance.get("sessions") or []
    if not sessions:
        return "Aucune seance : le calendrier ne couvre pas ce jour."

    today = sessions[0]
    max_hr = int(round(heart_rate_reference["value"]))
    session = today.get("session") or {}
    overrides = active_plan_overrides()
    lines: list[str] = []

    lines.append("=== DEBUT SEANCE DU JOUR ===")
    lines.append("Structure de seance du site — source unique, ne pas recomposer.")
    lines.append(
        "FC : reference automatique partagee. Un override manuel propre a un "
        "navigateur peut afficher d'autres bpm sur ce navigateur uniquement."
    )
    lines.append(f"Date        : {today.get('dateLabel') or today.get('date')}")
    week = guidance.get("currentWeek") or {}
    if week:
        km_min = week.get("estimatedKmMin")
        km_max = week.get("estimatedKmMax")
        km_text = f"{km_min}-{km_max}" if km_min != km_max else str(km_max)
        days_min = week.get("plannedRunDaysMin")
        days_max = week.get("plannedRunDaysMax")
        days_text = f"{days_min}-{days_max}" if days_min != days_max else str(days_max)
        lines.append(
            f"Semaine     : {week.get('label')} · {week.get('phaseLabel')} · "
            f"~{km_text} km · {days_text} sorties"
        )
    lines.append(f"Seance      : {today.get('title')}")
    lines.append(f"Statut      : {today.get('statusLabel') or today.get('status')}")
    lines.append(f"Categorie   : {today.get('category')}")
    if today.get("estimatedDuration") or today.get("estimatedKm"):
        lines.append(
            f"Volume est. : ~{today.get('estimatedKm')} km / {today.get('estimatedDuration')}"
        )
    lines.append(f"Ajustement  : {today.get('adjustment')}")
    if today.get("date") in overrides:
        note = (overrides[today["date"]] or {}).get("overrideNote")
        lines.append(f"Override coach deja pose sur ce jour : {note or '(sans note)'}")

    lines.append("")
    lines.append(f"Echauffement    : {session.get('warmup') or '-'}")
    lines.append(f"Courir          : {session.get('main') or '-'}")
    lines.append(f"Retour au calme : {session.get('cooldown') or '-'}")

    paces = today.get("paces") or []
    if paces:
        lines.append("")
        lines.append("Allures :")
        for pace in paces:
            note = f" ({pace['note']})" if pace.get("note") else ""
            lines.append(f"  - {pace.get('label')} : {pace.get('value')}{note}")

    hr = today.get("hr") or []
    if hr:
        lines.append(f"FC cible ({_hr_reference_label(heart_rate_reference)}) :")
        for zone in hr:
            bounds = ""
            if zone.get("pctMin") and zone.get("pctMax"):
                low = int(round(zone["pctMin"] * max_hr))
                high = int(round(zone["pctMax"] * max_hr))
                bounds = f"{low}-{high} bpm ({int(zone['pctMin'] * 100)}-{int(zone['pctMax'] * 100)}%)"
            note = f" — {zone['note']}" if zone.get("note") else ""
            lines.append(f"  - {zone.get('label')} : {bounds or '-'}{note}")

    observations = today.get("observations")
    if observations:
        lines.append("")
        lines.append(f"Donnees vues : {observations}")

    upcoming = sessions[1 : horizon + 1]
    if upcoming:
        lines.append("")
        lines.append("Jours suivants (deja adaptes, meme source) :")
        for entry in upcoming:
            flag = " [override coach]" if entry.get("date") in overrides else ""
            lines.append(
                f"  {entry.get('relativeLabel') or ''} {entry.get('dateLabel') or entry.get('date')}"
                f" : {entry.get('title')}{flag}"
            )
            lines.append(f"      {(entry.get('session') or {}).get('main') or '-'}")

    lines.append("")
    lines.append(
        "RAPPEL : cette sortie EST la verite affichee au dashboard. Ne recompose pas "
        "la seance depuis une trame inline. Pour changer un jour, appelle "
        "scripts/ajuster_le_plan.py."
    )
    lines.append("=== FIN SEANCE DU JOUR ===")
    return "\n".join(lines)


def _json_payload(
    guidance: dict,
    heart_rate_reference: dict,
) -> dict:
    """Contrat structure du coach, testable sans base de donnees."""
    payload = dict(guidance)
    payload["schemaVersion"] = 1
    payload["maxHr"] = int(round(heart_rate_reference["value"]))
    payload["heartRateReference"] = {
        **heart_rate_reference,
        "browserLocalOverrideVisible": False,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jour", default="", help="Jour cible ISO (defaut : aujourd'hui)")
    parser.add_argument("--jours", type=int, default=7, help="Nombre de jours suivants a lister")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute")
    args = parser.parse_args()

    target_day = (args.jour or date.today().isoformat())[:10]
    guidance, heart_rate_reference = _fetch(target_day)

    if args.json:
        payload = _json_payload(guidance, heart_rate_reference)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(_render(guidance, max(0, args.jours), heart_rate_reference))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

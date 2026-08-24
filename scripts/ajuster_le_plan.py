#!/usr/bin/env python3
"""CLI `ajuster_le_plan` — forme exécutable de l'outil MCP du même nom.

La vérité du plan NE vit PAS dans un SKILL.md ni dans un markdown. Le calendrier
marathon est figé dans le code (`daily_training_plan._build_calendar`). Le SEUL
canal par lequel une décision du coach atteint le dashboard est la table
`plan_overrides`, écrite ici via `db.upsert_plan_override` — exactement le même
chemin que l'outil MCP `ajuster_le_plan` (voir coach_mcp.py et CLAUDE.md).

L'écriture va d'abord sur la base primaire (locale en dev) puis est répliquée
automatiquement vers la 2e base (Neon) par `database_pg._replicate`. On ne
« touche » donc jamais Neon directement : la réplication intégrée s'en charge.

Usage :
  # Poser/écraser une séance sur un jour
  python scripts/ajuster_le_plan.py set --jour 2026-08-01 --categorie rest \
      --note "Randonnée, pas de course"
  python scripts/ajuster_le_plan.py set --jour 2026-07-30 --categorie easy \
      --titre "Footing récup" --contenu "40' très facile 5:20-5:40" \
      --note "Lendemain de grosse SL"

  # Lister les ajustements actifs (ce que le site affiche)
  python scripts/ajuster_le_plan.py lister

  # Annuler un ajustement (retour au calendrier codé)
  python scripts/ajuster_le_plan.py annuler --jour 2026-08-01

Sortie : JSON sur stdout (ok/erreur), miroir de l'outil MCP. Code retour 0 si ok.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Charge .env du repo sans écraser l'environnement déjà présent."""
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001 - best effort, on continue
        print(f"[ajuster_le_plan] dotenv indisponible ({exc})", file=sys.stderr)


def _deps():
    """Import tardif : mêmes fonctions que l'outil MCP `ajuster_le_plan`."""
    sys.path.insert(0, str(REPO_ROOT))
    import db  # noqa: WPS433
    from daily_training_plan import normalize_plan_override  # noqa: WPS433

    return db, normalize_plan_override


def _normalized_day(jour: str) -> str:
    from datetime import date

    day = str(jour or "").strip()[:10]
    date.fromisoformat(day)  # ValueError si format invalide
    return day


def cmd_set(args: argparse.Namespace) -> int:
    db, normalize_plan_override = _deps()
    day = _normalized_day(args.jour)
    payload = {
        "title": args.titre or "",
        "main": args.contenu or "",
        "category": (args.categorie or "easy").strip().lower(),
        "warmup": args.echauffement or "",
        "cooldown": args.retour_au_calme or "",
        "tag": args.tag or "",
        "note": args.note or "",
    }
    session = normalize_plan_override(payload)
    if session is None:
        print(json.dumps({
            "ok": False,
            "jour": day,
            "erreur": (
                "Ajustement inutilisable : fournis --titre ET --contenu, ou "
                "--categorie rest pour un jour de repos. Rien n'a été écrit."
            ),
        }, ensure_ascii=False, indent=2))
        return 1
    db.upsert_plan_override(day, session, note=args.note or "", source="coach-cli")
    print(json.dumps({
        "ok": True,
        "jour": day,
        "seance": session,
        "effet": "Le site affiche cet ajustement dès le prochain chargement du plan.",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_lister(_args: argparse.Namespace) -> int:
    db, _ = _deps()
    overrides = db.get_plan_overrides()
    print(json.dumps({
        "nombre": len(overrides),
        "ajustements": overrides,
        "rappel": (
            "Ces ajustements sont ceux que le site affiche. Le calendrier de base "
            "est figé dans le code : tout ce qui n'est pas ici n'existe pas pour "
            "le dashboard."
        ),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_annuler(args: argparse.Namespace) -> int:
    db, _ = _deps()
    day = _normalized_day(args.jour)
    removed = db.delete_plan_override(day)
    print(json.dumps({
        "ok": True,
        "jour": day,
        "supprime": removed,
        "effet": (
            "Retour à la séance du calendrier de base."
            if removed
            else "Aucun ajustement n'existait pour ce jour."
        ),
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ajuster_le_plan",
        description="Écrit/lit/supprime les ajustements coach dans plan_overrides "
                    "(seul canal vers le dashboard).",
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    p_set = sub.add_parser("set", help="Poser ou écraser la séance d'un jour.")
    p_set.add_argument("--jour", required=True, help="Jour ISO YYYY-MM-DD.")
    p_set.add_argument("--titre", default="", help="Titre de la séance.")
    p_set.add_argument("--contenu", default="", help="Corps de la séance.")
    p_set.add_argument("--categorie", default="easy",
                       help="easy | quality | long | rest | race.")
    p_set.add_argument("--echauffement", default="")
    p_set.add_argument("--retour-au-calme", dest="retour_au_calme", default="")
    p_set.add_argument("--tag", default="")
    p_set.add_argument("--note", default="", help="Raison de l'ajustement.")
    p_set.set_defaults(func=cmd_set)

    p_list = sub.add_parser("lister", help="Lister les ajustements actifs.")
    p_list.set_defaults(func=cmd_lister)

    p_del = sub.add_parser("annuler", help="Supprimer l'ajustement d'un jour.")
    p_del.add_argument("--jour", required=True, help="Jour ISO YYYY-MM-DD.")
    p_del.set_defaults(func=cmd_annuler)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "erreur": f"Jour invalide : {exc}"},
                         ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "erreur": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

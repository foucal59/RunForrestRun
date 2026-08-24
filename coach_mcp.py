"""Shared FastMCP server for the running coach.

Lecture : le journal statique genere par scripts/coach_journal.py (aucun acces a
Neon ni Garmin).

Ecriture : les outils `ajuster_le_plan` / `annuler_ajustement_plan` ecrivent dans
la table `plan_overrides`. C'est le seul canal par lequel une decision du coach
atteint le site : le calendrier marathon lui-meme est fige dans le code
(daily_training_plan._build_calendar), donc un ajustement note ailleurs (SKILL.md,
markdown du plan) reste invisible pour le dashboard.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "public" / "coach-journal.json"
STATIC_SNAPSHOT_PATHS = (
    DEFAULT_SNAPSHOT_PATH,
    REPO_ROOT / "dist" / "coach-journal.json",
    Path.cwd() / "public" / "coach-journal.json",
    Path.cwd() / "dist" / "coach-journal.json",
)

mcp = FastMCP("Coach Marathon")


def _snapshot_url() -> str:
    return os.environ.get("COACH_SNAPSHOT_URL", "").strip()


def _snapshot_path() -> Path:
    return Path(os.environ.get("COACH_SNAPSHOT_PATH", str(DEFAULT_SNAPSHOT_PATH)))


def _static_snapshot_url() -> str:
    base_url = os.environ.get("COACH_STATIC_BASE_URL", "").strip()
    # Vercel's production hostname is public and stable. A deployment-specific
    # VERCEL_URL may be protected and therefore unusable from the function.
    if not base_url and os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
        base_url = f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
    if not base_url:
        base_url = os.environ.get("BASE_URL", "").strip()
    if not base_url and os.environ.get("VERCEL_URL"):
        base_url = f"https://{os.environ['VERCEL_URL']}"
    if not base_url:
        return ""
    return f"{base_url.rstrip('/')}/coach-journal.json"


def _load_snapshot_url(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def load_snapshot() -> dict[str, Any]:
    """Load the latest coach snapshot from URL, local files, or Vercel static asset."""
    snapshot_url = _snapshot_url()
    if snapshot_url:
        try:
            return _load_snapshot_url(snapshot_url)
        except Exception as exc:  # noqa: BLE001 - local fallback keeps MCP usable
            print(f"[coach-mcp] snapshot URL failed ({exc}); using local file", flush=True)

    paths = (_snapshot_path(), *STATIC_SNAPSHOT_PATHS)
    seen: set[Path] = set()
    for path in paths:
        candidate = path.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            with candidate.open(encoding="utf-8") as handle:
                return json.load(handle)

    static_url = _static_snapshot_url()
    if static_url:
        try:
            return _load_snapshot_url(static_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[coach-mcp] static snapshot URL failed ({exc})", flush=True)

    raise FileNotFoundError(f"coach snapshot not found in: {', '.join(str(path) for path in paths)}")


def _bounded_count(value: int, default: int = 3, maximum: int = 7) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(1, min(maximum, count))


def _recent_runs(snapshot: dict[str, Any], count: int) -> list[dict[str, Any]]:
    runs = snapshot.get("derniers_runs") or []
    return list(runs[:_bounded_count(count)])


def _training_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "genere_le": snapshot.get("genere_le"),
        "objectif": snapshot.get("objectif"),
        "aujourdhui": snapshot.get("seance_du_jour"),
        "projection": snapshot.get("projection", []),
        "zones_allure": snapshot.get("zones_allure"),
        "autres_activites": snapshot.get("autres_activites", []),
        "regle_ajustement": snapshot.get("regle_ajustement"),
        "consigne_fatigue": (
            "Les autres activites ne comptent pas dans le volume de course, "
            "mais leur duree et leur denivele doivent peser sur la fraicheur."
        ),
    }


def _analysis_payload(snapshot: dict[str, Any], nombre: int = 3) -> dict[str, Any]:
    runs = _recent_runs(snapshot, nombre)
    return {
        "genere_le": snapshot.get("genere_le"),
        "objectif": snapshot.get("objectif"),
        "volume_7j_km": snapshot.get("volume_7j_km"),
        "dernier_run": runs[0] if runs else None,
        "runs": runs,
        "autres_activites": snapshot.get("autres_activites", []),
        "consigne_client": (
            "Analyse les runs et la charge hors course recente, sans compter "
            "celle-ci dans le volume running. Examine fractions, allure, FC, "
            "duree et denivele, puis termine par un avis clair sur la suite."
        ),
    }


@mcp.tool
def journal_du_jour() -> dict[str, Any]:
    """Return the complete coach snapshot: profile, goal, recent runs and plan."""
    return load_snapshot()


@mcp.tool
def entrainement_a_faire() -> dict[str, Any]:
    """Return today's training, next sessions, pace zones and adjustment rule."""
    return _training_payload(load_snapshot())


@mcp.tool
def seance_du_jour() -> dict[str, Any]:
    """Return today's planned session with generation date."""
    snapshot = load_snapshot()
    return {
        "genere_le": snapshot.get("genere_le"),
        **(snapshot.get("seance_du_jour") or {}),
    }


@mcp.tool
def analyse_runs_precedents(nombre: int = 3) -> dict[str, Any]:
    """Return recent runs for coaching analysis, newest first."""
    return _analysis_payload(load_snapshot(), nombre)


@mcp.tool
def sept_derniers_runs() -> dict[str, Any]:
    """Return the 7 latest analyzed runs with splits, pace and heart rate."""
    snapshot = load_snapshot()
    return {
        "genere_le": snapshot.get("genere_le"),
        "volume_7j_km": snapshot.get("volume_7j_km"),
        "runs": snapshot.get("derniers_runs", []),
    }


@mcp.tool
def autres_activites() -> dict[str, Any]:
    """Return recent non-running activities (hiking, cycling, skiing, strength…).

    Elles ne comptent ni dans le volume, ni dans les allures, ni dans les records
    — mais elles pesent sur la fraicheur. Une grosse rando la veille justifie
    d'alleger la seance du lendemain via `ajuster_le_plan`.
    """
    snapshot = load_snapshot()
    activites = snapshot.get("autres_activites") or []
    return {
        "genere_le": snapshot.get("genere_le"),
        "activites": activites,
        "consigne_client": (
            "Ces activites ne sont pas des courses : ne les compte jamais dans le "
            "volume hebdomadaire. Sers-t'en uniquement pour juger la fatigue "
            "(denivele, duree, temps sur pieds) avant de programmer une seance a "
            "enjeu, et ajuste le plan avec `ajuster_le_plan` si besoin."
        ),
    }


@mcp.tool
def projection() -> dict[str, Any]:
    """Return the next 3 planned sessions and the coach adjustment rule."""
    snapshot = load_snapshot()
    return {
        "genere_le": snapshot.get("genere_le"),
        "objectif": snapshot.get("objectif"),
        "projection": snapshot.get("projection", []),
        "regle_ajustement": snapshot.get("regle_ajustement"),
    }


@mcp.tool
def zones_allure() -> dict[str, Any]:
    """Return target pace zones for easy, threshold, race pace and VO2max work."""
    snapshot = load_snapshot()
    return {
        "profil": snapshot.get("profil"),
        "zones_allure": snapshot.get("zones_allure"),
    }


@mcp.tool
def poser_question_coach(question: str) -> dict[str, Any]:
    """Return the coach context needed to answer a natural-language question."""
    snapshot = load_snapshot()
    lower_question = (question or "").lower()
    if any(word in lower_question for word in ("aujourd", "seance", "entrainement", "faire")):
        contexte: dict[str, Any] = _training_payload(snapshot)
    elif any(word in lower_question for word in ("run", "course", "preced", "analyse", "dernier")):
        contexte = _analysis_payload(snapshot, 3)
    else:
        contexte = _training_payload(snapshot)
    return {
        "question": question,
        "consigne_client": (
            "Reponds comme un coach running concis, en francais, en utilisant "
            "uniquement ce snapshot. Cite les dates, allures et volumes utiles. "
            "Si la donnee n'est pas dans le snapshot, dis-le clairement."
        ),
        "contexte_selectionne": contexte,
        "snapshot": snapshot,
    }


# ── Ajustements du plan (ecriture) ──

_ISO_DAY_LENGTH = 10


def _normalized_day(jour: str) -> str:
    """Valide un jour ISO 'YYYY-MM-DD' (evite d'ecrire une cle bancale en base)."""
    day = str(jour or "").strip()[:_ISO_DAY_LENGTH]
    date.fromisoformat(day)  # leve ValueError si le format est invalide
    return day


def _plan_write_deps():
    """Charge db + le plan a la demande : le MCP reste utilisable sans base."""
    import db
    from daily_training_plan import normalize_plan_override

    return db, normalize_plan_override


@mcp.tool
def ajustements_du_plan() -> dict[str, Any]:
    """Return the coach adjustments currently overriding the hard-coded plan."""
    db, _ = _plan_write_deps()
    overrides = db.get_plan_overrides()
    return {
        "nombre": len(overrides),
        "ajustements": overrides,
        "rappel": (
            "Ces ajustements sont ceux que le site affiche. Le calendrier de base "
            "est fige dans le code : tout ce qui n'est pas ici n'existe pas pour "
            "le dashboard."
        ),
    }


@mcp.tool
def ajuster_le_plan(
    jour: str,
    titre: str = "",
    contenu: str = "",
    categorie: str = "easy",
    echauffement: str = "",
    retour_au_calme: str = "",
    tag: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Replace the planned session of one day so the site shows the adjustment.

    `jour` is ISO 'YYYY-MM-DD'. Use categorie='rest' for a rest day (titre and
    contenu are then optional); otherwise both titre and contenu are required.
    Valid categorie values: easy, quality, long, rest, race.
    """
    db, normalize_plan_override = _plan_write_deps()
    day = _normalized_day(jour)
    payload = {
        "title": titre,
        "main": contenu,
        "category": (categorie or "easy").strip().lower(),
        "warmup": echauffement,
        "cooldown": retour_au_calme,
        "tag": tag,
        "note": note,
    }
    session = normalize_plan_override(payload)
    if session is None:
        return {
            "ok": False,
            "jour": day,
            "erreur": (
                "Ajustement inutilisable : fournis un titre ET un contenu, ou "
                "categorie='rest' pour un jour de repos. Rien n'a ete ecrit."
            ),
        }

    db.upsert_plan_override(day, session, note=note, source="coach-mcp")
    return {
        "ok": True,
        "jour": day,
        "seance": session,
        "effet": "Le site affiche cet ajustement des le prochain chargement du plan.",
    }


@mcp.tool
def annuler_ajustement_plan(jour: str) -> dict[str, Any]:
    """Remove the coach adjustment for one day and fall back to the coded plan."""
    db, _ = _plan_write_deps()
    day = _normalized_day(jour)
    removed = db.delete_plan_override(day)
    return {
        "ok": True,
        "jour": day,
        "supprime": removed,
        "effet": (
            "Retour a la seance du calendrier de base."
            if removed
            else "Aucun ajustement n'existait pour ce jour."
        ),
    }


def create_http_app(path: str = "/"):
    """Create an ASGI app exposing the MCP Streamable HTTP endpoint."""
    return mcp.http_app(path=path)

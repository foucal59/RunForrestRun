#!/usr/bin/env python3
"""Météo Open-Meteo d'une course + mise à jour du plan.

La base de données (Neon / local) est la source de vérité pour la course
(coordonnées GPS de départ + heure). On interroge Open-Meteo au point horaire
le plus proche du départ (Europe/Paris), puis on peut réécrire la section
« Météo de la dernière course » du plan marathon.

Usage :
  python scripts/weather_for_run.py                  # dernière course, affiche
  python scripts/weather_for_run.py --activity-id ID # une course précise
  python scripts/weather_for_run.py --update-plan     # réécrit la section du .md
  python scripts/weather_for_run.py --json            # sortie JSON brute

À lancer à CHAQUE analyse de run / mise à jour du plan (voir CLAUDE.md).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parent.parent
# Fichier de plan a mettre a jour avec --update-plan. Propre a chaque coureur :
# indique le tien via TRAINING_PLAN_FILE (chemin absolu ou relatif au repo).
PLAN = Path(os.environ.get("TRAINING_PLAN_FILE") or (REPO / "training-plan.md"))
if not PLAN.is_absolute():
    PLAN = REPO / PLAN
API_HOURLY = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
    "rain,weather_code,wind_speed_10m,wind_gusts_10m"
)

# Libellés WMO (simplifiés, FR)
WMO = {
    0: "ciel clair", 1: "plutôt clair", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grains de neige",
    80: "averses faibles", 81: "averses", 82: "averses violentes",
    85: "averses de neige", 86: "averses de neige fortes",
    95: "orage", 96: "orage avec grêle", 99: "orage avec grêle forte",
}
MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


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


def connect_all() -> list[tuple[str, str, object]]:
    """Ouvre toutes les bases joignables (locale + Neon).

    Les deux déploiements ont une primaire différente et la réplication peut
    être en retard : on se connecte à toutes les bases atteignables et on
    choisira ensuite la course la plus fraîche (cf. get_run). Ça évite de lire
    une base locale périmée quand Neon est en avance (ou l'inverse).
    """
    import pg8000.native

    load_env()
    cons: list[tuple[str, str, object]] = []
    seen: set[tuple] = set()
    for name in ("DATABASE_URL", "LOCAL_DATABASE_URL", "DATABASE_URL_NEON"):
        u = os.environ.get(name)
        if not u:
            continue
        p = urlparse(u)
        key = (p.hostname, p.port or 5432, p.path)
        if key in seen:
            continue
        seen.add(key)
        ssl = True if ("neon" in (p.hostname or "") or "sslmode=require" in u) else None
        try:
            con = pg8000.native.Connection(
                user=p.username,
                password=unquote(p.password) if p.password else None,
                host=p.hostname, port=p.port or 5432,
                database=(p.path.lstrip("/").split("?")[0]) or "postgres",
                ssl_context=ssl, timeout=15,
            )
            cons.append((name, p.hostname or "?", con))
            log(f"[weather] connecté: {name} ({p.hostname})")
        except Exception as e:  # noqa: BLE001
            log(f"[weather] {name} injoignable: {type(e).__name__}: {e}")
    if not cons:
        raise SystemExit("[weather] aucune base joignable")
    return cons


def get_run(cons, activity_id: int | None) -> dict:
    cols = "id, name, start_date_local, start_lat, start_lng, has_heartrate"
    keys = cols.replace(" ", "").split(",")
    best: dict | None = None
    best_con = None
    for name, host, con in cons:
        try:
            if activity_id:
                rows = con.run(f"SELECT {cols} FROM activities WHERE id = :id", id=activity_id)
            else:
                rows = con.run(
                    f"SELECT {cols} FROM activities WHERE type='Run' "
                    "ORDER BY start_date_local DESC LIMIT 1"
                )
        except Exception as e:  # noqa: BLE001
            log(f"[weather] requête échouée sur {name}: {e}")
            continue
        if not rows:
            continue
        run = dict(zip(keys, rows[0]))
        if best is None or str(run["start_date_local"]) > str(best["start_date_local"]):
            best, best_con = run, con
    if best is None:
        raise SystemExit("[weather] aucune course trouvée")
    log(f"[weather] course retenue : {best['start_date_local']} (base la plus fraîche)")
    # Fallback coords : premier point GPS non nul du stream (même base)
    if best["start_lat"] is None or best["start_lng"] is None:
        pts = best_con.run(
            "SELECT lat, lng FROM activity_streams WHERE activity_id = :id "
            "AND lat IS NOT NULL AND lng IS NOT NULL ORDER BY stream_index LIMIT 1",
            id=best["id"],
        )
        if pts:
            best["start_lat"], best["start_lng"] = pts[0][0], pts[0][1]
    return best


def parse_start(start_date_local) -> tuple[str, int, int]:
    """Retourne (date_iso, heure, minute) en heure locale de la course."""
    s = str(start_date_local)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", s)
    if not m:
        raise SystemExit(f"[weather] start_date_local illisible: {s!r}")
    y, mo, d, hh, mm = map(int, m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}", hh, mm


def fetch_weather(lat: float, lon: float, date_iso: str, hour: int) -> dict:
    import httpx

    d = dt.date.fromisoformat(date_iso)
    age = (dt.date.today() - d).days
    base = (
        "https://archive-api.open-meteo.com/v1/archive"
        if age > 5
        else "https://api.open-meteo.com/v1/forecast"
    )
    params = {
        "latitude": round(lat, 5), "longitude": round(lon, 5),
        "hourly": API_HOURLY, "start_date": date_iso, "end_date": date_iso,
        "timezone": "Europe/Paris",
    }
    r = httpx.get(base, params=params, timeout=30)
    r.raise_for_status()
    H = r.json()["hourly"]
    target = f"{date_iso}T{hour:02d}:00"
    idx = H["time"].index(target) if target in H["time"] else min(hour, len(H["time"]) - 1)
    out = {k: H[k][idx] for k in H if k != "time"}
    out["time"] = H["time"][idx]
    out["endpoint"] = "archive" if "archive" in base else "forecast"
    return out


def impact_sentence(w: dict, has_hr) -> str:
    t = w["temperature_2m"]
    gust = w["wind_gusts_10m"]
    wind = w["wind_speed_10m"]
    rain = (w.get("precipitation") or 0) or (w.get("rain") or 0)
    if t >= 28:
        tl = "forte chaleur"
    elif t >= 24:
        tl = "chaleur notable"
    elif t >= 20:
        tl = "chaleur correcte"
    elif t >= 12:
        tl = "conditions fraîches, favorables"
    elif t >= 5:
        tl = "temps frais"
    else:
        tl = "temps froid"
    rl = "pas de pluie" if rain <= 0 else f"pluie ({rain} mm)"
    if gust >= 45:
        wl = "rafales fortes"
    elif gust >= 35 or wind >= 20:
        wl = "vent sensible"
    else:
        wl = "vent faible"
    s = f"{tl.capitalize()}, {rl}, {wl}."
    if has_hr in (False, 0, "f", None):
        s += (
            " FC absente sur cette sortie (montre en réparation) : "
            "l'allure ne doit pas être surinterprétée comme une mesure pure de forme."
        )
    return s


def build_section(run: dict, w: dict, date_iso: str, hour: int, minute: int) -> str:
    y, mo, d = map(int, date_iso.split("-"))
    date_fr = f"{d} {MOIS_FR[mo]} {y}"
    code = int(w["weather_code"])
    code_lbl = WMO.get(code, "n/d")
    api_lbl = "Historical Weather API" if w["endpoint"] == "archive" else "Forecast API"
    return (
        "### Météo de la dernière course\n\n"
        f"Dernière course : {run['name']}, {date_fr} à {hour:02d}:{minute:02d}. "
        f"Coordonnées issues du stream GPS : départ autour de "
        f"**{run['start_lat']:.5f}, {run['start_lng']:.5f}**.\n\n"
        f"Source météo : Open-Meteo {api_lbl}, point horaire "
        f"**{hour:02d}:00 Europe/Paris**.\n\n"
        "| Température | Ressenti | Humidité | Pluie | Vent | Rafales | Code météo |\n"
        "|---:|---:|---:|---:|---:|---:|---|\n"
        f"| {w['temperature_2m']:.1f} °C | {w['apparent_temperature']:.1f} °C | "
        f"{int(round(w['relative_humidity_2m']))} % | {w['precipitation']:.1f} mm | "
        f"{w['wind_speed_10m']:.1f} km/h | {w['wind_gusts_10m']:.1f} km/h | "
        f"{code} ({code_lbl}) |\n\n"
        f"Impact sur l'analyse : {impact_sentence(w, run.get('has_heartrate'))}\n\n"
    )


def update_plan(section: str) -> bool:
    if not PLAN.exists():
        raise SystemExit(
            f"[weather] plan introuvable: {PLAN}\n"
            "  Indique ton fichier de plan via TRAINING_PLAN_FILE, ou lance la\n"
            "  commande sans --update-plan pour seulement afficher la meteo."
        )
    text = PLAN.read_text(encoding="utf-8")
    pat = re.compile(r"### Météo de la dernière course.*?(?=^---$)", re.DOTALL | re.MULTILINE)
    if not pat.search(text):
        raise SystemExit("[weather] section « Météo de la dernière course » introuvable")
    new = pat.sub(lambda _m: section, text, count=1)
    if new == text:
        return False
    PLAN.write_text(new, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity-id", type=int, default=None)
    ap.add_argument("--update-plan", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cons = connect_all()
    try:
        run = get_run(cons, args.activity_id)
    finally:
        for _n, _h, c in cons:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
    if run["start_lat"] is None or run["start_lng"] is None:
        raise SystemExit("[weather] pas de coordonnées GPS pour cette course")

    date_iso, hour, minute = parse_start(run["start_date_local"])
    w = fetch_weather(float(run["start_lat"]), float(run["start_lng"]), date_iso, hour)

    if args.json:
        print(json.dumps({"run": {k: str(v) for k, v in run.items()}, "weather": w},
                         ensure_ascii=False, indent=2))
        return

    section = build_section(run, w, date_iso, hour, minute)
    print(section)
    if args.update_plan:
        changed = update_plan(section)
        log("[weather] plan mis à jour" if changed else "[weather] plan déjà à jour")


if __name__ == "__main__":
    main()

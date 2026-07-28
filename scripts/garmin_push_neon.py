#!/usr/bin/env python3
"""
Pousse les tokens Garmin locaux dans Neon sync_meta['garmin_tokens'].

Le déploiement Vercel (filesystem éphémère) pourra ensuite les relire au runtime.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# racine projet sur le path (db / garmin_freshness y sont) — imports différés après
# avoir posé DATABASE_URL, car database_pg lit DATABASE_URL au moment de l'import.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def load_env_var(env_path: str, key: str) -> str:
    """Lit une clé depuis .env en ligne-par-ligne (préserve le & des query strings,
    même logique que scripts/start.sh). Dernière occurrence gagne."""
    val = ""
    if not os.path.exists(env_path):
        return ""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                val = v.strip().strip('"').strip("'")
    return val


def main() -> None:
    ap = argparse.ArgumentParser(description="Pousse les tokens Garmin locaux dans Neon")
    ap.add_argument("--token-dir", default=os.path.join(PROJECT_ROOT, ".runtime", "garminconnect"),
                    help="Dossier des tokens locaux (défaut .runtime/garminconnect/)")
    ap.add_argument("--env", default=os.path.join(PROJECT_ROOT, ".env"),
                    help="Fichier .env d'où lire DATABASE_URL (défaut .env)")
    args = ap.parse_args()

    # 1. DATABASE_URL Neon AVANT tout import de db.
    neon = os.environ.get("DATABASE_URL") or load_env_var(args.env, "DATABASE_URL")
    if not neon:
        print("[push] DATABASE_URL introuvable (ni env, ni .env) — abandon.", file=sys.stderr)
        sys.exit(1)
    os.environ["DATABASE_URL"] = neon
    # En self-hosted, start.sh flippe DATABASE_URL vers le local et garde Neon dans
    # DATABASE_URL_NEON. Ici on veut écrire DIRECTEMENT sur Neon → on neutralise la
    # réplication secondaire (Neon ET local) pour ne pas semer la confusion.
    os.environ.pop("DATABASE_URL_NEON", None)
    os.environ.pop("LOCAL_DATABASE_URL", None)
    host = neon.split("@")[-1].split("/")[0] if "@" in neon else "?"
    print(f"[push] DATABASE_URL → {host}", file=sys.stderr)

    import garmin_freshness
    import db

    api = garmin_freshness.load_garmin_api(args.token_dir)
    if api is None:
        print(
            f"[push] impossible de charger des tokens Garmin depuis {args.token_dir} "
            "— lance d'abord garmin_browser_login.py.",
            file=sys.stderr,
        )
        sys.exit(1)
    token_data = garmin_freshness.serialize_garmin_tokens(api)
    if "garmin_tokens.json" not in token_data:
        print(f"[push] tokens incomplets dans {args.token_dir}: {list(token_data)}", file=sys.stderr)
        sys.exit(1)
    print(f"[push] tokens chargés ({', '.join(sorted(token_data))})", file=sys.stderr)

    # 3. Écrire dans Neon sync_meta.
    db.set_sync_meta("garmin_tokens", token_data)
    print("[push] garmin_tokens écrit dans Neon sync_meta", file=sys.stderr)

    # 4. Relecture round-trip depuis Neon.
    meta = db.get_sync_meta()
    td = meta.get("garmin_tokens")
    if isinstance(td, str):
        td = json.loads(td)
    if not td or "garmin_tokens.json" not in td:
        print("[push] ⚠️ relecture Neon: garmin_tokens absent/incomplet.", file=sys.stderr)
        sys.exit(2)
    print("[push] relecture Neon OK", file=sys.stderr)

    try:
        profile = garmin_freshness.get_garmin_profile("")
        if profile:
            name = profile.get("display_name") or profile.get("full_name") or "(profil sans nom)"
            print(f"[push] validation live OK — {name}", file=sys.stderr)
        else:
            print("[push] validation live non concluante (profil absent)", file=sys.stderr)
    except Exception as e:
        print(f"[push] validation live non concluante (non bloquant): {e}", file=sys.stderr)

    print("\n✓ Tokens Garmin poussés dans Neon. Le déploiement Vercel les utilisera "
          "au prochain freshness-check.")


if __name__ == "__main__":
    main()

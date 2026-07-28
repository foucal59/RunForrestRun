#!/usr/bin/env python3
"""
Compat layer for the former browser-based Garmin login script.

This now uses python-garminconnect directly and writes tokens in the new
garminconnect format.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import garmin_freshness


def _read_or_prompt(value: str, prompt: str, secret: bool = False) -> str:
    if value:
        return value
    if secret:
        return getpass.getpass(prompt)
    return input(prompt).strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Login Garmin via python-garminconnect"
    )
    parser.add_argument(
        "--out",
        default=os.path.join(PROJECT_ROOT, ".runtime", "garminconnect"),
        help="Dossier de sortie des tokens",
    )
    parser.add_argument("--email", default=os.environ.get("GARMIN_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("GARMIN_PASSWORD", ""))
    parser.add_argument("--mfa-code", default=os.environ.get("GARMIN_MFA_CODE", ""))
    args = parser.parse_args()

    email = _read_or_prompt(args.email, "Email Garmin Connect : ")
    password = _read_or_prompt(args.password, "Mot de passe : ", secret=True)
    mfa_code = args.mfa_code.strip()
    if not mfa_code:
        mfa_code = input("Code MFA (laisser vide si non utilisé) : ").strip()

    try:
        profile = garmin_freshness.garmin_login(
            email=email,
            password=password,
            token_dir=args.out,
            mfa_code=mfa_code,
        )
    except Exception as exc:
        print(f"Échec de connexion Garmin : {exc}", file=sys.stderr)
        sys.exit(1)

    name = profile.get("full_name") or profile.get("display_name") or "?"
    print(f"✓ Connecté : {name}")
    print(f"✓ Tokens écrits dans : {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

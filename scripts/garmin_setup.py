#!/usr/bin/env python3
"""
Configuration initiale Garmin Connect.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import getpass
import garmin_freshness

def main():
    print("=== Configuration Garmin Connect ===\n")
    email = input("Email Garmin Connect : ").strip()
    password = getpass.getpass("Mot de passe : ")
    mfa_code = input("Code MFA (laisser vide si non utilisé) : ").strip()

    token_dir = os.path.join(os.path.dirname(__file__), "..", ".runtime", "garminconnect")
    os.makedirs(token_dir, exist_ok=True)

    print("\nConnexion à Garmin Connect…")
    try:
        profile = garmin_freshness.garmin_login(
            email,
            password,
            token_dir=token_dir,
            mfa_code=mfa_code,
        )
    except Exception as e:
        print(f"\nÉchec de connexion : {e}")
        sys.exit(1)

    name = profile.get("full_name") or profile.get("display_name") or "?"
    uid = profile.get("user_id", "?")
    print(f"\nConnecté : {name} (user_id={uid})")
    print(f"Tokens sauvegardés dans : {os.path.abspath(token_dir)}")
    print("\nLe serveur self_hosted utilisera ces tokens automatiquement.")
    print("Pour Vercel : connectez-vous via le formulaire Garmin dans l'interface web.")


if __name__ == "__main__":
    main()

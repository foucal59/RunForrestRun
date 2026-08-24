"""Profil deterministe et non personnel, partage par toute la suite de tests.

Le plan est GENERE depuis `runner_profile.PROFILE` a l'import de
`daily_training_plan`. Sans cadrage, la suite testerait un calendrier different
chaque jour (la date de course par defaut se cale sur aujourd'hui) et les tests
deviendraient irreproductibles.

Ce profil est donc pose ici, AVANT tout import de module applicatif : dates
fixes, objectif rond, aucun record — donc aucune donnee identifiante, et un jeu
d'allures que n'importe qui peut relire.
"""

import os

# ── Cadrage du plan ──
# 15 semaines numerotees + reprise, course un DIMANCHE, sortie longue le SAMEDI,
# qualite le MARDI, repos le LUNDI : le gabarit par defaut du generateur.
# `setdefault` et non affectation : la suite doit pouvoir tourner sur un AUTRE
# profil sans etre modifiee. C'est la preuve que les tests decrivent le
# generateur et non un plan particulier :
#
#   PLAN_WEEKS=12 PLAN_LONG_RUN_WEEKDAY=6 RUNNER_GOAL_TIME=3:45:00 pytest tests/
os.environ.setdefault("PLAN_RACE_NAME", "Marathon test")
os.environ.setdefault("PLAN_START_DATE", "2026-07-09")
os.environ.setdefault("PLAN_RACE_DATE", "2026-10-25")
os.environ.setdefault("PLAN_WEEKS", "15")
os.environ.setdefault("PLAN_TAPER_WEEKS", "3")
os.environ.setdefault("PLAN_LONG_RUN_WEEKDAY", "5")
os.environ.setdefault("PLAN_QUALITY_WEEKDAY", "1")
os.environ.setdefault("PLAN_REST_WEEKDAY", "0")

# ── Caracteristiques du coureur ──
# Un objectif rond plutot que des records : les allures derivees sont alors
# lisibles a l'oeil (4:37/km d'allure marathon) sans nommer personne.
os.environ.setdefault("RUNNER_GOAL_TIME", "3:15:00")
os.environ.setdefault("RUNNER_MAX_HR", "181")

# Le snapshot observe (ecrit par le backend depuis la base) ne doit jamais
# s'immiscer dans les tests : ils tourneraient differemment selon la machine.
os.environ["RUNNER_OBSERVED_FILE"] = os.path.join(
    os.path.dirname(__file__), "fixtures-observed-profile-absent.json"
)
os.environ["RUNNER_PROFILE_FILE"] = os.path.join(
    os.path.dirname(__file__), "fixtures-runner-profile-absent.json"
)

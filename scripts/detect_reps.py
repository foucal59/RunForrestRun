#!/usr/bin/env python3
"""Analyse fine d'un run a partir du FLUX (activity_streams), pas des laps.

Pourquoi : le bouton "lap" de la montre n'est souvent pas appuye, donc
activity_laps ne contient qu'un seul tour global -> les fractions (VO2, seuil,
AM) sont invisibles si on se fie aux laps. Ce script lit le flux point par point
(time_sec + velocity_smooth, et distance si dispo) et :
  - calcule les splits au km,
  - detecte les efforts rapides (reps) par seuil de vitesse + hysteresis,
  - affiche echauffement / reps / recups / retour au calme.

IMPORTANT (a retenir pour l'analyse coach) :
  * NE JAMAIS conclure "run regulier/modere" a partir de la seule allure MOYENNE.
    Une moyenne de 4:42/km sur 11 km est exactement ce que donne une VO2 6x400.
  * Si le plan prevoit des fractions et que activity_laps n'a qu'1 tour :
    c'est le bouton lap non presse, PAS une seance sautee. Lire le flux.
  * La distance du flux est parfois nulle (runs Garmin) : on la reconstruit en
    integrant la vitesse puis on la recale sur la distance reelle de l'activite.
    Le comptage de reps peut alors etre a +/-1 ; la STRUCTURE (allure des reps,
    cadence ~toutes les X min) reste fiable et suffit a valider la seance.

Usage :
  python3 scripts/detect_reps.py [--activity-id ID] [--sql CHEMIN] [--date AAAA-MM-JJ]
Par defaut : dernier run de la base locale .runtime/local-db/bdd_runs.sql
"""
import argparse
import io
import os
import sys

DEFAULT_SQL = os.path.join(os.path.dirname(__file__), '..', '.runtime', 'local-db', 'bdd_runs.sql')
TAB = '\t'
NULL = '\\N'
END = '\\.'


def f2(x):
    try:
        return float(x)
    except Exception:
        return None


def fpace(dist_m, dur_s):
    if not dist_m or dist_m <= 0 or not dur_s:
        return '-'
    spk = dur_s / (dist_m / 1000.0)
    m = int(spk // 60)
    s = int(round(spk % 60))
    if s == 60:
        m += 1
        s = 0
    return str(m) + ':' + ('0' + str(s))[-2:]


def fdur(sec):
    sec = int(round(sec or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return str(h) + 'h' + ('0' + str(m))[-2:] + ':' + ('0' + str(s))[-2:]
    return str(m) + ':' + ('0' + str(s))[-2:]


def parse(sql, activity_id=None, date=None):
    """Single streaming pass: pick target activity, then collect its stream."""
    acols = lcols = scols = None
    mode = None
    runs = {}
    target = str(activity_id) if activity_id else None
    stream_rows = []
    with io.open(sql, encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith('COPY public.activities ('):
                acols = [c.strip() for c in line[line.find('(') + 1:line.find(')')].split(',')]
                mode = 'act'
                continue
            if line.startswith('COPY public.activity_streams ('):
                scols = [c.strip() for c in line[line.find('(') + 1:line.find(')')].split(',')]
                # activities block is finished: resolve target now
                if target is None:
                    cand = list(runs.values())
                    if date:
                        cand = [r for r in cand if r.get('start_date_local', '')[:10] == date]
                    if cand:
                        target = max(cand, key=lambda r: r.get('start_date_local', '')).get('id')
                mode = 'stream'
                continue
            if line.startswith('COPY public.') and mode in ('act', 'stream'):
                mode = None
            if mode == 'act':
                if line.rstrip('\n') == END:
                    mode = None
                    continue
                parts = line.rstrip('\n').split(TAB)
                if len(parts) != len(acols):
                    continue
                row = dict(zip(acols, parts))
                if row.get('type') == 'Run':
                    runs[row.get('id')] = row
            elif mode == 'stream':
                if line.rstrip('\n') == END:
                    break
                if target and line.startswith(target + TAB):
                    parts = line.rstrip('\n').split(TAB)
                    if len(parts) == len(scols):
                        stream_rows.append(parts)
    return runs, target, scols, stream_rows


def build_series(scols, rows):
    ci = {c: i for i, c in enumerate(scols)}
    rows = sorted(rows, key=lambda r: int(r[ci['stream_index']]))
    T, V, Draw = [], [], []
    for r in rows:
        t = f2(r[ci['time_sec']])
        v = f2(r[ci['velocity_smooth']])
        d = f2(r[ci['distance']]) if 'distance' in ci else None
        if t is None:
            continue
        if v is None:
            v = 0.0
        v = max(0.0, min(6.5, v))  # clip GPS spikes (>6.5 m/s = plus vite qu'un 5k)
        T.append(t)
        V.append(v)
        Draw.append(d)
    n = len(T)
    have_dist = sum(1 for d in Draw if d is not None) > 0.5 * n
    if have_dist:
        # forward-fill missing distance
        D = []
        last = 0.0
        for d in Draw:
            if d is not None:
                last = d
            D.append(last)
    else:
        D = [0.0] * n
        for i in range(1, n):
            dt = T[i] - T[i - 1]
            if dt < 0 or dt > 30:
                dt = 1.0
            D[i] = D[i - 1] + 0.5 * (V[i] + V[i - 1]) * dt
    return T, V, D, have_dist


def km_splits(T, D):
    out = []
    km = 1
    prev_t = 0.0
    for i in range(1, len(D)):
        while D[i] >= km * 1000.0:
            frac = (km * 1000.0 - D[i - 1]) / (D[i] - D[i - 1]) if D[i] != D[i - 1] else 0
            tc = T[i - 1] + frac * (T[i] - T[i - 1])
            out.append((km, tc - prev_t))
            prev_t = tc
            km += 1
    return out


def detect_reps(T, V, D):
    n = len(T)
    W = 4
    Vs = [sum(V[max(0, i - W):min(n, i + W + 1)]) / (min(n, i + W + 1) - max(0, i - W)) for i in range(n)]
    HI, LO = 3.98, 3.72  # ~4:11/km enter, ~4:29/km exit
    segs = []
    inrep = False
    s = 0
    for i in range(n):
        if not inrep and Vs[i] >= HI:
            inrep = True
            s = i
        elif inrep and Vs[i] < LO:
            segs.append((s, i))
            inrep = False
    if inrep:
        segs.append((s, n - 1))
    reps = []
    for (a, b) in segs:
        dur = T[b] - T[a]
        dist = D[b] - D[a]
        if not (25 <= dur <= 240 and dist >= 150):
            continue
        if dist / dur < 3.85:  # allure moy du bloc > 4:20/km : ecarte les surges d'echauffement
            continue
        reps.append((a, b, dur, dist))
    return reps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--activity-id')
    ap.add_argument('--sql', default=DEFAULT_SQL)
    ap.add_argument('--date', help='AAAA-MM-JJ pour choisir le run de ce jour')
    args = ap.parse_args()

    runs, target, scols, rows = parse(args.sql, args.activity_id, args.date)
    if not target or not rows:
        print('Aucun flux trouve (target=%s, points=%d).' % (target, len(rows)), file=sys.stderr)
        sys.exit(2)
    r = runs.get(target, {})
    true_dist = f2(r.get('distance')) or 0.0
    T, V, D, have_dist = build_series(scols, rows)
    if not have_dist and D[-1] > 0 and true_dist > 0:
        scale = true_dist / D[-1]
        D = [d * scale for d in D]
        V = [v * scale for v in V]

    print('=== Run %s | %s | %s ===' % (target, r.get('source', '?'), r.get('name', '')))
    print('Date %s | %.2f km | %s | allure moy %s/km | source distance flux: %s'
          % (r.get('start_date_local', '')[:16], (true_dist or D[-1]) / 1000.0,
             fdur(f2(r.get('moving_time'))), fpace(true_dist or D[-1], f2(r.get('moving_time'))),
             'oui' if have_dist else 'reconstruite (integ. vitesse, recalee)'))
    ahr = r.get('average_heartrate')
    print('FC moy: %s' % ('absente (ceinture non captee)' if ahr in (None, NULL, '') else ahr))

    print('\n-- Splits au km --')
    for km, dt in km_splits(T, D):
        print('  km %d: %s' % (km, fpace(1000.0, dt)))

    reps = detect_reps(T, V, D)
    print('\n-- Efforts rapides detectes (heuristique flux, +/-1) : %d --' % len(reps))
    prev = None
    for k, (a, b, dur, dist) in enumerate(reps, 1):
        rec = '' if prev is None else ' | recup %ds' % round(T[a] - T[prev])
        print('  Rep %d: %.2f-%.2f km | %dm | %ds | %s/km%s'
              % (k, D[a] / 1000, D[b] / 1000, round(dist), round(dur), fpace(dist, dur), rec))
        prev = b
    if reps:
        md = sum(x[3] for x in reps) / len(reps)
        mt = sum(x[2] for x in reps) / len(reps)
        print('  Moyenne reps: %dm a %s/km' % (round(md), fpace(md, mt)))
        print('  Echauffement: %.2f km a %s/km | RC: %.2f km a %s/km'
              % (D[reps[0][0]] / 1000, fpace(D[reps[0][0]], T[reps[0][0]]),
                 (D[-1] - D[reps[-1][1]]) / 1000, fpace(D[-1] - D[reps[-1][1]], T[-1] - T[reps[-1][1]])))
        print('\nVERDICT: structure a intervalles -> seance de qualite (NE PAS lire comme un run regulier).')
    else:
        print('  Aucun bloc rapide net -> allure homogene (footing/endurance/SL), coherent avec une seance continue.')


if __name__ == '__main__':
    main()

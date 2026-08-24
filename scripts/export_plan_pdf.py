#!/usr/bin/env python3
"""Regenere public/training-plan.pdf DEPUIS le calendrier code.

Pourquoi un script plutot qu'un PDF ecrit a la main : un document telecharge
depuis le Cockpit qui contredit la page Plan est pire que pas de document du
tout. Le jour de la sortie longue, le pic de volume et les allures d'un plan
recopie a la main se perimen des le premier ajustement.

Le calendrier, les volumes et le nombre de sorties sont donc lus dans
`build_plan_overview()` — la meme fonction que le site — et les allures dans
`runner_profile.PROFILE`. Rien n'est recopie : seul le texte de methode est
redige ici, et il ne parle que de principes, jamais de chronos.

Aucun acces base : build_plan_overview() sans runs rend le plan nominal, ce qui
est exactement ce qu'un document imprimable doit montrer — la trame, pas les
adaptations du jour.

Usage : python3 scripts/export_plan_pdf.py [--out CHEMIN]
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from reportlab.lib import colors  # noqa: E402
    from reportlab.lib.enums import TA_CENTER  # noqa: E402
    from reportlab.lib.pagesizes import A4  # noqa: E402
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
    from reportlab.lib.units import mm  # noqa: E402
    from reportlab.platypus import (  # noqa: E402
        BaseDocTemplate,
        Frame,
        KeepTogether,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
except ModuleNotFoundError as exc:
    if exc.name != "reportlab" and not str(exc.name).startswith("reportlab."):
        raise
    REPORTLAB_IMPORT_ERROR = exc
    colors = None
else:
    REPORTLAB_IMPORT_ERROR = None

from daily_training_plan import PACE_REFS, RACE_DAY, build_plan_overview  # noqa: E402
from runner_profile import PROFILE, fmt_clock  # noqa: E402

TITLE = f"Plan {PROFILE.race_name} {PROFILE.race_date.year}"
AUTHOR = "Coach"
RUNNER = os.environ.get("PLAN_RUNNER_NAME", "")

DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Le role de chaque allure, en une ligne. Le libelle et la valeur viennent de
# PACE_REFS : seul le "a quoi ca sert" est redige ici.
PACE_ROLES = {
    "recovery": "Regeneration",
    "easy": "Volume aerobie facile",
    "steady": "Socle aerobie",
    "marathon": "Specificite course",
    "semi": "Rythme soutenu",
    "threshold": "Moteur du marathon",
    "vo2": "Plafond aerobie",
    "strides": "Foulee, sans fatigue",
}

GOAL_SOURCE_LABELS = {
    "configured": "objectif choisi",
    "projected_from_records": "projete depuis les records",
    "fallback": "valeur par defaut, a renseigner",
}

GOAL_SOURCE_EXPLAINS = {
    "configured": "Il vient du profil du coureur.",
    "projected_from_records": (
        "Il est projete depuis le meilleur record connu par la formule de Riegel, avec une marge "
        "de conversion : une projection brute suppose une endurance specifique deja acquise."
    ),
    "fallback": (
        "Aucun objectif ni record n'est renseigne : c'est une valeur d'attente. Renseigne "
        "`goalTime` dans runner_profile.json, ou laisse une premiere synchronisation Garmin "
        "remplir les records."
    ),
}


def _race_pace_offset(seconds: int) -> str:
    """Allure de depart : l'allure objectif ralentie de quelques secondes."""
    total = int(round(PROFILE.goal_pace + seconds))
    return f"{total // 60}:{total % 60:02d}/km"

if colors is not None:
    INK = colors.HexColor("#1c2733")
    MUTED = colors.HexColor("#5a6b7c")
    ACCENT = colors.HexColor("#c2410c")
    RULE = colors.HexColor("#d7dee5")
    BAND = colors.HexColor("#f1f5f9")

    # Les seances de qualite et les sorties longues se reperent d'un coup d'oeil.
    CATEGORY_TINT = {
        "quality": colors.HexColor("#fff1e7"),
        "long": colors.HexColor("#eaf3ff"),
        "race": colors.HexColor("#fde8e8"),
        "rest": colors.HexColor("#f8fafc"),
    }
else:
    INK = MUTED = ACCENT = RULE = BAND = None
    CATEGORY_TINT = {}


def require_reportlab():
    if REPORTLAB_IMPORT_ERROR is not None:
        raise RuntimeError(
            "La generation du PDF requiert reportlab (pip install reportlab)."
        ) from REPORTLAB_IMPORT_ERROR


def ascii_only(text: str) -> str:
    """Les polices de base ReportLab n'ont pas les accents ni les tirets longs."""
    swaps = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "·": "|", "…": "..."}
    for src, dst in swaps.items():
        text = text.replace(src, dst)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def styles():
    require_reportlab()
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold",
                             fontSize=16, leading=20, textColor=INK, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11.5, leading=15, textColor=ACCENT, spaceBefore=10, spaceAfter=5),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica",
                               fontSize=9, leading=12.6, textColor=INK, spaceAfter=5),
        "bullet": ParagraphStyle("bullet", parent=base["BodyText"], fontName="Helvetica",
                                 fontSize=9, leading=12.6, textColor=INK,
                                 leftIndent=9, bulletIndent=1, spaceAfter=3),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10.4, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8, leading=10.4, textColor=INK),
        "week": ParagraphStyle("week", fontName="Helvetica-Bold", fontSize=9.5, leading=12.5,
                               textColor=colors.white),
        "cover_title": ParagraphStyle("ct", fontName="Helvetica-Bold", fontSize=27, leading=32,
                                      alignment=TA_CENTER, textColor=INK),
        "cover_sub": ParagraphStyle("cs", fontName="Helvetica", fontSize=13, leading=18,
                                    alignment=TA_CENTER, textColor=MUTED),
        "note": ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=8, leading=11,
                               textColor=MUTED, spaceBefore=4),
    }


def bullets(items, st):
    return [Paragraph(ascii_only(text), st["bullet"], bulletText="•") for text in items]


# ── Donnees calculees ──

def week_rows(week):
    """Une ligne par jour, telle que la page Plan la montre."""
    rows = []
    for session in week["sessions"]:
        day = ascii_only(session["dayLabel"])
        if session["category"] == "rest":
            rows.append((day, "Repos", "", session["category"]))
            continue
        main = (session.get("structure") or {}).get("main") or ""
        effort = []
        if session.get("estimatedKm"):
            effort.append(f"~{session['estimatedKm']:g} km")
        if session.get("estimatedDuration"):
            effort.append(session["estimatedDuration"])
        if session.get("optional"):
            effort.append("optionnel")
        rows.append((day, f"{session['title']} - {main}" if main else session["title"],
                     " | ".join(effort), session["category"]))
    return rows


def volume_span(weeks):
    """Bornes reelles du plan, pour que la couverture ne mente jamais."""
    numbered = [w for w in weeks if w["index"] >= 1]
    peak = max(numbered, key=lambda w: w["estimatedKmMax"])
    low = min(numbered, key=lambda w: w["estimatedKmMax"])
    return low, peak


# ── Rendu ──

def build_story(overview, st):
    weeks = overview["weeks"]
    low, peak = volume_span(weeks)
    story = []

    goal_pace = PROFILE.pace("marathon")
    long_day = DAY_NAMES[PROFILE.long_run_weekday]
    records = " | ".join(
        f"{key.upper()} en {fmt_clock(value)}" for key, value in sorted(PROFILE.records.items())
    )

    # Couverture
    story.append(Spacer(1, 34 * mm))
    story.append(Paragraph("PLAN D'ENTRAINEMENT", st["cover_sub"]))
    story.append(Paragraph(ascii_only(PROFILE.race_name), st["cover_title"]))
    subtitle = PROFILE.race_date.strftime("%d/%m/%Y")
    if RUNNER:
        subtitle = f"{subtitle} - {RUNNER}"
    story.append(Paragraph(ascii_only(subtitle), st["cover_sub"]))
    story.append(Spacer(1, 12 * mm))

    facts = [
        ("Objectif", f"{PROFILE.goal_label} (allure {goal_pace})"),
        ("Origine de l'objectif", GOAL_SOURCE_LABELS.get(PROFILE.goal_source, PROFILE.goal_source)),
        ("Duree du plan", f"{PROFILE.plan_weeks} semaines - reprise puis S1 a S{PROFILE.plan_weeks}"),
        ("Volume", f"de ~{low['estimatedKmMax']} a ~{peak['estimatedKmMax']} km/sem "
                   f"(pic S{peak['index']}), jusqu'a 6 sorties/sem"),
        ("Sortie longue", f"le {long_day.upper()}, de {PROFILE.long_start_km} "
                          f"a {PROFILE.long_peak_km} km"),
        ("FC max de reference", f"{PROFILE.max_hr} bpm"),
    ]
    if records:
        facts.insert(2, ("Records de reference", records))
    table = Table([[Paragraph(ascii_only(k), st["cellb"]), Paragraph(ascii_only(v), st["cell"])]
                   for k, v in facts], colWidths=[52 * mm, 108 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("BACKGROUND", (0, 0), (0, -1), BAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(ascii_only(
        "Ce PDF est genere depuis daily_training_plan.py, le calendrier qui alimente le site. "
        "Il montre la trame nominale : la page Plan y ajoute les ajustements du coach et "
        "l'adaptation aux runs reellement courus."), st["note"]))
    story.append(PageBreak())

    # Methode
    story.append(Paragraph("1. Principe directeur", st["h2"]))
    story.append(Paragraph(ascii_only(
        "Un marathon ne se gagne pas sur la vitesse mais sur la duree : la capacite a tenir "
        f"l'allure objectif ({goal_pace}) quand les jambes sont deja fatiguees. Le plan construit "
        "donc d'abord l'endurance aerobie, puis la specificite de l'allure course, et garde "
        "l'intensite haute pour ce qu'elle est - un entretien, pas un objectif."), st["body"]))

    story.append(Paragraph("2. D'ou vient l'objectif", st["h2"]))
    story.append(Paragraph(ascii_only(
        f"L'objectif retenu est {PROFILE.goal_label}, soit {goal_pace}. "
        + GOAL_SOURCE_EXPLAINS.get(PROFILE.goal_source, "")
        + " Toutes les allures du plan en decoulent : ajuste l'objectif dans le profil du "
        "coureur et le plan entier se recalibre, sans toucher au code."), st["body"]))

    story.append(Paragraph("3. Cadre d'entrainement", st["h2"]))
    story.extend(bullets([
        f"Polarisation 80/20 : environ 80 % du volume en facile ({PROFILE.pace('easy')}), "
        "20 % en qualite. Piege recurrent : courir les footings trop vite - un footing reste "
        "conversationnel.",
        f"Sortie longue = pierre angulaire, ancree au {long_day.upper()}. Si une seance doit "
        "sauter, on sacrifie le footing de volume, puis la qualite - jamais la SL ni son bloc "
        "a allure marathon.",
        "6 sorties par semaine au maximum, avec un jour de repos ferme. Une semaine sur quatre "
        "est une decharge : c'est elle qui permet a la moyenne de monter.",
        "Volume : montee progressive, decharge toutes les 3-4 semaines, "
        f"pic a ~{peak['estimatedKmMax']} km en S{peak['index']} (qualite comprise).",
        "Deux seances structurantes maximum sur 7 jours : une qualite et une sortie longue.",
    ], st))

    story.append(Paragraph(f"4. Allures de reference (objectif {PROFILE.goal_label})", st["h2"]))
    # Les allures viennent du profil, jamais d'une table recopiee : un tableau
    # d'allures ecrit a la main dans un PDF se perime des le premier ajustement.
    paces = [
        (ref["label"], ref["pace"].replace("/km", ""), PACE_ROLES.get(ref["key"], ""))
        for ref in PACE_REFS
    ]
    head = [Paragraph(ascii_only(h), st["cellb"]) for h in ("Type de seance", "Allure (min/km)", "Role")]
    body = [[Paragraph(ascii_only(c), st["cell"]) for c in row] for row in paces]
    table = Table([head] + body, colWidths=[58 * mm, 52 * mm, 60 * mm], repeatRows=1)
    marathon_row = next(
        (index + 1 for index, ref in enumerate(PACE_REFS) if ref["key"] == "marathon"), 1
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, -2), 0.35, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, marathon_row), (-1, marathon_row), CATEGORY_TINT["quality"]),
    ]))
    story.append(table)
    story.append(Paragraph(ascii_only(
        f"Projections a cette forme : 10 km en {fmt_clock(PROFILE.projected('10k'))}, "
        f"semi en {fmt_clock(PROFILE.projected('semi'))}. "
        f"Cibles de FC calculees sur une FC max de {PROFILE.max_hr} bpm."), st["note"]))

    # Calendrier, lu dans le code
    story.append(Paragraph("6. Calendrier semaine par semaine", st["h2"]))
    story.append(Paragraph(ascii_only(
        f"SL = sortie longue | AM = allure marathon {PROFILE.pace_target('marathon')} | "
        "les kilometrages sont des estimations "
        "du plan, qualite comprise. Genere depuis le calendrier du site."), st["note"]))
    story.append(Spacer(1, 3 * mm))

    for week in weeks:
        km = (f"~{week['estimatedKmMin']}-{week['estimatedKmMax']}"
              if week["estimatedKmMin"] != week["estimatedKmMax"]
              else f"~{week['estimatedKmMax']}")
        days = (f"{week['plannedRunDaysMin']}-{week['plannedRunDaysMax']}"
                if week["plannedRunDaysMin"] != week["plannedRunDaysMax"]
                else f"{week['plannedRunDaysMax']}")
        header = Table(
            [[Paragraph(ascii_only(week["label"]), st["week"]),
              Paragraph(ascii_only(f"{week['phaseLabel']} | {km} km | {days} sorties"), st["week"])]],
            colWidths=[95 * mm, 75 * mm],
        )
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), INK),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))

        rows = week_rows(week)
        grid = Table(
            [[Paragraph(ascii_only(d), st["cellb"]),
              Paragraph(ascii_only(t), st["cell"]),
              Paragraph(ascii_only(e), st["cell"])] for d, t, e, _ in rows],
            colWidths=[26 * mm, 110 * mm, 34 * mm],
        )
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]
        for index, (_, _, _, category) in enumerate(rows):
            tint = CATEGORY_TINT.get(category)
            if tint is not None:
                style.append(("BACKGROUND", (0, index), (-1, index), tint))
        grid.setStyle(TableStyle(style))
        story.append(KeepTogether([header, grid, Spacer(1, 4.5 * mm)]))

    story.append(Paragraph("5. Jour J", st["h2"]))
    story.append(Paragraph(ascii_only(
        "Une seule regle porte la course : partir plus lentement que ce que les jambes "
        "autorisent. Le temps gagne sur les dix premiers kilometres est toujours repris avec "
        "interets sur les dix derniers."), st["body"]))
    story.extend(bullets([
        f"Premiers kilometres nettement freines, environ {_race_pace_offset(8)} : se sentir retenu.",
        f"Installer ensuite l'allure objectif ({goal_pace}), sans jamais l'attaquer plus vite.",
        "A partir du 30e km : tenir posture et cadence, puis accelerer legerement seulement si "
        "les sensations le permettent.",
        "Rien de nouveau le jour J : meme ravitaillement, meme materiel, meme petit-dejeuner "
        "que ceux rodes en sortie longue.",
    ], st))
    story.append(Paragraph(ascii_only(
        f"Passage a mi-course visee : {fmt_clock(PROFILE.goal_seconds // 2)} environ. "
        "C'est le semi test de la semaine de rodage qui arrete la cible definitive."), st["note"]))
    return story


def render(out_path, generated_for=None):
    overview = build_plan_overview(generated_for) if generated_for else build_plan_overview()
    st = styles()

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, ascii_only(f"{TITLE} - {RUNNER}"))
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canvas.restoreState()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title=TITLE, author=AUTHOR, subject=f"{PROFILE.race_name} {RACE_DAY.isoformat()}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="page")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=decorate)])
    doc.build(build_story(overview, st))
    return out_path, overview


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "public/training-plan.pdf"))
    ap.add_argument("--date", default=None, help="Jour de reference (defaut : aujourd'hui)")
    args = ap.parse_args()

    path, overview = render(args.out, args.date)
    low, peak = volume_span(overview["weeks"])
    print(
        f"[plan-pdf] {path} ecrit - {len(overview['weeks'])} semaines, "
        f"volume ~{low['estimatedKmMax']} a ~{peak['estimatedKmMax']} km/sem "
        f"(pic S{peak['index']}), {os.path.getsize(path)} octets",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

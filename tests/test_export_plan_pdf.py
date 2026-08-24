"""Le PDF telechargeable doit sortir du calendrier code, jamais d'une recopie.

Un PDF ecrit a la main se perime : il finit par annoncer un jour de sortie
longue, un pic de volume ou des allures que la page Plan contredit. Ces tests
figent la seule propriete qui empeche ca — chaque ligne du document vient de
`build_plan_overview()` et de `runner_profile.PROFILE`.

Aucune date n'est ecrite en dur ici : le calendrier est genere depuis le profil,
donc les assertions se lisent depuis le profil aussi. Un test qui figerait
« la SL du 22 aout fait 25 km » recreerait exactement le couplage a un plan
personnel que le generateur existe pour supprimer.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from daily_training_plan import PLAN_SHAPE, build_plan_overview
from runner_profile import PROFILE
from scripts.export_plan_pdf import ascii_only, volume_span, week_rows

HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None

DAY_ABBR = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")


class PlanPdfDataTests(unittest.TestCase):
    """Ces tests ne rendent aucun PDF : ils verifient la donnee qui l'alimente."""

    def setUp(self):
        self.overview = build_plan_overview()
        self.weeks = {week["index"]: week for week in self.overview["weeks"] if week["index"]}

    def test_every_week_renders_all_its_days(self):
        for week in self.overview["weeks"]:
            self.assertEqual(len(week_rows(week)), len(week["sessions"]), week["start"])

    def test_the_long_run_falls_on_the_configured_weekday(self):
        # C'est l'erreur exacte qu'un document recopie a la main finit par figer :
        # la SL affichee un autre jour que celui ou elle est reellement courue.
        long_abbr = DAY_ABBR[PROFILE.long_run_weekday]
        after_abbr = DAY_ABBR[(PROFILE.long_run_weekday + 1) % 7]
        for index, week in self.weeks.items():
            if week["phase"] == "race_week":
                continue
            rows = {day.split()[0]: category for day, _, _, category in week_rows(week)}
            self.assertEqual(rows.get(long_abbr), "long", f"S{index}")
            self.assertNotEqual(rows.get(after_abbr), "long", f"S{index}")

    def test_rest_days_render_without_an_effort_estimate(self):
        deload = min(PLAN_SHAPE["deloads"])
        rows = week_rows(self.weeks[deload])
        rest = [row for row in rows if row[3] == "rest"]
        self.assertTrue(rest, "une semaine de decharge doit rendre des jours")
        for _, title, effort, _ in rest:
            self.assertEqual(title, "Repos")
            self.assertEqual(effort, "")

    def test_volume_span_reports_the_real_peak_week(self):
        low, peak = volume_span(self.overview["weeks"])
        self.assertLess(low["estimatedKmMax"], peak["estimatedKmMax"])
        # Le pic tombe dans le bloc de construction, jamais dans l'affutage.
        self.assertLessEqual(peak["index"], PLAN_SHAPE["buildWeeks"])
        self.assertEqual(
            peak["estimatedKmMax"],
            max(week["estimatedKmMax"] for week in self.overview["weeks"]),
        )

    def test_a_deload_week_is_lighter_than_the_charge_week_before_it(self):
        for deload in sorted(PLAN_SHAPE["deloads"]):
            week = self.weeks[deload]
            previous = self.weeks.get(deload - 1)
            self.assertEqual((week["phase"], week["phaseLabel"]), ("deload", "Decharge"))
            if previous and previous["phase"] != "deload":
                self.assertLess(week["estimatedKmMax"], previous["estimatedKmMax"], f"S{deload}")
                self.assertLessEqual(
                    week["plannedRunDaysMax"], previous["plannedRunDaysMax"], f"S{deload}"
                )

    def test_a_deload_long_run_carries_no_marathon_pace_block(self):
        # Une decharge qui garde son bloc a allure marathon n'en est pas une.
        for deload in sorted(PLAN_SHAPE["deloads"]):
            long_session = next(
                session
                for session in self.weeks[deload]["sessions"]
                if session["category"] == "long"
            )
            self.assertNotIn("AM", long_session["title"], f"S{deload}")
            self.assertIn("sans bloc rapide", long_session["structure"]["main"], f"S{deload}")

    def test_the_taper_gives_back_volume_week_after_week(self):
        taper = [week for week in self.overview["weeks"] if week["phase"] == "taper"]
        self.assertTrue(taper, "un plan marathon a un affutage")
        volumes = [week["estimatedKmMax"] for week in taper]
        self.assertEqual(volumes, sorted(volumes, reverse=True))
        peak = max(week["estimatedKmMax"] for week in self.overview["weeks"])
        self.assertLess(volumes[-1], peak * 0.7)

    def test_accents_are_stripped_for_the_builtin_fonts(self):
        # Helvetica de base n'a ni accents ni tirets longs : sans ce filtre, le
        # PDF affiche des carres noirs.
        self.assertEqual(ascii_only("Spécifique — récupération"), "Specifique - recuperation")


@unittest.skipUnless(HAS_REPORTLAB, "reportlab absent : outil hors ligne, pas une dependance du site")
class PlanPdfRenderTests(unittest.TestCase):
    def test_render_writes_a_real_pdf(self):
        from scripts.export_plan_pdf import render

        out = Path(tempfile.mkdtemp()) / "plan.pdf"
        path, overview = render(out)

        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes()[:5], b"%PDF-")
        self.assertGreater(path.stat().st_size, 10_000)
        # Les semaines numerotees, plus la reprise.
        self.assertEqual(len(overview["weeks"]), PROFILE.plan_weeks + 1)


if __name__ == "__main__":
    unittest.main()

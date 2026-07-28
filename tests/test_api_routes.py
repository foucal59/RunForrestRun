import ast
import unittest
from pathlib import Path


class VercelApiRouteTests(unittest.TestCase):
    def test_vercel_app_exposes_plan_overview_route(self):
        source = Path(__file__).resolve().parents[1] / "api" / "app.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        routes = []
        imports_plan_builder = False
        imports_workout_builder = False

        for node in module.body:
            if isinstance(node, ast.ImportFrom) and node.module == "daily_training_plan":
                imports_plan_builder = any(alias.name == "build_plan_overview" for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module == "workout_builder":
                imports_workout_builder = any(alias.name == "build_garmin_workout" for alias in node.names)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"get", "post"}
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    continue
                routes.append((func.attr, decorator.args[0].value))

        self.assertTrue(imports_plan_builder)
        self.assertTrue(imports_workout_builder)
        self.assertIn(("get", "/api/data/plan-overview"), routes)
        self.assertIn(("post", "/api/data/workout-garmin"), routes)
        self.assertNotIn(("get", "/api/data/workout-fit"), routes)
        self.assertNotIn(("get", "/api/data/workout-tcx"), routes)


if __name__ == "__main__":
    unittest.main()

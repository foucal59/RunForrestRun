import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "export_local_db_sql.sh"


class AtomicSqlExportTests(unittest.TestCase):
    def _project_with_pg_dump(self, pg_dump_body: str):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        scripts = root / "scripts"
        fake_bin = root / "bin"
        scripts.mkdir()
        fake_bin.mkdir()
        shutil.copy2(EXPORT_SCRIPT, scripts / EXPORT_SCRIPT.name)

        pg_dump = fake_bin / "pg_dump"
        pg_dump.write_text("#!/bin/bash\n" + pg_dump_body, encoding="utf-8")
        pg_dump.chmod(pg_dump.stat().st_mode | stat.S_IXUSR)

        dump = root / ".runtime" / "local-db" / "bdd_runs.sql"
        dump.parent.mkdir(parents=True)
        dump.write_text("PREVIOUS VALID DUMP\n", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "LOCAL_DATABASE_URL": "postgresql://local/test",
            "LOCAL_DATABASE_SQL_PATH": str(dump),
        }
        return root, dump, env

    def test_failed_pg_dump_preserves_the_previous_dump(self):
        root, dump, env = self._project_with_pg_dump(
            "printf 'PARTIAL DUMP\\n'\nexit 7\n"
        )

        result = subprocess.run(
            ["/bin/bash", str(root / "scripts" / EXPORT_SCRIPT.name)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(dump.read_text(encoding="utf-8"), "PREVIOUS VALID DUMP\n")
        self.assertEqual(list(dump.parent.glob(".sql-export.*")), [])

    def test_successful_pg_dump_atomically_replaces_the_dump(self):
        root, dump, env = self._project_with_pg_dump(
            "printf 'NEW COMPLETE DUMP\\n'\n"
        )

        result = subprocess.run(
            ["/bin/bash", str(root / "scripts" / EXPORT_SCRIPT.name)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dump.read_text(encoding="utf-8"), "NEW COMPLETE DUMP\n")
        self.assertIn("dump written", result.stderr)
        self.assertEqual(list(dump.parent.glob(".sql-export.*")), [])


if __name__ == "__main__":
    unittest.main()

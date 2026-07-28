"""
DB shim — auto-selects the right adapter.

• SQLITE_PATH set  →  db_sqlite (sqlite3, local dev, no server needed)
• otherwise        →  database_pg (pg8000 + Neon PostgreSQL, production)
"""
import os

if os.environ.get("SQLITE_PATH"):
    from db_sqlite import *  # noqa: F401,F403
else:
    from database_pg import *  # noqa: F401,F403

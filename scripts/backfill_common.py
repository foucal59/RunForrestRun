"""Helpers partages par les scripts de backfill Garmin (sommeil, VO2max, ...).

Chaque script garde son propre STATE_PATH et sa logique metier; tout ce qui
est env/venv/dates/etat de reprise/rate-limit vit ici.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_TOKEN_DIR = PROJECT_ROOT / ".runtime" / "garminconnect"


def ensure_project_python() -> None:
    """Re-exec with the project virtualenv when required packages are missing."""
    required = ("garminconnect", "pg8000")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if not missing:
        return

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    current = Path(sys.executable).expanduser().absolute() if sys.executable else None
    if venv_python.exists() and current != venv_python.absolute():
        os.execv(str(venv_python), [str(venv_python), *sys.argv])

    missing_csv = ", ".join(missing)
    if venv_python.exists():
        raise SystemExit(
            "Missing Python packages with the current interpreter "
            f"({missing_csv}). Re-run with {venv_python} or let this script "
            "start from a shell where the project venv is active."
        )

    raise SystemExit(
        "Missing Python packages "
        f"({missing_csv}). Create the project virtualenv and install requirements first:\n"
        "python3 -m venv .venv\n"
        ".venv/bin/pip install -r requirements.txt"
    )


def load_env_var(env_path: Path, key: str) -> str:
    """Read one key from a .env file line by line (shell-friendly, like sibling scripts)."""
    if not env_path.exists():
        return ""
    value = ""
    with env_path.open() as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, _, current_value = line.partition("=")
            if current_key.strip() == key:
                value = current_value.strip().strip('"').strip("'")
    return value


def parse_day(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be YYYY-MM-DD, got: {value!r}") from exc


def parse_dateish(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def iso_today() -> str:
    return date.today().isoformat()


def resolve_target_database_url(env_path: Path, cli_value: str) -> str:
    if cli_value:
        return cli_value

    for key in ("DATABASE_URL_NEON", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            return value

    for key in ("DATABASE_URL_NEON", "DATABASE_URL"):
        value = load_env_var(env_path, key)
        if value:
            return value

    return ""


def sanitize_target_host(url: str) -> str:
    if "@" not in url:
        return "unknown-host"
    return url.split("@", 1)[1].split("/", 1)[0]


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise SystemExit(
            f"Unable to parse state file {path}. Delete it or use --reset-state. ({exc})"
        ) from exc


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def is_rate_limited_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return (
        "429" in text
        or "too many requests" in text
        or "rate limit" in text
        or "ratelimit" in text
    )

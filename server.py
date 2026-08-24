"""
Self-hosted FastAPI backend for the running dashboard.

Serves both API endpoints and the built frontend (dist/). In self-hosted mode,
the local PostgreSQL DB is the primary store and Neon is the best-effort
secondary replica. Garmin Connect is contacted only to top up the delta when
new runs appear.
"""
from __future__ import annotations
from datetime import date
import json
import os
import subprocess
import sys

# Load .env file if present (local dev)
_env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())
import time
import secrets
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import db
from database_convergence import synchronize_available_databases
from coach_mcp import create_http_app as create_coach_mcp_http_app
from coach_mcp import load_snapshot as load_coach_snapshot
from compat_api_logging import log_compatibility_api_usage
from daily_training_plan import (
    build_plan_overview,
    build_three_day_training_guidance,
    build_workout_export,
    normalize_recent_training_runs,
    set_plan_overrides,
)
from workout_builder import build_garmin_workout, is_workout_eligible
from posthog_client import get_client as _ph

# ── Shared utilities (reuse existing logic) ──

import threading

SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "garmin_session")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    os.environ["SESSION_SECRET"] = SESSION_SECRET
    print("[AUTH] SESSION_SECRET missing; generated ephemeral local secret", file=sys.stderr)
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", "2592000"))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
GARMIN_TOKEN_DIR = os.environ.get(
    "GARMIN_TOKEN_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime", "garminconnect"),
)
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",") if os.environ.get("ALLOWED_ORIGINS") else []
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()

PROJECT_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_DIR / ".runtime"
INCREMENTAL_SYNC_PID = RUNTIME_DIR / "incremental-sync.pid"
INCREMENTAL_SYNC_LOG = RUNTIME_DIR / "launch.log"
INCREMENTAL_SYNC_DEBOUNCE = int(
    os.environ.get("INCREMENTAL_SYNC_DEBOUNCE_SECONDS", "600")
)
_incremental_sync_lock = threading.Lock()
_incremental_sync_last_trigger = 0.0
_background_tasks: set[asyncio.Task] = set()


def _incremental_sync_urls() -> tuple[str, str] | None:
    neon_url = os.environ.get("DATABASE_URL_NEON", "").strip()
    local_url = os.environ.get("LOCAL_DATABASE_URL", "").strip()
    if not neon_url or not local_url or neon_url == local_url:
        return None
    return neon_url, local_url


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _launch_incremental_sync() -> bool:
    """Start one detached Neon/local convergence process after an UI opening."""
    global _incremental_sync_last_trigger
    urls = _incremental_sync_urls()
    if not urls:
        return False
    with _incremental_sync_lock:
        now = time.monotonic()
        if now - _incremental_sync_last_trigger < INCREMENTAL_SYNC_DEBOUNCE:
            print("[SYNC] incremental Neon/local ignoré (debounce)", file=sys.stderr)
            return False
        try:
            existing_pid = int(INCREMENTAL_SYNC_PID.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            existing_pid = 0
        if existing_pid and _pid_is_running(existing_pid):
            print(
                f"[SYNC] incremental Neon/local déjà en cours (pid {existing_pid})",
                file=sys.stderr,
            )
            return False

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["DATABASE_URL_NEON"], env["LOCAL_DATABASE_URL"] = urls
        env.setdefault("SYNC_DB_TIMEOUT", "120")
        with INCREMENTAL_SYNC_LOG.open("a") as log_file:
            process = subprocess.Popen(
                [sys.executable, str(PROJECT_DIR / "scripts" / "sync_neon_local.py")],
                cwd=str(PROJECT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        INCREMENTAL_SYNC_PID.write_text(str(process.pid))
        _incremental_sync_last_trigger = now
        print(
            "[SYNC] incremental Neon/local déclenché par ouverture UI "
            f"(pid {process.pid})",
            file=sys.stderr,
        )
        return True


def _schedule_incremental_sync() -> None:
    task = asyncio.create_task(asyncio.to_thread(_launch_incremental_sync))
    _background_tasks.add(task)

    def _finish(done: asyncio.Task) -> None:
        _background_tasks.discard(done)
        try:
            done.result()
        except Exception as exc:
            print(
                f"[SYNC] lancement incremental Neon/local échoué (non fatal): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    task.add_done_callback(_finish)

# ── Crypto helpers (from _utils.py) ──

import base64
import hmac
import hashlib


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    s = (s or "").strip()
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))


def _sign(value: str) -> str:
    if not SESSION_SECRET:
        raise ValueError("SESSION_SECRET not configured")
    mac = hmac.new(SESSION_SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(mac)


def encode_session(session: dict) -> str:
    payload = _b64url_encode(json.dumps(session, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = _sign(payload)
    return f"{payload}.{sig}"


def decode_session(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        payload, sig = raw.split(".", 1)
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        return json.loads(_b64url_decode(payload).decode("utf-8"))
    except Exception:
        return None


# ── Session helpers ──

def get_session(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    return decode_session(raw)


def set_session_cookie(response: Response, session: dict):
    response.set_cookie(
        key=SESSION_COOKIE,
        value=encode_session(session),
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=BASE_URL.startswith("https"),
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def _db_flow_status() -> dict:
    primary_url = os.environ.get("DATABASE_URL", "")
    local_url = os.environ.get("LOCAL_DATABASE_URL", "")
    neon_url = os.environ.get("DATABASE_URL_NEON", "")
    if os.environ.get("SQLITE_PATH"):
        primary = "sqlite"
    elif local_url and primary_url == local_url:
        primary = "local"
    elif primary_url:
        primary = "neon"
    else:
        primary = "unconfigured"
    secondary = "neon" if neon_url and neon_url != primary_url else None
    return {"mode": "self_hosted", "primary": primary, "secondary": secondary}


# ══════════════════════════════════════════════
# FastAPI App
# ══════════════════════════════════════════════

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "900"))  # 15 min default
coach_mcp_app = create_coach_mcp_http_app(path="/")


# ── App lifecycle ──

async def _startup_freshness_check():
    """Check Garmin after startup without blocking the API event loop."""
    try:
        import garmin_freshness
        result = await asyncio.to_thread(
            garmin_freshness.check_and_populate, GARMIN_TOKEN_DIR
        )
        print(f"[STARTUP] Garmin freshness: {result}", file=sys.stderr)
    except Exception as e:
        print(f"[STARTUP] Garmin freshness check error: {type(e).__name__}: {e}", file=sys.stderr)


async def _background_garmin_loop():
    """Sync de fond Garmin — garde la base à jour même UI fermée.

    Tout passe par Garmin Connect. check_and_populate est synchrone (réseau +
    DB) donc on l'exécute dans un thread pour ne pas bloquer l'event loop."""
    # Startup already runs a freshness check. Wait one full interval before the
    # first background pass so slow Garmin calls never overlap or duplicate work.
    await asyncio.sleep(SYNC_INTERVAL)
    while True:
        try:
            import garmin_freshness
            result = await asyncio.to_thread(
                garmin_freshness.check_and_populate, GARMIN_TOKEN_DIR
            )
            print(f"[SYNC] background Garmin: {result}", file=sys.stderr)
        except Exception as e:
            print(f"[SYNC] background Garmin error: {type(e).__name__}: {e}", file=sys.stderr)
        await asyncio.sleep(SYNC_INTERVAL)


async def _run_migrations():
    """Run DB migrations in background so server starts serving immediately."""
    try:
        await asyncio.to_thread(db.init_db_migrations)
    except Exception as e:
        print(f"[STARTUP] Migrations error (non-fatal): {type(e).__name__}: {e}", file=sys.stderr)


@asynccontextmanager
async def lifespan(app):
    async with coach_mcp_app.lifespan(app):
        # Startup — fast connectivity check off the event loop (a cold Neon can
        # take ~20s of connect retries; a thread keeps the loop responsive).
        await asyncio.to_thread(db.init_db)
        # A status-log count must never crash the whole backend: a missing table
        # (fresh DB before migrations/mirror) or a transient DB hiccup here would
        # otherwise abort lifespan startup and make every /api/* 500 via the proxy.
        try:
            count = await asyncio.to_thread(db.get_activity_count)
            print(f"[APP] Server ready. DB has {count} activities.", file=sys.stderr)
        except Exception as e:
            print(f"[APP] Server ready. activity count unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        migrations_task = asyncio.create_task(_run_migrations())
        freshness_task = asyncio.create_task(_startup_freshness_check())
        # Sync de fond : Garmin uniquement.
        sync_task = asyncio.create_task(_background_garmin_loop())
        try:
            yield
        finally:
            migrations_task.cancel()
            freshness_task.cancel()
            sync_task.cancel()
            for t in (migrations_task, freshness_task, sync_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            # Shutdown
            _ph().flush()
            print("[APP] Shutting down.", file=sys.stderr)


app = FastAPI(title="Garmin Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)

# CORS — for dev (localhost:5173) or custom origins
origins = ["http://localhost:5173", "http://localhost:8080"]
if ALLOWED_ORIGINS:
    origins.extend(ALLOWED_ORIGINS)
if BASE_URL:
    origins.append(BASE_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api/mcp", coach_mcp_app)


def _bearer_token(request: Request) -> str:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


@app.middleware("http")
async def require_session_for_private_data(request: Request, call_next):
    """Keep personal DB reads private and prevent anonymous DB egress."""
    path = request.url.path
    if path.startswith("/api/mcp") and request.method != "OPTIONS":
        if MCP_AUTH_TOKEN and not secrets.compare_digest(_bearer_token(request), MCP_AUTH_TOKEN):
            return JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
    if (path.startswith("/api/data/") or path == "/api/streams") and not get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def log_compatibility_routes(request: Request, call_next):
    """Measure legacy API usage before deciding whether routes can be removed."""
    return await log_compatibility_api_usage(request, call_next)


# ── Setup endpoints ──

@app.get("/api/setup/status")
async def setup_status():
    configured = bool(os.environ.get("DATABASE_URL") or os.environ.get("SQLITE_PATH"))
    try:
        count = db.get_activity_count()
        return JSONResponse({"configured": configured, "activities": count, "dbFlow": _db_flow_status()})
    except Exception as e:
        print(
            f"[setup/status] count failed, configured={configured}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return JSONResponse({
            "configured": configured,
            "activities": None,
            "error": f"{type(e).__name__}",
            "dbFlow": _db_flow_status(),
        })


@app.post("/api/setup/configure")
async def setup_configure(request: Request):
    """Écrit la configuration dans .env et met à jour les variables globales."""
    global SESSION_SECRET

    body = await request.json()
    # Générer un SESSION_SECRET si absent
    current_session_secret = os.environ.get("SESSION_SECRET", "")
    new_session_secret = current_session_secret or secrets.token_urlsafe(32)

    print("[Setup] saving configuration", file=sys.stderr)

    # Lire le .env existant ou partir d'un fichier vide
    env_path = Path(os.path.dirname(__file__)) / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    # Mettre à jour ou ajouter les clés
    updates = {
        "SESSION_SECRET": new_session_secret,
        "BASE_URL": os.environ.get("BASE_URL", "http://localhost:8080"),
    }
    if not os.environ.get("DATABASE_URL") and os.environ.get("LOCAL_DATABASE_URL"):
        updates["DATABASE_URL"] = os.environ["LOCAL_DATABASE_URL"]
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n")

    # Mettre à jour os.environ et les globals du module
    for key, value in updates.items():
        os.environ[key] = value
        if key == "DATABASE_URL" and hasattr(db, "DATABASE_URL"):
            db.DATABASE_URL = value

    SESSION_SECRET = new_session_secret

    print("[Setup] configuration saved and applied successfully", file=sys.stderr)
    return JSONResponse({"ok": True})


# ── Auth endpoints ──

def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def _session_from_garmin_profile(profile: dict) -> dict:
    full_name = profile.get("full_name") or profile.get("display_name") or ""
    parts = full_name.split(" ", 1)
    return {
        "v": 2,
        "source": "garmin",
        "athlete": {
            "id": profile.get("user_id", 0),
            "firstname": parts[0] if parts else "",
            "lastname": parts[1] if len(parts) > 1 else "",
            "profile": profile.get("profile_image", ""),
            "shoes": [],
        },
        "iat": int(time.time()),
    }


@app.post("/api/auth/garmin-login")
async def auth_garmin_login(request: Request):
    """Connexion Garmin Connect."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")

    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    mfa_code = (body.get("mfa_code") or "").strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email et mot de passe requis")

    import garmin_freshness
    try:
        profile = garmin_freshness.garmin_login(
            email,
            password,
            token_dir=GARMIN_TOKEN_DIR,
            mfa_code=mfa_code,
        )
    except garmin_freshness.GarminMFARequiredError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        print(f"[AUTH] Garmin login failed: {e}", file=sys.stderr)
        raise HTTPException(status_code=401, detail=f"Connexion Garmin échouée : {str(e)[:200]}")

    session = _session_from_garmin_profile(profile)
    response = JSONResponse({"ok": True, "athlete": session["athlete"]})
    set_session_cookie(response, session)
    print(f"[AUTH] Garmin login OK: user_id={profile.get('user_id')}", file=sys.stderr)
    distinct_id = str(profile.get("user_id", "anonymous"))
    _ph().capture(distinct_id, "user_logged_in", {"mfa_used": bool(mfa_code)})
    return response


@app.post("/api/auth/local-session")
async def auth_local_session(request: Request):
    """Restore a localhost session from the existing Garmin token store."""
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Connexion locale uniquement")

    import garmin_freshness
    profile = await asyncio.to_thread(garmin_freshness.load_garmin_profile, GARMIN_TOKEN_DIR)
    if not profile or not profile.get("user_id"):
        raise HTTPException(status_code=401, detail="Aucune session Garmin locale valide")

    session = _session_from_garmin_profile(profile)
    response = JSONResponse({"ok": True, "athlete": session["athlete"]})
    set_session_cookie(response, session)
    print(f"[AUTH] Local Garmin session restored: user_id={profile.get('user_id')}", file=sys.stderr)
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    session = get_session(request)
    if not session or not session.get("athlete"):
        return JSONResponse({"authenticated": False}, status_code=401)

    athlete = session.get("athlete", {})
    shoes = athlete.get("shoes", [])

    return JSONResponse({
        "authenticated": True,
        "athlete": {
            "id": athlete.get("id"),
            "firstname": athlete.get("firstname", ""),
            "lastname": athlete.get("lastname", ""),
            "profile": athlete.get("profile", ""),
            "shoes": shoes,
        },
    })


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    session = get_session(request)
    if session and session.get("athlete"):
        distinct_id = str(session["athlete"].get("id", "anonymous"))
        _ph().capture(distinct_id, "user_logged_out")
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


# ── Streams endpoint (DB-first, hydrate-on-miss from Garmin) ──

def _get_or_hydrate_streams(activity_id: int):
    """Délègue au helper partagé (DB-first, hydratation Garmin sur absence)."""
    import garmin_freshness
    return garmin_freshness.get_or_hydrate_streams(activity_id, GARMIN_TOKEN_DIR)


@app.get("/api/streams")
async def get_streams(request: Request, id: int = None):
    """Return streams from DB, hydrating from Garmin on a miss. 404 if unavailable."""
    if not id:
        raise HTTPException(400, "Missing id parameter")

    cached = _get_or_hydrate_streams(id)
    if not cached or not cached.get("streams"):
        raise HTTPException(404, "Streams not available for this activity")
    return JSONResponse(cached)


# ── DB-read endpoints (frontend reads from here) ──

@app.get("/api/data/activities")
async def data_activities(request: Request, since: str = "", before: str = "", limit: int = 0, offset: int = 0):
    """Return activities from the primary DB. Supports bounded ranges or paging."""
    if since or before:
        activities = db.get_activities_range(since, before)
        print(f"[data/activities] range since={since or '-'} before={before or '-'} → {len(activities)}", file=sys.stderr)
        return {"activities": activities, "count": len(activities), "total": len(activities), "partial": True}
    if limit > 0:
        activities, total = db.get_activities_page(limit, offset)
        print(f"[data/activities] page limit={limit} offset={offset} → {len(activities)}/{total}", file=sys.stderr)
        return {"activities": activities, "count": len(activities), "total": total}
    activities = db.get_all_activities()
    return {"activities": activities, "count": len(activities), "total": len(activities), "sync": db.get_sync_status()}


@app.get("/api/data/prs")
async def data_prs(request: Request):
    """Return computed PRs from the primary DB."""
    prs = db.get_computed_bests_bulk(["5k", "10k", "semi", "marathon"])
    return {"prs": prs}


@app.delete("/api/data/activities/{activity_id}")
async def delete_activity(activity_id: int, request: Request):
    """Delete an activity from the primary DB, then replicate best-effort."""
    session = get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    print(f"[DELETE] {activity_id}", file=sys.stderr)
    db.delete_activity(activity_id)
    distinct_id = str((session.get("athlete") or {}).get("id", "anonymous"))
    _ph().capture(distinct_id, "activity_deleted", {"activity_id": activity_id})
    return {"ok": True, "deleted_id": activity_id}


@app.get("/api/data/status")
async def data_status(request: Request):
    """Return sync status. Never 500 — return a safe default on transient DB error."""
    try:
        return db.get_sync_status()
    except Exception as e:
        print(f"[data/status] failed, returning default: {type(e).__name__}: {e}", file=sys.stderr)
        return {"activityCount": 0, "listComplete": True,
                "detailsComplete": True, "detailsRemaining": 0,
                "detailsCount": 0, "streamsComplete": True, "streamsRemaining": 0,
                "error": f"{type(e).__name__}"}


@app.get("/api/data/ready")
async def data_ready(request: Request):
    """Lightweight DB readiness probe (parity with the Vercel endpoint)."""
    return db.get_db_readiness()


@app.post("/api/data/sync")
async def data_sync(request: Request):
    """Explicit UI sync: Garmin first, then every available run database."""
    session = get_session(request)
    if not session or not session.get("athlete"):
        print("[data/sync] no session, skipping", file=sys.stderr)
        return JSONResponse(
            {"added": 0, "details_fetched": 0, "checked": False, "skipped": "no_session"},
        )

    import garmin_freshness
    try:
        result = await asyncio.to_thread(
            garmin_freshness.check_and_populate, GARMIN_TOKEN_DIR
        )
    except Exception as exc:
        print(f"[data/sync] Garmin failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(500, detail=f"{type(exc).__name__}: {str(exc)[:300]}")

    result["database_sync"] = await asyncio.to_thread(
        synchronize_available_databases
    )
    print(f"[data/sync] databases={result['database_sync']}", file=sys.stderr)
    if result.get("added", 0) > 0:
        distinct_id = str(session["athlete"].get("id", "anonymous"))
        _ph().capture(distinct_id, "garmin_sync_completed", {"activities_added": result["added"]})
    return JSONResponse(result)


@app.post("/api/data/freshness-check")
async def data_freshness_check(request: Request):
    """Freshness probe : vérifie Garmin Connect pour les nouveaux runs."""
    session = get_session(request)
    if not session or not session.get("athlete"):
        print("[freshness] no session, skipping", file=sys.stderr)
        return JSONResponse(
            {"added": 0, "details_fetched": 0, "checked": False, "skipped": "no_session"},
        )
    import garmin_freshness
    try:
        result = await asyncio.to_thread(
            garmin_freshness.check_and_populate, GARMIN_TOKEN_DIR
        )
    except Exception as e:
        print(f"[freshness] unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(500, detail=f"{type(e).__name__}: {str(e)[:300]}")
    if result.get("added", 0) > 0:
        distinct_id = str(session["athlete"].get("id", "anonymous"))
        _ph().capture(distinct_id, "garmin_sync_completed", {"activities_added": result["added"]})
    _schedule_incremental_sync()
    return JSONResponse(result)


@app.get("/api/health")
async def health():
    # Liveness only: start.sh uses this endpoint to decide whether the server
    # process is healthy. DB readiness has its own status endpoint and must not
    # make a live backend look dead during a transient DB lock or reconnect.
    return {"status": "ok", "dbFlow": _db_flow_status()}


@app.get("/api/data/streams/{activity_id}")
async def data_streams(activity_id: int, request: Request):
    """Return streams from DB, hydrating from Garmin on a miss. 404 if unavailable."""
    cached = _get_or_hydrate_streams(activity_id)
    if not cached or not cached.get("streams"):
        raise HTTPException(404, "Streams not available for this activity")
    return JSONResponse(cached)


@app.get("/api/data/shoes")
async def data_shoes(request: Request):
    shoes = db.get_all_gears()
    print(f"[data/shoes] Returning {len(shoes)} shoes", file=sys.stderr)
    return {"shoes": shoes}


@app.get("/api/data/gear")
async def data_gear_compat(request: Request):
    gear = db.get_all_gears()
    print(f"[data/gear] Returning {len(gear)} gear items", file=sys.stderr)
    return {"gear": gear, "count": len(gear)}


@app.get("/api/data/vo2max")
async def data_vo2max(request: Request):
    """VO2max evolution (Garmin), DB-only."""
    history = db.get_vo2max_history()
    latest = history[-1]["vo2max"] if history else None
    return {"history": history, "latest": latest, "count": len(history)}


@app.get("/api/data/training-status")
async def data_training_status(request: Request):
    """Garmin-native training status, DB-only (stored in sync_meta)."""
    meta = db.get_sync_meta() or {}
    ts = meta.get("training_status") or {"status": None}
    return ts


def _apply_coach_plan_overrides() -> dict:
    """Pose les ajustements du coach avant tout calcul de plan.

    Le calendrier marathon est fige dans le code : sans ce chargement, une
    decision du coach (table plan_overrides) resterait invisible sur le site.
    Lecture best-effort — une base indisponible ne doit pas casser le plan.
    """
    try:
        overrides = db.get_plan_overrides()
    except Exception as e:
        print(f"[plan-overrides] lecture impossible: {type(e).__name__}: {e}", file=sys.stderr)
        overrides = {}
    set_plan_overrides(overrides)
    if overrides:
        print(f"[plan-overrides] {len(overrides)} ajustement(s) coach appliques", file=sys.stderr)
    return overrides


@app.get("/api/data/daily-training")
async def data_daily_training(request: Request, day: str = ""):
    """Adaptive marathon guidance for today and the next seven days."""
    target_day = (day or date.today().isoformat())[:10]
    _apply_coach_plan_overrides()
    recent_runs = db.get_recent_runs_for_plan(target_day, days=90)[:10]
    latest_sleep = db.get_latest_sleep_score(target_day)
    return build_three_day_training_guidance(target_day, recent_runs, latest_sleep)


@app.post("/api/data/daily-training")
async def data_daily_training_from_recent_runs(request: Request):
    """Adaptive marathon guidance using the latest runs already loaded by the UI."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    target_day = str(payload.get("day") or date.today().isoformat())[:10]
    _apply_coach_plan_overrides()
    recent_runs = normalize_recent_training_runs(
        payload.get("recentRuns") or payload.get("recent_runs") or [],
        target_day,
    )
    if not recent_runs:
        recent_runs = db.get_recent_runs_for_plan(target_day, days=90)[:10]
    else:
        # Les runs envoyes par l'UI n'ont que les moyennes de l'activite : sans
        # les laps, un fractionne courru en cote passe pour un footing et la
        # seance cle est decalee a tort. Meme structure que la lecture DB.
        attach = getattr(db, "attach_plan_run_structure", None)
        if attach is not None:
            recent_runs = attach(recent_runs)
    latest_sleep = db.get_latest_sleep_score(target_day)
    return build_three_day_training_guidance(target_day, recent_runs, latest_sleep)


@app.get("/api/data/plan-overview")
async def data_plan_overview(request: Request, day: str = ""):
    """Full marathon plan, week by week, with detailed session targets."""
    target_day = (day or date.today().isoformat())[:10]
    _apply_coach_plan_overrides()
    # La page Plan doit raconter la meme chose que le cockpit : le calendrier
    # sert de structure, les runs reellement enregistres priment.
    recent_runs = db.get_recent_runs_for_plan(target_day, days=90)[:10]
    overview = build_plan_overview(target_day, recent_runs)
    adjusted = sum(1 for w in overview["weeks"] for s in w["sessions"] if s["adjusted"])
    print(
        f"[plan-overview] {len(overview['weeks'])} weeks, generated for "
        f"{overview['generatedFor']}, {adjusted} adjusted session(s)",
        file=sys.stderr,
    )
    return overview


def _upload_garmin_workout_export(export: dict) -> tuple[dict, dict]:
    import garmin_freshness

    api = garmin_freshness.load_garmin_api(GARMIN_TOKEN_DIR)
    if api is None:
        raise PermissionError("Garmin Connect n'est pas authentifie. Reconnecte Garmin puis reessaie.")

    payload = build_garmin_workout(
        export["structure"],
        title=f"{export['date']} - {export['title']}",
        category=export["category"],
        est_minutes=export.get("estimatedMinutes"),
    )
    result = api.upload_workout(payload)
    if not isinstance(result, dict):
        result = {"raw": result}
    return payload, result


def _coerce_workout_export_payload(target_day: str, payload: dict) -> dict | None:
    workout = payload.get("workout") if isinstance(payload, dict) else None
    if not isinstance(workout, dict):
        return None
    structure = workout.get("structure") or workout.get("session")
    category = workout.get("category")
    if not isinstance(structure, dict) or not is_workout_eligible(category):
        return None
    try:
        estimated_minutes = int(workout.get("estimatedMinutes") or 45)
    except (TypeError, ValueError):
        estimated_minutes = 45
    title = str(workout.get("title") or "Seance").split(" · ", 1)[0].strip() or "Seance"
    return {
        "date": target_day,
        "title": title,
        "category": category,
        "tag": workout.get("tag"),
        "structure": {
            "warmup": str(structure.get("warmup") or ""),
            "main": str(structure.get("main") or ""),
            "cooldown": str(structure.get("cooldown") or ""),
        },
        "estimatedKm": workout.get("estimatedKm"),
        "estimatedMinutes": estimated_minutes,
    }


@app.post("/api/data/workout-garmin")
async def data_workout_garmin(request: Request, day: str = ""):
    """Create a structured workout directly in Garmin Connect."""
    target_day = (day or date.today().isoformat())[:10]
    try:
        body = await request.json()
    except Exception:
        body = {}
    _apply_coach_plan_overrides()
    export = _coerce_workout_export_payload(target_day, body) or build_workout_export(target_day)
    if export is None:
        raise HTTPException(404, "Pas de seance Garmin structuree pour ce jour.")
    try:
        payload, result = await asyncio.to_thread(_upload_garmin_workout_export, export)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:
        print(f"[workout-garmin] upload failed for {target_day}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(502, f"Upload Garmin impossible : {str(exc)[:200]}") from exc

    workout_id = result.get("workoutId")
    workout_name = result.get("workoutName") or payload["workoutName"]
    session = get_session(request) or {}
    athlete = session.get("athlete") or {}
    _ph().capture(str(athlete.get("id", "anonymous")), "workout_garmin_uploaded", {
        "date": export["date"],
        "category": export["category"],
        "workout_id": workout_id,
    })
    print(f"[workout-garmin] {target_day} -> {workout_id or '?'} ({export['category']})", file=sys.stderr)
    return {
        "ok": True,
        "date": export["date"],
        "title": export["title"],
        "category": export["category"],
        "workoutId": workout_id,
        "workoutName": workout_name,
    }


# ── Coach snapshot (public, read-only, no DB) ──

@app.get("/api/coach/journal")
async def coach_journal():
    """Latest coach snapshot used by the MCP server."""
    try:
        return JSONResponse(load_coach_snapshot())
    except FileNotFoundError:
        raise HTTPException(404, "Snapshot coach indisponible")


# ── SPA: Serve frontend from dist/ ──

DIST_DIR = Path(__file__).parent / "dist"

if DIST_DIR.exists():
    # Serve static assets (JS, CSS, images, wasm)
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    # Serve files from dist root (favicon, etc.)
    @app.get("/favicon.ico")
    async def serve_favicon():
        fav = DIST_DIR / "favicon.ico"
        if fav.exists():
            return FileResponse(fav)
        return Response(status_code=404)

    # SPA fallback: all non-API routes serve index.html
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        # Try to serve static file first
        file_path = DIST_DIR / path
        if file_path.is_file() and ".." not in path:
            return FileResponse(file_path)
        # Otherwise serve index.html (SPA routing)
        return FileResponse(DIST_DIR / "index.html")
else:
    @app.get("/")
    async def no_dist():
        return {"error": "Frontend not built. Run: npm run build"}

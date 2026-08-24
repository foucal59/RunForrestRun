"""
Vercel serverless entry point.
DB-only mode: the Neon PostgreSQL DB is the single source of truth.
The API serves data from Neon only; Garmin Connect is contacted solely for
login and the freshness top-up.
"""
from __future__ import annotations
from datetime import date
import asyncio
import json
import os
import sys
import time
import secrets
import traceback
from pathlib import Path

import threading
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path for imports (Vercel runs from api/ subdirectory)
sys.path.insert(0, str(Path(__file__).parent.parent))

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

# ── Constants ──

SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "garmin_session")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", "2592000"))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
GARMIN_TOKEN_DIR = os.environ.get("GARMIN_TOKEN_DIR", "")  # vide = tokens dans Neon sync_meta
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",") if os.environ.get("ALLOWED_ORIGINS") else []
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()

# ── Crypto / session helpers ──

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


# ── FastAPI App ──

coach_mcp_app = create_coach_mcp_http_app(path="/")
app = FastAPI(
    title="Garmin Dashboard (Vercel, DB-only)",
    docs_url=None,
    redoc_url=None,
    lifespan=coach_mcp_app.lifespan,
)

origins = [
    "http://localhost:8080",
    "http://localhost:5173",
]
if ALLOWED_ORIGINS:
    origins.extend(ALLOWED_ORIGINS)
if BASE_URL and BASE_URL not in origins:
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
    """Keep personal DB reads private and prevent anonymous Neon egress."""
    path = request.url.path
    if path.startswith("/api/mcp") and request.method != "OPTIONS":
        if not MCP_AUTH_TOKEN:
            if os.environ.get("VERCEL"):
                return JSONResponse({"detail": "MCP_AUTH_TOKEN not configured"}, status_code=503)
        elif not secrets.compare_digest(_bearer_token(request), MCP_AUTH_TOKEN):
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log full traceback for every unhandled 500 so it appears in Vercel runtime logs."""
    tb = traceback.format_exc()
    print(f"[500] {request.method} {request.url.path} → {type(exc).__name__}: {exc}\n{tb}", file=sys.stderr)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# Lazy DB init — nothing at module load (Neon cold start can take 10s+).
# _safe_conn() handles connection lazily on first query.
# Migrations run once after the first successful data request.
_migrations_lock = threading.Lock()
_migrations_done = False

def _run_migrations_once():
    """Run schema migrations once, in background, after first successful query."""
    global _migrations_done
    if _migrations_done:
        return
    with _migrations_lock:
        if _migrations_done:
            return
        _migrations_done = True
    def _bg():
        global _migrations_done
        try:
            db.init_db_migrations()
            print("[APP] Migrations done.", file=sys.stderr)
        except Exception as e:
            print(f"[APP] Migrations error (non-fatal): {e}", file=sys.stderr)
            with _migrations_lock:
                _migrations_done = False
    threading.Thread(target=_bg, daemon=True).start()


# ── Auth endpoints ──

@app.post("/api/auth/garmin-login")
async def auth_garmin_login(request: Request):
    """Connexion Garmin Connect — remplace l'OAuth Strava."""
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

    full_name = profile.get("full_name") or profile.get("display_name") or ""
    parts = full_name.split(" ", 1)
    session = {
        "v": 2,
        "source": "garmin",
        "athlete": {
            "id": profile.get("user_id", 0),
            "firstname": parts[0] if parts else "",
            "lastname": parts[1] if len(parts) > 1 else "",
            "profile": profile.get("profile_image", ""),
        },
        "iat": int(time.time()),
    }
    response = JSONResponse({"ok": True, "athlete": session["athlete"]})
    set_session_cookie(response, session)
    print(f"[AUTH] Garmin login OK: user_id={profile.get('user_id')} name={full_name!r}", file=sys.stderr)
    distinct_id = str(profile.get("user_id", "anonymous"))
    _ph().capture(distinct_id, "user_logged_in", {"mfa_used": bool(mfa_code)})
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the logged-in athlete. Profile data comes from Neon."""
    session = get_session(request)
    if not session or not session.get("athlete"):
        return JSONResponse({"authenticated": False}, status_code=401)

    session_athlete = session.get("athlete", {})
    db_athlete = None
    try:
        db_athlete = db.get_athlete()
    except Exception as e:
        print(f"[auth/me] db.get_athlete failed: {e}", file=sys.stderr)

    # Prefer DB athlete record if available
    athlete = db_athlete or {}
    shoes = []
    try:
        shoes = db.get_all_gears()
    except Exception as e:
        print(f"[auth/me] db.get_all_gears failed: {e}", file=sys.stderr)

    return JSONResponse({
        "authenticated": True,
        "athlete": {
            "id": athlete.get("id") or session_athlete.get("id"),
            "firstname": athlete.get("firstname") or session_athlete.get("firstname", ""),
            "lastname": athlete.get("lastname") or session_athlete.get("lastname", ""),
            "profile": athlete.get("profile") or session_athlete.get("profile", ""),
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


# ── Data endpoints (DB-only, no Strava calls) ──

@app.get("/api/data/activities")
async def data_activities(request: Request, since: str = "", before: str = "", limit: int = 0, offset: int = 0):
    """Return activities from Neon.

    - ?since=YYYY-MM-DD and/or ?before=YYYY-MM-DD → bounded date segment (fast, no
      COUNT). Used by progressive windowed loading to fetch small slices that never
      hit the serverless gateway timeout.
    - ?limit=N&offset=M → pagination fallback.
    - no params → full load + sync status (legacy; large DBs should window instead).
    """
    if since or before:
        try:
            activities = db.get_activities_range(since, before)
        except (TimeoutError, OSError) as e:
            print(f"[data/activities] DB timeout range since={since} before={before}: {type(e).__name__}", file=sys.stderr)
            return JSONResponse(status_code=503, content={"detail": f"DB timeout: {type(e).__name__}", "retry_after": 10})
        except Exception as e:
            if "timeout" in str(e).lower() or "connection" in str(e).lower():
                print(f"[data/activities] DB connection error range: {type(e).__name__}: {e}", file=sys.stderr)
                return JSONResponse(status_code=503, content={"detail": f"{type(e).__name__}", "retry_after": 10})
            raise
        _run_migrations_once()
        print(f"[data/activities] range since={since or '-'} before={before or '-'} → {len(activities)}", file=sys.stderr)
        return {"activities": activities, "count": len(activities), "total": len(activities), "partial": True}
    if limit > 0:
        activities, total = db.get_activities_page(limit, offset)
        print(f"[data/activities] page limit={limit} offset={offset} → {len(activities)}/{total}", file=sys.stderr)
        return {"activities": activities, "count": len(activities), "total": total}
    print("[data/activities] Reading all from DB", file=sys.stderr)
    activities = db.get_all_activities()
    status = db.get_sync_status()
    print(f"[data/activities] Returning {len(activities)} activities", file=sys.stderr)
    return {"activities": activities, "count": len(activities), "total": len(activities), "sync": status}


@app.get("/api/data/prs")
async def data_prs(request: Request):
    print("[data/prs] Reading computed PRs from DB", file=sys.stderr)
    prs = db.get_computed_bests_bulk(["5k", "10k", "semi", "marathon"])
    _run_migrations_once()
    print(f"[data/prs] Returning PRs: {[f'{k}:{len(v)}' for k, v in prs.items()]}", file=sys.stderr)
    return {"prs": prs}


@app.delete("/api/data/activities/{activity_id}")
def delete_activity(activity_id: int, request: Request):
    """Delete an activity from the DB.

    Sync `def` on purpose: FastAPI runs it in a worker thread (own thread-local
    DB connection), so the delete is not stuck behind the freshness-check probe
    that does blocking I/O on the event loop — that contention left the DELETE
    hanging with no response on Vercel.
    """
    session = get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    print(f"[DELETE] Deleting activity {activity_id} from DB", file=sys.stderr)
    db.delete_activity(activity_id)
    distinct_id = str((session.get("athlete") or {}).get("id", "anonymous"))
    _ph().capture(distinct_id, "activity_deleted", {"activity_id": activity_id})
    return {"ok": True, "deleted_id": activity_id}


@app.get("/api/data/gear")
async def data_gear(request: Request):
    """Read all gear items (shoes) from DB."""
    print("[data/gear] Reading gear from DB", file=sys.stderr)
    gear = db.get_all_gears()
    print(f"[data/gear] Returning {len(gear)} gear items", file=sys.stderr)
    return {"gear": gear, "count": len(gear)}


@app.get("/api/data/shoes")
async def data_shoes(request: Request):
    """Backwards-compat alias for /api/data/gear."""
    print("[data/shoes] Reading shoes from DB", file=sys.stderr)
    shoes = db.get_all_gears()
    print(f"[data/shoes] Returning {len(shoes)} shoes", file=sys.stderr)
    return {"shoes": shoes}


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


@app.get("/api/data/activities/{activity_id}/splits")
async def data_activity_splits(activity_id: int, request: Request):
    """Return splits for a single activity from DB."""
    splits = db.get_activity_splits(activity_id)
    return {"splits": splits}


@app.get("/api/data/activities/{activity_id}/laps")
async def data_activity_laps(activity_id: int, request: Request):
    """Return laps for a single activity from DB."""
    laps = db.get_activity_laps(activity_id)
    return {"laps": laps}


@app.get("/api/data/athlete-zones")
async def data_athlete_zones(request: Request):
    """Return HR zones from DB."""
    zones = db.get_athlete_zones()
    return {"zones": zones}


@app.get("/api/data/athlete")
async def data_athlete(request: Request):
    """Return athlete profile + stats from DB."""
    athlete = db.get_athlete()
    stats = db.get_athlete_stats()
    return {"athlete": athlete, "stats": stats}


@app.get("/api/data/status")
async def data_status(request: Request):
    """Get sync status from DB. Never 500 — a transient DB error here must not
    break the dashboard; return a safe default that hides the backfill banner."""
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
    """Lightweight DB readiness probe (no Strava calls)."""
    print("[data/ready] Checking DB readiness", file=sys.stderr)
    readiness = db.get_db_readiness()
    print(f"[data/ready] ready={readiness['ready']} reason={readiness['reason']}", file=sys.stderr)
    return readiness


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

    # The explicit button waits for convergence. This is deliberately separate
    # from the lightweight on-open freshness endpoint below.
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
    """On-open freshness probe (Vercel). Vérifie Garmin Connect pour les nouveaux runs."""
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
    return JSONResponse(result)


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

    print(f"[streams] Reading streams for activity {id} from DB", file=sys.stderr)
    cached = _get_or_hydrate_streams(id)
    if not cached or not cached.get("streams"):
        raise HTTPException(404, "Streams not available for this activity")
    return JSONResponse(cached)


@app.get("/api/data/streams/{activity_id}")
async def data_streams(activity_id: int, request: Request):
    """Alias kept for newer frontend code."""
    cached = _get_or_hydrate_streams(activity_id)
    if not cached or not cached.get("streams"):
        raise HTTPException(404, "Streams not available for this activity")
    return JSONResponse(cached)


# ── Setup status (so the frontend setup gate keeps working) ──

@app.get("/api/setup/status")
async def setup_status():
    """Quick check used by the frontend to know if the app is ready to serve.

    `configured` reflects whether the deployment is wired up (DATABASE_URL set),
    NOT whether a query just succeeded — otherwise a Neon cold start would bounce
    the user to the setup wizard mid-session. The activity count is best-effort.
    """
    configured = bool(os.environ.get("DATABASE_URL") or os.environ.get("SQLITE_PATH"))
    try:
        count = db.get_activity_count()
        return {"configured": configured, "activities": count}
    except Exception as e:
        print(f"[setup/status] count failed (DB cold?), configured={configured}: {type(e).__name__}: {e}", file=sys.stderr)
        return {"configured": configured, "activities": None, "error": f"{type(e).__name__}"}


# ── Health endpoint ──

@app.get("/api/health")
async def health():
    return {"status": "ok", "activities": db.get_activity_count()}


# ── Coach snapshot (public, read-only, no Neon) ──
# Servi depuis un JSON statique régénéré chaque matin par scripts/coach_journal.py
# et committé dans le repo (→ redeploy Vercel). Lu par le serveur MCP coach.

@app.get("/api/coach/journal")
async def coach_journal():
    """Dernier snapshot coach (7 runs analysés + séance du jour + projection). Public, sans Neon."""
    try:
        return JSONResponse(load_coach_snapshot())
    except FileNotFoundError:
        raise HTTPException(404, "Snapshot coach indisponible")

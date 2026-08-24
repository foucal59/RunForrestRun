"""Structured, privacy-conscious logging for deprecated compatibility routes."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlsplit


_EXACT_ROUTES = {
    "/api/streams": "/api/streams",
    "/api/data/athlete-zones": "/api/data/athlete-zones",
    "/api/data/athlete": "/api/data/athlete",
}
_ACTIVITY_ROUTE_PREFIX = "/api/data/activities/"
_ACTIVITY_ROUTE_SUFFIXES = {
    "/splits": "/api/data/activities/{activity_id}/splits",
    "/laps": "/api/data/activities/{activity_id}/laps",
}


def compatibility_route_template(path: str) -> str | None:
    """Return the normalized compatibility route, without personal identifiers."""
    normalized = (path or "").rstrip("/") or "/"
    exact = _EXACT_ROUTES.get(normalized)
    if exact:
        return exact

    if not normalized.startswith(_ACTIVITY_ROUTE_PREFIX):
        return None
    remainder = normalized[len(_ACTIVITY_ROUTE_PREFIX):]
    for suffix, template in _ACTIVITY_ROUTE_SUFFIXES.items():
        if not remainder.endswith(suffix):
            continue
        activity_id = remainder[:-len(suffix)]
        if activity_id and "/" not in activity_id:
            return template
    return None


def _source_origin(headers) -> str:
    """Keep only the origin of a caller-provided URL, never its path or query."""
    raw = headers.get("origin") or headers.get("referer") or ""
    if not raw:
        return "-"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "invalid"
    if not parsed.scheme or not parsed.netloc:
        return "invalid"
    return f"{parsed.scheme}://{parsed.netloc}"[:200]


async def log_compatibility_api_usage(request, call_next):
    """ASGI middleware helper used by both local and Vercel backends."""
    route = compatibility_route_template(request.url.path)
    if route is None:
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _write_log(request, route, 500, started)
        raise
    _write_log(request, route, response.status_code, started)
    return response


def _write_log(request, route: str, status_code: int, started: float) -> None:
    entry = {
        "event": "compat_api_call",
        "runtime": "vercel" if os.environ.get("VERCEL") else "local",
        "method": request.method,
        "route": route,
        "status": int(status_code),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "source_origin": _source_origin(request.headers),
        "fetch_site": (request.headers.get("sec-fetch-site") or "-")[:40],
        "user_agent": (request.headers.get("user-agent") or "-")[:160],
    }
    print(
        f"[COMPAT-API] {json.dumps(entry, ensure_ascii=False, separators=(',', ':'))}",
        file=sys.stderr,
        flush=True,
    )

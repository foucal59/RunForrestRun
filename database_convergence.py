"""Manual convergence of every configured run database.

Garmin writes always land in the active primary first. This module is the
second half of an explicit UI sync: if both Neon and local PostgreSQL are
configured, it runs the existing bidirectional incremental reconciler and
returns a small, credential-free status payload for the frontend.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse


def _database_label(url: str, *, local_url: str, neon_url: str) -> str:
    if url and local_url and url == local_url:
        return "local"
    if url and neon_url and url == neon_url:
        return "neon"
    return "primary"


def _configured_databases() -> tuple[list[str], str, str]:
    """Return public labels plus the two URLs understood by the reconciler."""
    if os.environ.get("SQLITE_PATH"):
        return ["sqlite"], "", ""

    primary_url = os.environ.get("DATABASE_URL", "").strip()
    local_url = os.environ.get("LOCAL_DATABASE_URL", "").strip()
    neon_url = os.environ.get("DATABASE_URL_NEON", "").strip()
    if not neon_url and primary_url and primary_url != local_url:
        neon_url = primary_url

    labels: list[str] = []
    seen: set[str] = set()
    for url in (primary_url, neon_url, local_url):
        if not url or url in seen:
            continue
        seen.add(url)
        labels.append(
            _database_label(url, local_url=local_url, neon_url=neon_url)
        )
    return labels, neon_url, local_url


def synchronize_available_databases() -> dict:
    """Converge all configured and reachable run stores.

    A single configured database is already synchronized after the Garmin
    import. With two PostgreSQL stores, the incremental reconciler exchanges
    missing/newer runs, their child rows, metadata and tombstones both ways.
    Replica failures stay non-destructive: the primary Garmin write remains
    committed and the response tells the UI which database needs a retry.
    """
    labels, neon_url, local_url = _configured_databases()
    if len(labels) <= 1 or not neon_url or not local_url or neon_url == local_url:
        print(
            f"[SYNC-ALL] single database synchronized: {labels or ['unconfigured']}",
            file=sys.stderr,
        )
        return {
            "ok": bool(labels),
            "configured": labels,
            "synchronized": labels,
            "mode": "single_database" if labels else "unconfigured",
        }

    timeout = int(os.environ.get("MANUAL_SYNC_DB_TIMEOUT", "10"))
    try:
        from scripts.sync_neon_local import main as converge_neon_local

        print(
            f"[SYNC-ALL] manual convergence requested for {labels}",
            file=sys.stderr,
        )
        code = converge_neon_local(
            neon_url=neon_url,
            local_url=local_url,
            dry_run=False,
            prepare_only=False,
            connection_timeout=timeout,
        )
        if code != 0:
            raise RuntimeError(f"incremental sync exited with status {code}")
        return {
            "ok": True,
            "configured": labels,
            "synchronized": labels,
            "mode": "converged",
        }
    except Exception as exc:
        # Do not expose URLs or credentials. A hostname is useful in server logs
        # only; the browser receives the exception type and public DB labels.
        failed_host = urlparse(local_url).hostname or "local"
        print(
            f"[SYNC-ALL] convergence failed for {failed_host} (non-fatal): "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        primary_url = os.environ.get("DATABASE_URL", "").strip()
        primary_label = _database_label(
            primary_url, local_url=local_url, neon_url=neon_url
        )
        synchronized = [primary_label] if primary_url else []
        return {
            "ok": False,
            "configured": labels,
            "synchronized": synchronized,
            "mode": "partial",
            "error": type(exc).__name__,
        }

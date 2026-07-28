"""Shared PostHog client — instance-based API.

Import `posthog_client` wherever you need to capture events.
The client is lazily initialized from environment variables on first use.
"""
from __future__ import annotations
import atexit
import os
from posthog import Posthog

_client: Posthog | None = None


def get_client() -> Posthog:
    global _client
    if _client is None:
        token = os.environ.get("POSTHOG_PROJECT_TOKEN", "")
        print("[POSTHOG] Initializing client", flush=True)
        _client = Posthog(
            project_api_key=token,
            enable_exception_autocapture=True,
        )
        atexit.register(_client.shutdown)
    return _client

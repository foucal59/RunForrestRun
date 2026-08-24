from __future__ import annotations

import asyncio
import io
import json
import os
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from compat_api_logging import (
    compatibility_route_template,
    log_compatibility_api_usage,
)


class CompatibilityRouteTemplateTests(unittest.TestCase):
    def test_exact_routes_are_detected(self):
        self.assertEqual(compatibility_route_template("/api/streams"), "/api/streams")
        self.assertEqual(
            compatibility_route_template("/api/data/athlete-zones/"),
            "/api/data/athlete-zones",
        )
        self.assertEqual(
            compatibility_route_template("/api/data/athlete"),
            "/api/data/athlete",
        )

    def test_activity_id_is_removed_from_route_template(self):
        self.assertEqual(
            compatibility_route_template("/api/data/activities/12345/splits"),
            "/api/data/activities/{activity_id}/splits",
        )
        self.assertEqual(
            compatibility_route_template("/api/data/activities/abc/laps"),
            "/api/data/activities/{activity_id}/laps",
        )

    def test_current_routes_are_not_logged(self):
        self.assertIsNone(compatibility_route_template("/api/data/streams/12345"))
        self.assertIsNone(compatibility_route_template("/api/data/activities"))


class CompatibilityUsageLoggingTests(unittest.TestCase):
    def test_log_is_structured_and_excludes_sensitive_values(self):
        request = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/data/activities/98765/splits"),
            headers={
                "referer": "https://example-dashboard.vercel.app/activity/98765?token=secret",
                "user-agent": "Test browser",
                "sec-fetch-site": "same-origin",
                "authorization": "Bearer secret",
                "cookie": "garmin_session=secret",
            },
        )

        async def call_next(_request):
            return SimpleNamespace(status_code=200)

        output = io.StringIO()
        with patch.dict(os.environ, {"VERCEL": "1"}), redirect_stderr(output):
            response = asyncio.run(log_compatibility_api_usage(request, call_next))

        self.assertEqual(response.status_code, 200)
        line = output.getvalue().strip()
        self.assertTrue(line.startswith("[COMPAT-API] "))
        payload = json.loads(line.removeprefix("[COMPAT-API] "))
        self.assertEqual(payload["runtime"], "vercel")
        self.assertEqual(payload["route"], "/api/data/activities/{activity_id}/splits")
        self.assertEqual(payload["source_origin"], "https://example-dashboard.vercel.app")
        self.assertEqual(payload["status"], 200)
        self.assertNotIn("98765", line)
        self.assertNotIn("secret", line)


if __name__ == "__main__":
    unittest.main()

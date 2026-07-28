#!/usr/bin/env python3
"""
Legacy alias kept for compatibility.

The widget workaround is no longer needed; this forwards to the maintained
python-garminconnect login flow.
"""
from garmin_browser_login import main


if __name__ == "__main__":
    main()

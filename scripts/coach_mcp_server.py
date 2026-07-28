#!/usr/bin/env python3
"""Local runner for the "Running Coach" MCP server.

The shared MCP tools live in coach_mcp.py so the local runner and Vercel expose
the same contract.

Source de donnees (par ordre de priorite) :
  1. COACH_SNAPSHOT_URL  (URL privee, avec COACH_SNAPSHOT_TOKEN si necessaire)
  2. COACH_SNAPSHOT_PATH  (fichier local, defaut: <repo>/.runtime/coach-journal.json)

Transports :
  - HTTP (defaut)  : python3 scripts/coach_mcp_server.py            -> http://127.0.0.1:8765/mcp/
  - stdio          : MCP_TRANSPORT=stdio python3 scripts/coach_mcp_server.py

Le snapshot est regenere chaque matin par scripts/coach_journal.py (--json).
Aucune ecriture, aucun acces a Neon : lecture seule.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from coach_mcp import mcp  # noqa: E402


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "http").strip()
    if transport == "stdio":
        mcp.run()
    else:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.run(transport="http", host=host, port=port)

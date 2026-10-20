"""Vercel entry point for POST /api/keys: issues a personal connector key (ticket 04). The logic lives in api/_auth.py;
the local server (api/mcp.py) serves the same route."""
from starlette.applications import Starlette
from starlette.routing import Route

from api import _auth

app = Starlette(routes=[Route("/api/keys", _auth.issue_endpoint, methods=["POST"])])

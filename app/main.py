"""
FastAPI wrapper around the upstream Basecamp MCP server.

This module imports the upstream `basecamp_fastmcp.py` and `oauth_app.py` from the
`Basecamp‑MCP‑Server` package, mounts their ASGI applications into a single
service and exposes a simple `/health` endpoint.  All configuration is
driven by environment variables to make deployment on Railway as simple as
setting a few secrets.

The wrapper assumes that the upstream package has been installed via pip
(`pip install git+https://github.com/georgeantonopoulos/Basecamp-MCP-Server.git`).
Upon import the upstream server registers its FastMCP tools, and we then
expose the Streamable HTTP app on `/mcp`.  The OAuth app is mounted on
`/oauth` if available.

Environment variables used:

* `BASECAMP_CLIENT_ID`, `BASECAMP_CLIENT_SECRET`, `BASECAMP_ACCOUNT_ID`,
  `USER_AGENT` – forwarded directly to the upstream code.  At least the
  client ID and secret are mandatory for OAuth to work.
* `TOKEN_DIR` – directory where the OAuth token JSON will be stored.  A
  persistent volume should be mounted here so that tokens survive restarts.
  Defaults to `/app/data`.
* `TOKEN_FILENAME` – name of the token JSON file inside `TOKEN_DIR`.  Defaults
  to `oauth_tokens.json`.  The upstream code expects this file to exist
  after completing the OAuth flow.

The wrapper does **not** implement any Basecamp logic itself.  Instead it
delegates entirely to the upstream package, ensuring that you always benefit
from upstream updates without having to port code manually.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Optional

from fastapi import FastAPI


def _set_working_directory() -> None:
    """Ensure the token directory exists and set it as the current working directory.

    The upstream server writes and reads its token file relative to the current
    working directory.  Setting CWD to `TOKEN_DIR` ensures that the file ends
    up in the desired location.
    """
    token_dir = os.environ.get("TOKEN_DIR", "/app/data")
    os.makedirs(token_dir, exist_ok=True)
    os.chdir(token_dir)


def _find_upstream_file(filename: str) -> str:
    """Search sys.path for a file belonging to the upstream package.

    When installed via pip the upstream repository files live in one of the
    entries on sys.path.  They may be at the top level (e.g. site-packages)
    or inside a subfolder named after the repository.  This function searches
    both possibilities and returns the first hit.
    """
    for base in sys.path:
        candidate = os.path.join(base, filename)
        if os.path.isfile(candidate):
            return candidate
        candidate2 = os.path.join(base, "Basecamp-MCP-Server", filename)
        if os.path.isfile(candidate2):
            return candidate2
    raise RuntimeError(f"Could not find {filename} in sys.path; is the upstream package installed?")


def _load_module(path: str, name: str) -> object:
    """Dynamically import a module from an arbitrary file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # allow relative imports within the module
    spec.loader.exec_module(module)
    return module


def _load_fastmcp() -> object:
    """Locate and load the upstream FastMCP server."""
    mcp_path = _find_upstream_file("basecamp_fastmcp.py")
    module = _load_module(mcp_path, "basecamp_fastmcp_wrapper")
    # The upstream file typically defines either `mcp` or `server` holding
    # the FastMCP instance.  Search for common attribute names.
    for name in ("mcp", "server", "app"):
        mcp_obj = getattr(module, name, None)
        if mcp_obj is not None:
            return mcp_obj
    raise RuntimeError(
        "The upstream FastMCP file does not expose an MCP instance. Expected one of 'mcp', 'server' or 'app'."
    )


def _load_oauth_app() -> Optional[object]:
    """Locate and load the upstream OAuth FastAPI app, if available."""
    try:
        oauth_path = _find_upstream_file("oauth_app.py")
    except RuntimeError:
        return None
    module = _load_module(oauth_path, "basecamp_oauth_wrapper")
    return getattr(module, "app", None)


# Configure environment and working directory up front
_set_working_directory()

# Create a FastAPI instance for the wrapper
app = FastAPI(title="Basecamp MCP (Railway Wrapper)")

# Mount the upstream MCP HTTP application
mcp_instance = _load_fastmcp()

# The upstream FastMCP instance exposes its ASGI app via either
# `streamable_http_app` (preferred) or `http_app`.  If neither exists we
# cannot serve HTTP.
if hasattr(mcp_instance, "streamable_http_app"):
    app.mount("/mcp", mcp_instance.streamable_http_app())
elif hasattr(mcp_instance, "http_app"):
    app.mount("/mcp", mcp_instance.http_app())
else:
    raise RuntimeError("Upstream FastMCP instance does not provide a HTTP app (no streamable_http_app/http_app)")

# Mount the upstream OAuth routes on /oauth if present
oauth_app = _load_oauth_app()
if oauth_app is not None:
    app.mount("/oauth", oauth_app)


@app.get("/health")
def health() -> dict[str, bool]:
    """Simple health endpoint used by Railway to determine service readiness."""
    return {"ok": True}
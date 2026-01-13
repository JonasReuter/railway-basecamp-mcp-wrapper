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
from fastapi.middleware.wsgi import WSGIMiddleware


def _set_working_directory() -> None:
    """Ensure the token directory exists and set it as the current working directory.

    The upstream server writes and reads its token file relative to the current
    working directory.  Setting CWD to `TOKEN_DIR` ensures that the file ends
    up in the desired location.
    """
    token_dir = os.environ.get("TOKEN_DIR", "/app/data")
    os.makedirs(token_dir, exist_ok=True)
    os.chdir(token_dir)

# Compute and set the BASECAMP_REDIRECT_URI environment variable if not
# explicitly provided.  The upstream OAuth app expects this value to
# match the redirect URL registered with Basecamp.  If a public base
# URL (PUBLIC_BASE_URL) has been supplied, derive the callback URL
# automatically by appending "/oauth/callback".  This allows users to
# configure only the PUBLIC_BASE_URL in Railway.
def _configure_redirect_uri() -> None:
    # Only set the redirect URI if it hasn't been defined and a public
    # base URL is available.  Do nothing if either condition is false.
    if os.environ.get("BASECAMP_REDIRECT_URI"):
        return
    public_base = os.environ.get("PUBLIC_BASE_URL")
    if not public_base:
        return
    # Ensure no trailing slash on the base URL to avoid double slashes.
    base = public_base.rstrip("/")
    # The upstream OAuth app defines its callback route at '/auth/callback' (relative
    # to its mount path).  Because we mount the Flask app at '/oauth', the
    # effective callback URL is '/oauth/auth/callback'.  See upstream
    # `oauth_app.py` for route definitions【712093837881706†L190-L263】.  We
    # therefore compute the redirect URI accordingly.
    os.environ["BASECAMP_REDIRECT_URI"] = f"{base}/oauth/auth/callback"


def _configure_token_storage() -> None:
    """
    Patch the upstream token_storage module to write the OAuth tokens to our
    desired location.  The upstream implementation writes tokens into the
    directory containing `token_storage.py` (i.e. /opt/basecamp-mcp).  This
    patch overrides the TOKEN_FILE attribute to point into the directory
    specified by the environment variables TOKEN_DIR and TOKEN_FILENAME.

    By doing so, tokens will be persisted in a Railway volume mounted at
    TOKEN_DIR, ensuring they survive restarts.  If TOKEN_DIR is not set,
    defaults to /app/data (matching earlier examples).  See upstream
    implementation for details on TOKEN_FILE usage【568162562896650†L16-L19】.
    """
    # Determine target directory and file name.  Use defaults consistent with
    # the Dockerfile and .env.example if environment variables are unset.
    token_dir = os.environ.get("TOKEN_DIR", "/app/data")
    token_filename = os.environ.get("TOKEN_FILENAME", "oauth_tokens.json")
    # Ensure directory exists
    os.makedirs(token_dir, exist_ok=True)
    # Construct full path
    token_path = os.path.join(token_dir, token_filename)
    try:
        # Import the upstream module and patch the TOKEN_FILE constant
        import importlib
        token_storage = importlib.import_module("token_storage")
        token_storage.TOKEN_FILE = token_path
    except Exception:
        # If import fails (should not happen), do nothing
        pass


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

_configure_redirect_uri()

_configure_token_storage()



# ---------------------------------------------------------------------------
# Mount the upstream MCP HTTP application
#
# By default FastMCP's `http_app()` returns an ASGI application that serves
# the MCP API on the `/mcp/` prefix (see the FastMCP HTTP deployment docs:
#   https://gofastmcp.com/deployment/http).  If we naively mount this app
# under `/mcp` we end up with a doubled path (`/mcp/mcp/`), which causes
# 404 errors.  To avoid this, we explicitly set the `path` parameter to
# "/" (empty prefix) so that the returned ASGI app serves the MCP API at
# its root.  When mounted at `/mcp`, the final URL becomes `/mcp/` (with
# Starlette automatically handling the redirect from `/mcp` to `/mcp/`).
mcp_instance = _load_fastmcp()

def _create_mcp_app() -> object:
    """Create the FastMCP ASGI app with the correct path and lifespan.

    The upstream FastMCP instance exposes two methods for HTTP deployment:
    `http_app()` and the older `streamable_http_app()`.  Both methods accept
    optional keyword arguments, including `path`, which controls the URL
    prefix that the MCP routes will be served under.  The default value for
    `path` is "/mcp", which would result in a duplicate prefix when the
    app is mounted at `/mcp`.  Therefore we always override `path` with
    "/" (or an empty string) to ensure the returned app serves its routes
    from the root.

    Returns the ASGI application and its lifespan attribute.
    """
    # Prefer the modern `http_app` if available.  It implements the
    # Streamable HTTP transport with SSE polling support as of FastMCP
    # v2.14.0.  Fall back to `streamable_http_app` if necessary.
    if hasattr(mcp_instance, "http_app"):
        try:
            return mcp_instance.http_app(path="/")
        except TypeError:
            # Older versions of FastMCP may not accept the `path` kwarg.
            # In that case, we fall back to the default behaviour, which
            # implicitly uses `/mcp` as the path.  The double prefix will
            # still occur, but this branch maintains backward compatibility.
            return mcp_instance.http_app()
    if hasattr(mcp_instance, "streamable_http_app"):
        try:
            return mcp_instance.streamable_http_app(path="/")
        except TypeError:
            return mcp_instance.streamable_http_app()
    raise RuntimeError(
        "Upstream FastMCP instance does not provide a HTTP app (no http_app/streamable_http_app)"
    )

# Instantiate the MCP ASGI app.  We call this function only once so that
# its lifespan can be reused by the parent FastAPI application.
mcp_app = _create_mcp_app()

# Create the parent FastAPI app with the MCP instance's lifespan.  Passing the
# lifespan ensures that FastMCP's session manager is properly started and
# shutdown alongside the wrapper.  Without this, requests to the MCP
# endpoints may return 404 because the session manager has not been
# initialized (see FastMCP docs on mounting MCP servers【222704054020368†L530-L543】).
# Note: We get the lifespan from the mcp_instance (FastMCP object), not from
# mcp_app (Starlette ASGI object), which doesn't have a lifespan attribute.
app = FastAPI(title="Basecamp MCP (Railway Wrapper)", lifespan=mcp_instance.lifespan)

# Mount the MCP app at '/mcp'.  With the path override above, the MCP
# endpoints will be available at `/mcp` (no duplicate prefix).  Starlette
# will automatically redirect `/mcp` to `/mcp/` when redirect_slashes is
# enabled (default).  Clients should follow this redirect, but for
# compatibility we accept that some POST requests may be redirected via
# 307; the MCP app will handle them correctly.
app.mount("/mcp", mcp_app)


# Mount the upstream OAuth routes on /oauth if present
oauth_app = _load_oauth_app()
if oauth_app is not None:
    # The upstream OAuth app is a Flask (WSGI) application.  FastAPI requires
    # an ASGI interface, so wrap it with WSGIMiddleware before mounting.
    app.mount("/oauth", WSGIMiddleware(oauth_app))


@app.get("/health")
def health() -> dict[str, bool]:
    """Simple health endpoint used by Railway to determine service readiness."""
    return {"ok": True}
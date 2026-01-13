# Basecamp MCP wrapper for Railway

This repository wraps the upstream [Basecamp‑MCP‑Server](https://github.com/georgeantonopoulos/Basecamp-MCP-Server) project and exposes it as a cloud‑native HTTP service suitable for use with tools like Langflow.  The wrapper is intentionally thin: it installs the upstream server as a dependency via `pip` and mounts its MCP and OAuth apps into a single FastAPI application.  Configuration is entirely environment‑driven so that you never have to bake secrets into your code or images.

## Features

* **Upstream package installation** – the GitHub repository is installed directly from the source via `pip` to ensure you always run the latest version.
* **HTTP MCP endpoint** – the wrapper exposes the upstream FastMCP server on `/mcp` using the Streamable HTTP transport.  This is the recommended transport for remote MCP servers and is compatible with Langflow’s MCP client interface【515982219742721†L0-L0】.
* **OAuth mounted** – the OAuth flow provided by the upstream repository is mounted under `/oauth`.  You initiate the authorization flow via `/oauth/start` and receive the callback at `/oauth/callback`.  Basecamp requires a type of `web_server` for this flow【515982219742721†L0-L0】.
* **Token persistence** – the wrapper honours a `TOKEN_DIR` and `TOKEN_FILENAME` so that OAuth and refresh tokens are saved to a Railway volume.  Without a persistent volume the tokens would be lost on redeploy.
* **Environment‑only configuration** – all sensitive values (client ID, secret, account ID, public URL) are provided via environment variables.  Nothing needs to be hard‑coded in the code or container images.

## Usage

### 1. Create the project in Railway

1. Fork or clone this repository into your own GitHub account.
2. In Railway click *New Project* → *Deploy from GitHub* and select your fork.
3. Set the following environment variables in the Railway dashboard (see `.env.example` for descriptions):
   - `BASECAMP_CLIENT_ID`
   - `BASECAMP_CLIENT_SECRET`
   - `BASECAMP_ACCOUNT_ID`
   - `USER_AGENT`
   - `PUBLIC_BASE_URL` – use your Railway domain (e.g. `https://your‑project.up.railway.app`).
   - `TOKEN_DIR` – defaults to `/app/data`.
   - `TOKEN_FILENAME` – defaults to `oauth_tokens.json`.
4. Add a **volume** to your project and mount it at the path given by `TOKEN_DIR` (default `/app/data`).  This ensures that refreshed tokens persist across redeploys.
5. Deploy the project.  Railway reads `railway.toml` to build the image from the provided `Dockerfile`.

### 2. Configure OAuth in Basecamp/37signals

1. Create a new integration at [launchpad.37signals.com/integrations](https://launchpad.37signals.com/integrations) and note the client ID and secret.
2. In the integration settings, set the redirect URI to `{PUBLIC_BASE_URL}/oauth/callback`.  This must match exactly.
3. Select the authorization flow type **Web Server** (this corresponds to `type=web_server` in the OAuth requests【515982219742721†L0-L0】).

### 3. Authorize your account

After deploying the service and configuring the integration:

1. Browse to `{PUBLIC_BASE_URL}/oauth/start`.
2. You will be redirected to Basecamp to authorize the integration.  After granting access you will be returned to `{PUBLIC_BASE_URL}/oauth/callback`.
3. The OAuth callback saves the tokens into your volume.  You only need to do this once; thereafter the wrapper automatically refreshes the access token when necessary.

### 4. Use the MCP endpoint

The upstream FastMCP server is mounted at `{PUBLIC_BASE_URL}/mcp`.  This endpoint implements the Streamable HTTP transport recommended by the MCP specification.  In Langflow you can add it as an MCP server by providing the URL:

```json
{
  "mcpServers": {
    "basecamp": {
      "url": "https://your‑project.up.railway.app/mcp"
    }
  }
}
```

Langflow will then query your Basecamp account through the remote MCP server.

## Local development

If you wish to run the wrapper locally you can clone the repository and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# create a .env file based on the template and fill in your secrets
cp .env.example .env
export $(grep -v '^#' .env | xargs)

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Security note

Ensure that you keep your client secret, tokens and API keys secure.  Do not commit a real `.env` file into source control and always mount the token file on a persistent volume when running in production.
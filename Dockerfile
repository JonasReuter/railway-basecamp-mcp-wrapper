FROM python:3.11-slim

# Disable Python bytecode generation and ensure unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create application directory
WORKDIR /app


# Install system packages required to build and install the upstream package.
# The upstream repository is installed from GitHub via pip, which requires
# `git` to be present in the container.  We also install `build-essential`
# so that any compiled dependencies can be built.  These packages are
# removed at the end to keep the image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Clone the upstream Basecamp MCP server.  We clone the repository rather
# than installing it via pip because the project is not published as a
# proper Python package and pip installation fails due to missing
# metadata.  Cloning here makes the source available for our wrapper
# at runtime.  We place it under /opt so that it is outside the app code
# tree and can be found via PYTHONPATH.
RUN git clone https://github.com/georgeantonopoulos/Basecamp-MCP-Server.git /opt/basecamp-mcp

# Set PYTHONPATH so that the wrapper can import modules from the upstream
# repository.  Without this, our dynamic module loader would not find
# `basecamp_fastmcp.py` and `oauth_app.py` in sys.path.  Note that
# $PYTHONPATH may be unset; in that case the colon prefix gracefully
# expands to an empty string.
ENV PYTHONPATH=/opt/basecamp-mcp:${PYTHONPATH}

# Copy and install Python dependencies first.  Doing this as a separate
# layer allows Docker to cache the dependency installation even when
# your application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the wrapper application into the image
COPY app ./app

# At runtime Railway sets the PORT environment variable automatically.
# Use bash -lc so that shell expansions (like ${PORT}) work reliably.
CMD ["bash", "-lc", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
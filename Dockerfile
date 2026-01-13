FROM python:3.11-slim

# Disable Python bytecode generation and ensure unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create application directory
WORKDIR /app

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
# ---------- Stage 1: build wheels (handles psycopg2 compile cleanly) ----------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build deps for psycopg2 (skip if you only use psycopg2-binary)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# ---------- Stage 2: minimal runtime ----------
FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUNICORN_CMD_ARGS="--bind 0.0.0.0:8000 --workers 2 --timeout 120 --log-level info"

# Runtime libs only (libpq for psycopg2; curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 ca-certificates curl libxrender1 libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps from prebuilt wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

# Install MolScribe separately with --no-deps because its setup.py pins
# torch<2.0 but it runs fine with torch 2.x. Installing without deps avoids
# pip's resolution failure while keeping the existing torch version intact.
# timm and OpenNMT-py are also installed --no-deps for the same reason.
RUN pip install --no-cache-dir huggingface_hub && \
    pip install --no-cache-dir --no-deps \
        timm==0.4.12 \
        OpenNMT-py==2.2.0 \
        MolScribe

# Copy your application (this brings in your NEW main page/templates/static)
# Make sure your repo contains: app.py, models.py, templates/, static/, etc.
COPY . .

# (Optional but recommended) run as non-root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health endpoint should exist in app.py (e.g., @app.get("/health"))
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# Gunicorn entrypoint
CMD ["gunicorn", "app:app"]


# syntax=docker/dockerfile:1

# =============================================================================
# FinAlly — AI Trading Workstation
#
# Multi-stage build: Node builds the Next.js static export, Python runs the
# FastAPI app that serves both the API and those static files on port 8000.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — build the Next.js static export (produces frontend/out)
# -----------------------------------------------------------------------------
FROM node:20-slim AS frontend-build

ENV NEXT_TELEMETRY_DISABLED=1 \
    CI=true

WORKDIR /build

# Dependency manifests first so the install layer caches independently of source.
# `npm ci` deliberately, with no fallback: it installs the lockfile exactly, and
# a missing lockfile should fail the build rather than silently resolve fresh
# versions and produce a subtly different image months from now.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Fail loudly here rather than shipping an image that serves a blank page.
RUN test -f out/index.html || (echo "ERROR: next build produced no out/index.html — is output:'export' set in next.config?" >&2; exit 1)

# -----------------------------------------------------------------------------
# Stage 2 — Python runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    FINALLY_DB_PATH=/app/db/finally.db \
    FINALLY_STATIC_DIR=/app/static

WORKDIR /app

# uid/gid 1000 deliberately: it matches the first human account on most Linux
# hosts, so a bind-mounted ./db is writable without extra flags.
RUN groupadd --gid 1000 finally \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash finally

# Dependencies resolve from the committed lockfile only — no network resolution,
# no drift between a local `uv sync` and the image.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code changes far more often than dependencies, so it lands last.
COPY backend/app ./app
RUN uv sync --frozen --no-dev

# The built frontend. API_CONTRACT.md §8: /app/static, sibling to app/.
COPY --from=frontend-build /build/out ./static

COPY --chmod=0755 scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# /app/db is the volume mount point. Creating and chowning it here is what makes
# a *named* volume inherit finally:finally ownership when Docker initialises it
# from the image — without this the non-root user cannot create finally.db.
# Only /app/db is chowned: the app never writes to its own code or virtualenv,
# and chowning the whole 6k-file venv costs ~50s of build time.
RUN mkdir -p /app/db \
 && chown finally:finally /app/db \
 && chmod 0775 /app/db \
 && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh

VOLUME ["/app/db"]

USER finally

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

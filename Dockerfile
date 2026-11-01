# syntax=docker/dockerfile:1
# Budget app image (PLAN 9.1). Build it with:
#   docker build --platform linux/amd64 -t budget-app:<tag> .
# The web stage runs natively (no emulation). Only the Python stage is amd64.
# No secrets and no database go in the image. The only samples are the fake runs demo
# mode is built from (/app/demo/runs). .dockerignore is an allowlist.

# ---- Frontend build (Node 24, build only, not in the runtime image) ----
FROM --platform=$BUILDPLATFORM node:24.22-trixie-slim@sha256:1375326a10579cc1fd089d5da149ad29ae76dc3ccee6c0b0746d3de09dc5d0a1 AS web
WORKDIR /web
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false
# .npmrc pins exact versions and the "before" date (releases at least two weeks old).
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc frontend/.node-version ./
RUN npm ci
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build

# ---- uv (pinned by version and digest; 0.12.23 was released 2026-10-03) ----
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# ---- Runtime ----
FROM python:3.14.8-slim-trixie@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a AS runtime
# uv is mounted only for the two sync steps, so it is in no layer of the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev
COPY --from=web /web/dist ./static
COPY schema/categories.json schema/redaction.json ./schema/
# Demo mode (docs/plans/demo-mode.md): the fake sample runs, owned by root and read only.
# The release scan checks they match samples/data file for file.
COPY samples/data ./demo/runs
RUN find /app/demo/runs -type d -exec chmod 555 {} + \
    && find /app/demo/runs -type f -exec chmod 444 {} +

# Mount points. Owned by 99:100 (the usual nobody:users) so a fresh named volume
# (the release smoke test) is writable too. On a server the host folders are mounted over these.
RUN mkdir -p /data/db /data/backups /data/import /data/config \
    && chown 99:100 /data/db /data/backups /data/import /data/config \
    && chmod 700 /data/db /data/backups /data/import /data/config

# Paths only. The env file on the server (--env-file) sets the rest and can override these.
ENV PATH="/app/.venv/bin:$PATH" \
    TZ=America/Chicago \
    BUDGET_STATIC_DIR=/app/static \
    BUDGET_CATEGORIES_FILE=/app/schema/categories.json \
    BUDGET_REDACTION_FILE=/app/schema/redaction.json \
    BUDGET_DB_PATH=/data/db/budget.db \
    BUDGET_IMPORT_DIR=/data/import \
    BUDGET_BACKUP_DIR=/data/backups \
    BUDGET_DEMO_RUNS=/app/demo/runs \
    BUDGET_DEMO_DIR=/tmp/budget-demo

USER 99:100
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

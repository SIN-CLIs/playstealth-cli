# syntax=docker/dockerfile:1.7

# =============================================================================
# HeyPiggy Vision Worker — production container
# =============================================================================
# Multi-stage build:
#   1) `builder` compiles wheels against a pinned requirements.txt.
#   2) `runtime`  copies only the installed site-packages + source, runs as a
#      non-root user, and exposes a Python-based healthcheck.
#
# Build:
#   docker build -t heypiggy-worker:latest .
#
# Run (with env file):
#   docker run --rm --env-file .env heypiggy-worker:latest
# -----------------------------------------------------------------------------

ARG PYTHON_VERSION=3.13

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

# Build tools needed for Pillow on slim (libjpeg/libpng headers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies into an isolated prefix so the runtime stage
# can copy just the site-packages without build tooling. We install *with*
# dependency resolution — the earlier `--no-deps || fallback` pattern
# masked real install errors, so we fail loudly instead.
COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# OCI image labels — visible in `docker inspect` and registries.
LABEL org.opencontainers.image.title="heypiggy-vision-worker" \
      org.opencontainers.image.description="Autonomous HeyPiggy survey worker for the A2A-SIN platform" \
      org.opencontainers.image.source="https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="OpenSIN-AI"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HEYPIGGY_LOG_FORMAT=json

# Runtime-only native libs (no -dev, no compilers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with stable UID/GID for volume permissions.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} app \
    && useradd  --system --uid ${APP_UID} --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy installed Python packages from builder.
COPY --from=builder /install /usr/local

# Copy source. .dockerignore keeps this minimal.
COPY --chown=app:app . /app

# Drop root.
USER app

# Artifacts volume (mount a real volume in prod).
VOLUME ["/tmp"]

# Health: the worker has no HTTP server — we verify the package still imports.
# Fails fast if Pillow/structlog or the worker package is broken.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import worker, PIL, structlog; print('ok')" || exit 1

# PID 1 reaping + signal forwarding for graceful shutdown (SIGTERM).
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command: run the worker via the package entrypoint.
# Override with `docker run ... python heypiggy_vision_worker.py` for the
# legacy shim.
CMD ["python", "-m", "worker"]

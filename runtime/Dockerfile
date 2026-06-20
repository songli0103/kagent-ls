# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# ---- Dependency layer (cached) -------------------------------------------
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---- Application layer ---------------------------------------------------
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Drop privileges — use a numeric UID so the pod can satisfy runAsNonRoot.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 1000 agent
USER 1000:1000

# The venv was created by uv sync above at /app/.venv. Put it on PATH so
# `python` and the agent runtime entry point resolve to the venv.
ENV PATH="/app/.venv/bin:${PATH}"

# Long-running HTTP server (the Operator creates a Deployment, not a Job,
# so the container stays up and serves /chat/completions on port 8080).
EXPOSE 8080
CMD ["python", "src/agent_runtime/main.py"]
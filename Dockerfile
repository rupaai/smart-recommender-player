# CI passes a mirror of the same image (Docker Hub rate-limits shared runners).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# Pinned so builds are repeatable; Dependabot bumps it.
RUN pip install --no-cache-dir uv==0.11.32

ENV UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
# git is only needed to fetch the Homelab plugin (a git dependency); it is removed again.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && uv sync --frozen --no-dev --no-install-project \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY server.py database.py scanner.py enricher.py recommender_rules.yaml ./
COPY services ./services
COPY packages ./packages
COPY templates ./templates
COPY static ./static
# Config for when Homelab runs the app (homelab.yaml points CONFIG_PATH here).
COPY homelab ./homelab

# Everything under /app is root-owned and read-only to the app; it writes only to mounted volumes.
RUN useradd --create-home --uid 1000 app
USER app

ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uv", "run", "--no-sync", "python", "server.py"]

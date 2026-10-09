FROM python:3.12-slim

# Pinned so builds are repeatable; Dependabot bumps it.
RUN pip install --no-cache-dir uv==0.11.32

ENV UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server.py database.py scanner.py enricher.py ./
COPY services ./services
COPY templates ./templates
COPY static ./static

# Everything under /app is root-owned and read-only to the app; it writes only to mounted volumes.
RUN useradd --create-home --uid 1000 app
USER app

ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uv", "run", "--no-sync", "python", "server.py"]

# clipper live recorder: `clipper record watch` (no GPU extras).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# uv installs exactly what uv.lock pins (incl. the yt-dlp nightly), as on the workers
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY clipper ./clipper
RUN uv sync --frozen --no-dev --no-editable --no-cache

ENV PATH=/app/.venv/bin:$PATH \
    CLIPPER_CONFIG=/data/clipper.toml \
    CLIPPER_PATHS_STREAMS=/data/streams \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /data

# status.json is rewritten every few seconds while the watcher loop runs
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,sys,time; sys.exit(time.time() - os.path.getmtime('/data/streams/.record/status.json') > 300)"

ENTRYPOINT ["clipper"]
CMD ["record", "watch"]

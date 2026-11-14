# clipper on the NAS (deploy/hub): `clipper serve`, the Discord bot and the worker.
# Debian 13 for ffmpeg 7 (Shorts use -/filter_complex), Intel's media driver for QSV on the
# NAS iGPU, the tailscale CLI for the war room's tailnet status. No GPU extras: transcription
# is Modal.
FROM python:3.12-slim-trixie

RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata fontconfig \
        fonts-dejavu-core intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 vainfo \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY --from=docker.io/tailscale/tailscale:v1.104.4 /usr/local/bin/tailscale /usr/local/bin/tailscale
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY clipper ./clipper
RUN uv sync --frozen --no-dev --no-editable --no-cache --extra modal --extra faces --extra discord

# The config and every relative [paths] entry live in /data (the NAS's clipper folder);
# the prompts are inside the package. HOME is writable for the tools that keep a cache there.
ENV PATH=/app/.venv/bin:$PATH \
    CLIPPER_CONFIG=/data/clipper.toml \
    HOME=/tmp \
    LIBVA_DRIVER_NAME=iHD \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /data

ENTRYPOINT ["clipper"]
CMD ["serve"]

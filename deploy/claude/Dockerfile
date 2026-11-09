# Claude Code on the NAS (deploy/claude): the hub image's base (Debian 13, ffmpeg 7, Intel's
# media driver for QSV) plus git, gh and Claude Code. The code isn't baked in: it is the
# clone at /app, which the entrypoint makes on first start and syncs a venv for.
FROM python:3.12-slim-trixie

RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata fontconfig \
        fonts-dejavu-core intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 vainfo \
        git gh openssh-client less procps ripgrep jq curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY --from=docker.io/tailscale/tailscale:v1.104.4 /usr/local/bin/tailscale /usr/local/bin/tailscale

# Claude Code's native build, inside the image (a session reads its binary from disk, which
# mustn't be the NAS volume). deploy.sh passes the day as CLAUDE_CODE_BUILD, so a deploy picks
# up a new release at most once a day; the container never updates itself.
ARG CLAUDE_CODE_VERSION=latest
ARG CLAUDE_CODE_BUILD=0
WORKDIR /tmp
RUN echo "claude code build $CLAUDE_CODE_BUILD" \
    && curl -fsSL https://claude.ai/install.sh | HOME=/opt/claude bash -s "$CLAUDE_CODE_VERSION" \
    && chmod -R a+rX /opt/claude \
    && ln -sf "$(readlink -f /opt/claude/.local/bin/claude)" /usr/local/bin/claude \
    && claude --version

COPY entrypoint.sh /usr/local/bin/clipper-claude
# The nightly checkup (#185): the script and its prompt as deployed, not the clone's (a phone
# session may have /app on any branch).
COPY checkup.sh /usr/local/bin/clipper-checkup
COPY checkup-git.sh /usr/local/bin/checkup-git
COPY checkup.md /usr/local/share/clipper/checkup.md
RUN chmod 755 /usr/local/bin/clipper-claude /usr/local/bin/clipper-checkup /usr/local/bin/checkup-git \
    && chmod 644 /usr/local/share/clipper/checkup.md

# /app is the clone, /home/claude Claude Code's home (both volumes). The config and every
# relative [paths] entry live in /data, as in the hub; prompts come with the clone.
ENV HOME=/home/claude \
    PATH=/app/.venv/bin:$PATH \
    CLIPPER_CONFIG=/data/clipper.toml \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_UPDATES=1 \
    LIBVA_DRIVER_NAME=iHD \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /app

ENTRYPOINT ["clipper-claude"]

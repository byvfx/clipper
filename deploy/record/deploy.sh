#!/usr/bin/env bash
# Deploy or update the live recorder (`clipper record watch`) on the NAS.
#
# Run from the repo root:  bash deploy/record/deploy.sh
#   RECORD_SSH_HOST  ssh host alias (default: nas)
#   RECORD_DIR       compose dir on the NAS, relative to the ssh user's home (default: clipper-record)
#   CLIPPER_TOML     config to install as $CLIPPER_DATA/clipper.toml (default: ./clipper.toml);
#                    the recorder reads [record] and [kick]; the image pins paths.streams
#                    to /data/streams (= $CLIPPER_DATA/streams), whatever the file says
#   RECORD_SUDO      prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# First run copies env.template to deploy/record/env.local (gitignored; alert webhook or
# bot token). Recordings in progress pause on the restart and resume, as a new part,
# when the new container starts.
set -euo pipefail

HOST=${RECORD_SSH_HOST:-nas}
DIR=${RECORD_DIR:-clipper-record}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
TOML=${CLIPPER_TOML:-$ROOT/clipper.toml}
ENV_LOCAL=$HERE/env.local

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL (add CLIPPER_ALERT_WEBHOOK for Discord alerts)"
fi
DATA=$(grep '^CLIPPER_DATA=' "$ENV_LOCAL" | cut -d= -f2-)
DATA=${DATA:-/volume1/docker/clipper}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$TMP/src/"
cp "$HERE/Dockerfile" "$TMP/src/"
cp -r "$ROOT/clipper" "$TMP/src/clipper"
find "$TMP/src" -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/compose.yaml" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"

tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' && rm -rf '$DIR/src' && tar -C '$DIR' -xzf - \
    && chmod 600 '$DIR/.env' && mkdir -p '$DATA/streams'"
ssh "$HOST" "cat > '$DATA/clipper.toml'" < "$TOML"   # UGOS sftp is chrooted; plain ssh isn't
ssh "$HOST" "cd '$DIR' && ${RECORD_SUDO:-} docker compose up -d --build --remove-orphans \
    && ${RECORD_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f'"

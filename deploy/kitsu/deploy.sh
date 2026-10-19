#!/usr/bin/env bash
# Deploy or update Kitsu on the NAS: the mathbou/docker-cgwire compose stack
# (Kitsu, Zou API/events/jobs, Postgres 18, Redis, Meilisearch) with our env file.
#
# Run from this machine:  bash deploy/kitsu/deploy.sh
#   KITSU_SSH_HOST   ssh host alias (default: nas)
#   KITSU_DIR        install dir on the NAS, relative to the ssh user's home (default: kitsu)
#   KITSU_SUDO       prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# First run generates deploy/kitsu/env.local with random secrets (keep it; it is the
# only copy of the DB password locally). Rerunning updates the stack and runs Zou's
# DB upgrade; data lives in named Docker volumes and survives.
set -euo pipefail

HOST=${KITSU_SSH_HOST:-nas}
DIR=${KITSU_DIR:-kitsu}
REPO=https://github.com/mathbou/docker-cgwire.git
REF=8e9c393d170ac515afb947b6068e6221dc854c16   # pinned; bump deliberately
HERE=$(cd "$(dirname "$0")" && pwd)
ENV_LOCAL=$HERE/env.local

if [ ! -f "$ENV_LOCAL" ]; then
    rnd() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-32}"; }
    sed -e "s/@DB_PASSWORD@/$(rnd)/" -e "s/@INDEXER_KEY@/$(rnd)/" -e "s/@SECRET_KEY@/$(rnd 48)/" \
        "$HERE/env.template" > "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "generated $ENV_LOCAL"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone -q "$REPO" "$TMP/src"
git -C "$TMP/src" checkout -q "$REF"
cp "$ENV_LOCAL" "$TMP/src/env.clipper"

tar -C "$TMP/src" --exclude=.git -czf - . \
    | ssh "$HOST" "mkdir -p '$DIR' && tar -C '$DIR' -xzf - && chmod 600 '$DIR/env.clipper'"
ssh -tt "$HOST" "cd '$DIR' && ${KITSU_SUDO:-} bash build.sh -e=\"\$PWD/env.clipper\""

PORT=$(grep '^PORT=' "$ENV_LOCAL" | cut -d= -f2)
echo "Kitsu should be up at http://$(ssh -G "$HOST" | awk '/^hostname /{print $2}'):$PORT"

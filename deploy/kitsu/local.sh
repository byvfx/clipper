#!/usr/bin/env bash
# Kitsu on this machine, no ssh: the same pinned mathbou/docker-cgwire compose stack (Kitsu,
# Zou API/events/jobs, Postgres 18, Redis, Meilisearch) that deploy.sh puts on a NAS, with our
# env file. Needs Docker (Desktop or Engine) and git.
#
#   bash deploy/kitsu/local.sh
#   KITSU_DIR    where the stack's files live (default: ~/kitsu)
#   KITSU_SUDO   prefix for docker commands if your user needs it (e.g. "sudo")
#
# First run generates deploy/kitsu/env.local with random secrets (keep it; it is the only copy
# of the DB password). Rerunning updates the stack and runs Zou's DB upgrade; data lives in
# named Docker volumes and survives. Then open http://localhost:<PORT> (PORT in env.local) and
# set [review] host = "http://<this machine>:<PORT>/api"; the wiki page Kitsu has the accounts.
set -euo pipefail

DIR=${KITSU_DIR:-$HOME/kitsu}
REPO=https://github.com/mathbou/docker-cgwire.git
REF=8e9c393d170ac515afb947b6068e6221dc854c16   # pinned, the same as deploy.sh; bump both deliberately
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
mkdir -p "$DIR"
tar -C "$TMP/src" --exclude=.git -cf - . | tar -C "$DIR" -xf -
cp "$ENV_LOCAL" "$DIR/env.clipper"
chmod 600 "$DIR/env.clipper"
(cd "$DIR" && ${KITSU_SUDO:-} bash build.sh -e="$PWD/env.clipper")

PORT=$(grep '^PORT=' "$ENV_LOCAL" | cut -d= -f2)
echo "Kitsu should be up at http://localhost:$PORT (first login: the stack's default admin, see the Kitsu wiki page)"

#!/usr/bin/env bash
# Deploy or update Claude Code on the NAS (deploy/claude/compose.yaml): a `claude
# remote-control` session in its own clone of the repo. Its own compose project: the recorder
# and the hub are never restarted by this. Restarting it ends the session that is open in the
# Claude app (the clone and Claude's sign-in stay).
#
# Run from the repo root:  bash deploy/claude/deploy.sh
#   CLAUDE_SSH_HOST  ssh host alias (default: $HUB_SSH_HOST, else $RECORD_SSH_HOST, else nas)
#   CLAUDE_COMPOSE   compose dir on the NAS, relative to the ssh user's home (default: clipper-claude)
#   HUB_SUDO         prefix for docker commands if the ssh user needs it (e.g. "sudo")
#
# The image holds no clipper code: the entrypoint clones CLIPPER_REPO into CLAUDE_DIR/repo
# on first start and leaves it alone after that (Claude pulls, branches and pushes there).
# First run copies env.template to deploy/claude/env.local (gitignored): the keys.
set -euo pipefail

HOST=${CLAUDE_SSH_HOST:-${HUB_SSH_HOST:-${RECORD_SSH_HOST:-nas}}}
DIR=${CLAUDE_COMPOSE:-clipper-claude}
HERE=$(cd "$(dirname "$0")" && pwd)
ENV_LOCAL=$HERE/env.local

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$HERE/env.template" "$ENV_LOCAL"
    chmod 600 "$ENV_LOCAL"
    echo "created $ENV_LOCAL: fill in the keys, then run this again" >&2
    exit 1
fi
# KEY's value in env.local, without the quotes a dotenv value may have (KEY="value")
setting() {
    grep "^$1=" "$ENV_LOCAL" | tail -1 | cut -d= -f2- | sed -E "s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true
}
DATA=$(setting CLIPPER_DATA); DATA=${DATA:-/volume1/docker/clipper}
CDIR=$(setting CLAUDE_DIR); CDIR=${CDIR:-/volume1/docker/clipper-claude}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
cp "$HERE/Dockerfile" "$HERE/entrypoint.sh" "$HERE/checkup.sh" "$HERE/checkup-git.sh" "$HERE/checkup.md" \
    "$HERE/audit.sh" "$HERE/audit-deep.md" "$TMP/src/"
cp "$HERE/compose.yaml" "$TMP/"
cp "$ENV_LOCAL" "$TMP/.env"
# The repo to clone and who commits there default to this checkout's.
fill() {  # fill KEY's empty line in .env (or add one) with a value
    grep -v "^$1=" "$TMP/.env" > "$TMP/env.new" || true
    echo "$1=$2" >> "$TMP/env.new"
    mv "$TMP/env.new" "$TMP/.env"
}
[ -n "$(setting CLIPPER_REPO)" ] || fill CLIPPER_REPO "$(git remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#')"
[ -n "$(setting GIT_USER_NAME)" ] || fill GIT_USER_NAME "$(git config user.name || true)"
[ -n "$(setting GIT_USER_EMAIL)" ] || fill GIT_USER_EMAIL "$(git config user.email || true)"
# The Anthropic key goes in as a file (a compose secret), never in the environment: Remote
# Control refuses to start where ANTHROPIC_API_KEY is set.
KEY=$(setting ANTHROPIC_API_KEY)
[ -n "$KEY" ] || { echo "ANTHROPIC_API_KEY is empty in $ENV_LOCAL (copy it from deploy/hub/env.local)" >&2; exit 1; }
(umask 077 && printf '%s\n' "$KEY" > "$TMP/anthropic_key")
grep -v '^ANTHROPIC_API_KEY=' "$TMP/.env" > "$TMP/env.new" || true
mv "$TMP/env.new" "$TMP/.env"

ssh "$HOST" "test -f '$DATA/clipper.toml'" \
    || { echo "no $DATA/clipper.toml on $HOST: the config lives there (see the NAS-hub wiki page)" >&2; exit 1; }
tar -C "$TMP" -czf - . | ssh "$HOST" "mkdir -p '$DIR' '$CDIR/repo' '$CDIR/home' && rm -rf '$DIR/src' \
    && tar -C '$DIR' -xzf - && chmod 600 '$DIR/.env' '$DIR/anthropic_key' && chmod 700 '$CDIR/home'"
ssh "$HOST" "cd '$DIR' && ${HUB_SUDO:-} docker compose build --build-arg CLAUDE_CODE_BUILD=$(date +%F) \
    && ${HUB_SUDO:-} docker compose up -d --remove-orphans && ${HUB_SUDO:-} docker compose ps"
echo "logs: ssh $HOST 'cd $DIR && docker compose logs -f claude'"
echo "first start: sign in with  ssh -t $HOST 'cd $DIR && docker compose exec claude claude auth login'"
echo "then answer its prompts once with  ssh -t $HOST 'docker attach clipper-claude'  (Ctrl-P Ctrl-Q detaches)"

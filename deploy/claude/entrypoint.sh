#!/bin/sh
# The claude container's start (deploy/claude): clone the repo on the first start, keep its
# venv in step with uv.lock, set up git and gh, wait for a claude.ai sign-in, then run
# `claude remote-control` in /app.
set -eu

# Remote Control refuses API-key auth; clipper reads its key from [paths] anthropic_key.
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN

# gh reads GH_TOKEN itself; this makes git use it too (the first clone of the private repo,
# then pushes).
if [ -n "${GH_TOKEN:-}" ]; then
    gh auth setup-git >/dev/null 2>&1 || echo "gh auth setup-git failed: git will ask for a login" >&2
fi

if [ ! -d /app/.git ]; then
    [ -n "${CLIPPER_REPO:-}" ] || { echo "CLIPPER_REPO is empty: set it in deploy/claude/env.local" >&2; exit 1; }
    echo "cloning $CLIPPER_REPO into /app"
    GIT_TERMINAL_PROMPT=0 git clone "$CLIPPER_REPO" /app \
        || { echo "clone failed: a private repo needs GH_TOKEN in deploy/claude/env.local" >&2; exit 1; }
fi

if [ -n "${GIT_USER_NAME:-}" ]; then git config --global user.name "$GIT_USER_NAME"; fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then git config --global user.email "$GIT_USER_EMAIL"; fi

# A broken lock on a branch Claude left mid-change mustn't keep the session from starting.
cd /app
uv sync --frozen --extra modal --extra faces --extra discord \
    || echo "uv sync failed: clipper may be out of date in /app/.venv" >&2

if ! claude auth status >/dev/null 2>&1; then
    echo "Claude Code isn't signed in. On the laptop:"
    echo "  ssh -t nas 'cd clipper-claude && docker compose exec claude claude auth login'"
    echo "waiting for the sign-in..."
    until claude auth status >/dev/null 2>&1; do sleep 10; done
    echo "signed in"
fi

# The nightly checkup (#185) at CHECKUP_AT (HH:MM, the container's TZ; "" = off), in the
# background beside the session: checkup.sh runs `clipper checkup` and, when it finds
# something, a read-only `claude -p` that DMs one report.
if [ -n "${CHECKUP_AT:-}" ]; then
    (
        while :; do
            now=$(date +%s)
            next=$(date -d "today $CHECKUP_AT" +%s) || { echo "CHECKUP_AT=$CHECKUP_AT isn't HH:MM: no checkup" >&2; exit 0; }
            [ "$next" -gt "$now" ] || next=$(date -d "tomorrow $CHECKUP_AT" +%s)
            sleep $((next - now))
            clipper-checkup >>/tmp/checkup.log 2>&1 || echo "checkup failed, see /tmp/checkup.log" >&2
        done
    ) &
fi

# The first start asks to enable Remote Control and to trust /app: answer once with
# `docker attach clipper-claude` (detach with Ctrl-P Ctrl-Q). Both answers are kept in home/.
exec claude remote-control --name "${CLAUDE_SESSION_NAME:-clipper on the NAS}" \
    --spawn same-dir --permission-mode "${CLAUDE_PERMISSION_MODE:-default}"

#!/bin/sh
# The weekly digest and the monthly deep audit, run by the claude container after the morning
# checkup (entrypoint.sh). Weekly on AUDIT_WEEKDAY (1 = Monday ... 7 = Sunday, the default):
# `clipper audit --save`, no model, DM'd as it is. Monthly on AUDIT_MONTHDAY (default 1):
# `clipper audit --brief` goes to a read-only `claude -p` (audit-deep.md), which follows the
# repeat problems into the logs and code, DMs a summary and leaves a report. Either one empty = off.
#
# Like checkup.sh it runs CHECKUP_REF's code (default main) from the worktree checkup.sh keeps.
#
# By hand: docker compose exec claude clipper-audit [--weekly] [--deep] [--dry-run]
#   --weekly or --deep runs that one whatever the date; --dry-run prints the DM and saves nothing.
set -u
DRY="" WEEK="" DEEP=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --weekly) WEEK=1 ;;
        --deep) DEEP=1 ;;
        *) echo "clipper-audit: [--weekly] [--deep] [--dry-run]" >&2; exit 2 ;;
    esac
done
if [ -z "$WEEK$DEEP" ]; then
    [ "$(date +%u)" = "${AUDIT_WEEKDAY-7}" ] && WEEK=1
    [ "$(date +%-d)" = "${AUDIT_MONTHDAY-1}" ] && DEEP=1
fi
[ -n "$WEEK$DEEP" ] || exit 0

REF=${CHECKUP_REF:-main}
WT=${CHECKUP_WORKTREE:-$HOME/checkup-src}
month=$(date +%Y-%m)

dm() {  # the DM: stdin
    if [ -n "$DRY" ]; then echo "--- DM (dry run, not sent):"; cat; else clipper alert send --kind checkup -; fi
}

if [ ! -d "$WT/.git" ] && [ ! -f "$WT/.git" ]; then
    git -C /app worktree prune
    git -C /app worktree add -q --detach "$WT" "origin/$REF" || { echo "can't make $WT" >&2; exit 1; }
fi
git -C "$WT" fetch -q origin "$REF" && git -C "$WT" checkout -q --detach FETCH_HEAD \
    || echo "couldn't update $WT to origin/$REF: running what it has ($(git -C "$WT" log --oneline -1))" >&2
export PYTHONPATH="$WT"
cd "$WT"

if ! clipper audit -h >/dev/null 2>&1; then
    echo "origin/$REF has no \`clipper audit\` yet: nothing run" >&2
    exit 0
fi

weekly() {
    save=--save
    [ -n "$DRY" ] && save=""   # a test run mustn't count as a week
    if out=$(clipper audit $save 2>/tmp/audit.err); then
        printf '%s\n' "$out" | dm
    else
        echo "Weekly audit: \`clipper audit\` failed: $(tail -n 3 /tmp/audit.err)" | dm
    fi
}

deep() {
    if ! clipper audit --brief >/tmp/audit-brief.md 2>/tmp/audit.err; then
        echo "Deep audit $month: \`clipper audit --brief\` failed: $(tail -n 3 /tmp/audit.err)" | dm
        return
    fi
    if ! grep -q '^# Deep audit brief' /tmp/audit-brief.md; then
        echo "Deep audit $month: no weekly digests saved yet, so nothing to go on." | dm
        return
    fi
    prompt=$(cat /usr/local/share/clipper/audit-deep.md)
    [ -n "$DRY" ] && prompt="$prompt

DRY RUN: don't run \`clipper alert send\`; put the message at the top of your final answer instead."
    # As in checkup.sh: exact commands wherever an extra flag could write, git through checkup-git.
    set -- Read Grep Glob \
        "Bash(clipper audit)" "Bash(clipper audit --all)" "Bash(clipper audit --brief)" "Bash(clipper costs)" \
        "Bash(clipper checkup)" "Bash(clipper checkup --json)" "Bash(clipper status)" \
        "Bash(clipper doctor --offline)" "Bash(clipper doctor --offline --role claude)" \
        "Bash(clipper record status)" "Bash(clipper worker --status)" "Bash(clipper alert send:*)" \
        "Bash(checkup-git:*)"
    if ! claude -p "$prompt" --max-turns 80 \
            --disallowedTools Edit Write NotebookEdit WebFetch WebSearch \
            --allowedTools "$@" </dev/null >/tmp/audit-deep.md; then
        # Signed out or a usage limit: the facts still go out, and next month's run reads them again.
        { echo "Deep audit $month: Claude couldn't run, so here are the repeat problems only."
          sed -n '/^## Problems/,/^## Week by week/p' /tmp/audit-brief.md | grep '^- ' | cut -c1-300 | head -n 5
        } | dm
        return
    fi
    if [ -n "$DRY" ]; then
        cat /tmp/audit-deep.md
    elif ! clipper audit --keep-deep /tmp/audit-deep.md 2>/tmp/audit.err; then
        echo "Deep audit $month: the report wasn't saved: $(tail -n 3 /tmp/audit.err)" | dm
    fi
}

[ -n "$WEEK" ] && weekly
[ -n "$DEEP" ] && deep
exit 0

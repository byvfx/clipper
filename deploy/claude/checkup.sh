#!/bin/sh
# The nightly checkup (#185), run by the claude container at CHECKUP_AT (entrypoint.sh).
# `clipper checkup` first, no model: nothing found = a one-line "all good" DM and no Claude run;
# findings = a headless `claude -p` (checkup.md) that reads the logs and DMs one report. That
# run is read-only: the tools below are all it may use; it never writes to /app.
# By hand: docker compose exec claude clipper-checkup
set -u
cd /app

report=$(clipper checkup --json --mark 2>/tmp/checkup.err)
code=$?
day=$(date +%m-%d)
if [ "$code" -eq 0 ]; then
    clipper alert send --kind checkup "Checkup $day: all good."
    exit 0
fi
if [ "$code" -ne 1 ]; then   # the checkup itself broke: say so without a model run
    clipper alert send --kind checkup "Checkup $day: \`clipper checkup\` exited $code: $(tail -n 3 /tmp/checkup.err)"
    exit 0
fi
printf '%s\n' "$report" > /tmp/checkup.json

# One argument per rule (a rule has spaces in it); in -p mode anything else is refused.
set -- Read Grep Glob \
    "Bash(clipper checkup:*)" "Bash(clipper status:*)" "Bash(clipper doctor --offline:*)" \
    "Bash(clipper record status)" "Bash(clipper worker --status)" "Bash(clipper alert send:*)" \
    "Bash(git log:*)" "Bash(git show:*)" "Bash(git diff:*)" "Bash(tail:*)" "Bash(head:*)"
if ! claude -p "$(cat /usr/local/share/clipper/checkup.md)" --max-turns 40 \
        --disallowedTools Edit Write NotebookEdit WebFetch WebSearch \
        --allowedTools "$@" </dev/null; then
    # No diagnosis (signed out, usage limit): the findings alone still go out.
    clipper checkup 2>&1 | head -c 1800 | clipper alert send --kind checkup -
fi

#!/bin/sh
# `git log|show|diff` for the nightly checkup's Claude run (checkup.sh): a prefix rule like
# Bash(git diff:*) would also allow `git diff --output=<file>`, which writes. Only those three
# subcommands, and no option that writes a file or runs a configured program.
case "${1:-}" in
    log|show|diff) ;;
    *) echo "checkup-git: only log, show or diff" >&2; exit 2 ;;
esac
for a in "$@"; do
    case "$a" in
        --output|--output=*|--ext-diff|--textconv|--open-files-in-pager*|--exec-path*|--git-dir*|--work-tree*)
            echo "checkup-git: $a isn't allowed here" >&2; exit 2 ;;
    esac
done
cmd=$1
shift
exec git --no-pager "$cmd" --no-ext-diff --no-textconv "$@"

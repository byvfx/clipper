#!/usr/bin/env bash
# First run on a new macOS or Linux machine (#78). From the clipper folder:
#
#   bash deploy/bootstrap.sh
#
# Installs what's missing (uv, which brings Python 3.12; ffmpeg), the Python packages, then
# runs `clipper setup`, which asks the rest. When setup picks roles that need more packages
# it exits with code 3; this script syncs them and starts setup again. Run it again any time
# to carry on. Arguments go to clipper setup, e.g. bash deploy/bootstrap.sh --answers a.toml
set -euo pipefail
cd "$(dirname "$0")/.."
have() { command -v "$1" >/dev/null 2>&1; }

if ! have uv; then
  echo "== Installing uv (it brings Python 3.12)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! have ffmpeg; then
  echo "== Installing ffmpeg"
  if have brew; then
    brew install ffmpeg
  elif have apt-get; then
    sudo apt-get install -y ffmpeg
  else
    echo "install ffmpeg (macOS: Homebrew from https://brew.sh, then brew install ffmpeg), then run this again"
    exit 1
  fi
fi

if [ ! -x .venv/bin/clipper ]; then
  echo "== Python packages (first sync; setup adds the ones its roles need)"
  uv sync
fi

# `clipper` typed anywhere: a link in ~/.local/bin to this folder's venv (uv's installer puts
# that folder on PATH). Bootstrapping another folder points it there.
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/clipper" "$HOME/.local/bin/clipper"
echo "== clipper command in ~/.local/bin (runs $PWD/.venv/bin/clipper)"

export CLIPPER_BOOTSTRAP=1
for _ in 1 2 3; do
  code=0
  .venv/bin/clipper setup "$@" || code=$?
  [ "$code" -eq 3 ] || exit "$code"
  extras=$(.venv/bin/clipper setup --sync-args)
  echo "== uv sync $extras"
  # shellcheck disable=SC2086  # one word per flag
  uv sync $extras
done
echo "setup still finds packages missing after syncing them; see the lines above"
exit 1

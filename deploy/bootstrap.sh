#!/usr/bin/env bash
# First run on a new macOS or Linux machine (#78). From the clipper folder:
#
#   bash deploy/bootstrap.sh
#
# Installs what's missing (uv, which brings Python 3.12; ffmpeg; on Linux the fonts Windows
# and macOS come with), the Python packages, then
# runs `clipper setup`, which asks the rest. When setup picks roles that need more packages
# it exits with code 3; this script syncs them and starts setup again. Run it again any time
# to carry on. Arguments go to clipper setup, e.g. bash deploy/bootstrap.sh --answers a.toml
set -euo pipefail
cd "$(dirname "$0")/.."
have() { command -v "$1" >/dev/null 2>&1; }
LOGIN_PATH=$PATH   # before this script adds to it
apt_updated=
apt_install() {
  # a new VM or container has empty (or stale) package lists: refresh them once
  if [ -z "$apt_updated" ]; then sudo apt-get update; apt_updated=1; fi
  sudo apt-get install -y "$@"
}

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
    apt_install ffmpeg
  else
    echo "install ffmpeg (macOS: Homebrew from https://brew.sh, then brew install ffmpeg), then run this again"
    exit 1
  fi
fi

# Covers, Short captions and the weekly issue use Impact, Georgia, Arial and Roboto Condensed:
# Windows and macOS have the first three, Linux none. Microsoft's core fonts package asks you
# to accept its license; without it clipper falls back to other fonts (doctor says which).
if [ "$(uname)" = Linux ] && have apt-get && ! fc-list 2>/dev/null | grep -qi impact; then
  echo "== Installing fonts (Impact, Georgia, Arial: accept Microsoft's license when it asks)"
  apt_install fontconfig fonts-roboto-unhinted
  # declining the license still exits 0, with the package installed and no fonts
  apt_install ttf-mscorefonts-installer || true
  if ! fc-list | grep -qi impact; then
    echo "== No Impact/Georgia/Arial yet: sudo dpkg-reconfigure ttf-mscorefonts-installer (and accept the license)"
  fi
fi

if [ ! -x .venv/bin/clipper ]; then
  echo "== Python packages (first sync; setup adds the ones its roles need)"
  uv sync
fi

# `clipper` typed anywhere: a link in ~/.local/bin to this folder's venv. Bootstrapping another
# folder points it there. uv's installer puts ~/.local/bin on PATH, but uv from Homebrew or pip
# doesn't, so add it to the login shell's startup file when it isn't on PATH already.
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/clipper" "$HOME/.local/bin/clipper"
echo "== clipper command in ~/.local/bin (runs $PWD/.venv/bin/clipper)"
case ":$LOGIN_PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    case "${SHELL##*/}" in
      zsh) rc="$HOME/.zshrc" ;;
      bash) if [ "$(uname)" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
      *) rc="$HOME/.profile" ;;
    esac
    # shellcheck disable=SC2016  # $HOME and $PATH expand when the shell starts
    line='export PATH="$HOME/.local/bin:$PATH"'
    if ! grep -qsF "$line" "$rc"; then
      printf '\n%s\n' "$line" >> "$rc"
      echo "== ~/.local/bin added to PATH in $rc (open a new terminal)"
    fi
    ;;
esac

export CLIPPER_BOOTSTRAP=1
# A joined config used in place: setup saves CLIPPER_CONFIG / CLIPPER_LOCAL_CONFIG in the shell
# profile, which this shell predates; pick them up before each (re)start of setup.
saved_env() {
  local f
  for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$f" ]; then
      eval "$(grep -E '^export CLIPPER_(LOCAL_)?CONFIG=' "$f" || true)"
    fi
  done
}
for _ in 1 2 3; do
  saved_env
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

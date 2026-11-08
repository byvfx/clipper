A by-hand pass over a new install, for what the sandbox runs can't reach. Those run the
bootstrap in clean Windows Sandbox and Ubuntu machines from an answers file, offline and with
no keys. This pass covers the prompts, real keys, the online checks, the real NAS share and
some real work. Use it on the desktop, the Mac, or any machine after a big change to
`clipper setup`, and note whatever trips you up: that's what the pass is for.

The [walkthrough](Install) explains each step. This page is the list to tick.

## Before you start

- [ ] The NAS share is mounted (`\\<nas>\docker\clipper` on Windows, under `/Volumes` on a Mac).
- [ ] Keys at hand: Anthropic, the Kitsu bot token, the Modal token id and secret (a worker on
      Modal), the Discord bot token (bot role only).
- [ ] The code is in a short folder: `C:\clipper` or `~/clipper`.

## The bootstrap

Run it with no answers file, so every question is asked.

- [ ] Each question makes sense, and its default is the answer you'd give.
- [ ] **Join:** give the share's `clipper.toml` by typing its path, or drag the file in. Setup
      offers to use it in place (the default on a share).
- [ ] **Roles:** pick what this machine will really run. If setup stops to install packages,
      the bootstrap carries on by itself.
- [ ] **Streams folder:** the share's streams folder. Setup checks it's writable.
- [ ] **Encoder:** the GPU's own (NVENC on an NVIDIA machine, VideoToolbox on a Mac), not
      libx264.
- [ ] **Keys:** type one wrong on purpose; it's refused with a reason. The right ones are
      accepted.
- [ ] **Linux only:** accept Microsoft's font license when it asks.
- [ ] It ends with "Ready" and what to run next.

## After it

- [ ] In a new terminal, `clipper --help` works from any folder.
- [ ] `clipper doctor --role <role>` with no `--offline` shows 0 failures. The online checks
      reach Kitsu, Modal (a worker on Modal), the [hub](NAS-hub) over ssh and the war room
      (control).
- [ ] `clipper setup --check` shows every step done.
- [ ] A joined install has no `clipper.toml` in the checkout, and its `clipper.local.toml`
      holds only this machine's values.
- [ ] **Mac worker:** doctor's ffmpeg `subtitles` (libass) check passes. Homebrew's plain
      `ffmpeg` lacks it: follow doctor's `ffmpeg-full` hint.

## First real work

Don't start `clipper worker`, `clipper serve` or `clipper bot` unattended here. The
[hub's](NAS-hub) containers already run them, and their single-instance locks don't see
another machine. Try things by hand instead:

- [ ] **Worker:** re-render one Short on the test stream:
      `clipper shorts kick-131151926 --items v01 --force`. That one command exercises the
      encoder, the caption font and libass, and it reuses the saved layout, so no model call
      is expected. Watch the result.
- [ ] **Worker on Modal:** if doctor says the Modal app isn't deployed, run
      `clipper transcribe --deploy` (several minutes, once per Modal account).
- [ ] **Control:** `clipper services status` lists serve, the bot, the worker and the recorder.
- [ ] The war room opens in a browser.

## Not part of setup

On a joined machine these already live in the shared config, so there's nothing to redo:
the Discord app and its ids ([Review](Review)), the YouTube sign-in
([YouTube channel](YouTube-channel)) and the shows ([Adding a show](Adding-a-show)).

## Afterwards

Each confusing question, wrong default or error without a fix goes into an issue (or the
HANDOFF), so the next install doesn't hit it.

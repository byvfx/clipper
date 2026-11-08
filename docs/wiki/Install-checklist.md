A by-hand pass over a new install, for what the sandbox runs can't reach. Those run the
bootstrap in clean Windows Sandbox and Ubuntu machines from an answers file, offline and with
no keys. This pass covers the prompts, real keys, the online checks, the real folders and
some real work. Note whatever trips you up: that's what the pass is for.

There are two kinds of install, and some checks differ:
- **Join the hub:** another machine for the running install (the Mac as a control station, a
  second worker). It shares the NAS config, streams and schedule.
- **New install:** a stack of its own with its own config, streams, schedule, YouTube channel
  and Discord bot, such as the desktop as the second channel (#46). Never point one at the live
  share's `clipper.toml`, streams or schedule: its uploads and review cards would mix with
  Lolcow Cinema's.

The [walkthrough](Install) explains each step. This page is the list to tick.

## Before you start

- [ ] **Join:** the NAS share is mounted (`\\<nas>\docker\clipper` on Windows, under
      `/Volumes` on a Mac).
- [ ] **New:** a streams folder of its own, on a disk with room (~20-30 GB per stream).
- [ ] Keys at hand: Anthropic, the Kitsu bot token, the Modal token id and secret (a worker on
      Modal), the Discord bot token (bot role only; a new install needs its own Discord bot
      app, since two bots can't share one token).
- [ ] The code is in a short folder: `C:\clipper` or `~/clipper`.

## The bootstrap

Run it with no answers file, so every question is asked. On a machine that's already set up,
setup skips the steps it has done: pass `--step <name>` to the bootstrap (or
`clipper setup --step <name>`) to go through one again (`mode`, `roles`, `machine`, `keys`,
`check`).

- [ ] Each question makes sense, and its default is the answer you'd give.
- [ ] **Join:** give the share's `clipper.toml` by typing its path, or drag the file in. Setup
      offers to use it in place (the default on a share).
- [ ] **New:** setup writes a starter `clipper.toml` in the checkout.
- [ ] **Roles:** pick what this machine will really run. If setup stops to install packages,
      the bootstrap carries on by itself.
- [ ] **Streams folder:** the share's streams folder (join) or this install's own (new). Setup
      checks it's writable.
- [ ] **Encoder:** the GPU's own (NVENC on an NVIDIA machine, VideoToolbox on a Mac), not
      libx264.
- [ ] **Keys:** type one wrong on purpose; it's refused with a reason. The right ones are
      accepted.
- [ ] **Linux only:** accept Microsoft's font license when it asks.
- [ ] It ends with "Ready" and what to run next.

## After it

- [ ] In a new terminal, `clipper --help` works from any folder.
- [ ] `clipper doctor --role <role>` with no `--offline` shows 0 failures. The online checks
      reach Kitsu, Modal (a worker on Modal), and for a control station the [hub](NAS-hub)
      over ssh and the war room.
- [ ] `clipper setup --check` shows every step done.
- [ ] **Join:** no `clipper.toml` in the checkout, and `clipper.local.toml` holds only this
      machine's values.
- [ ] **New:** `[paths]` streams, schedule and youtube_token in its `clipper.toml` point at this
      install's own files, not the share.
- [ ] **Mac worker:** doctor's ffmpeg `subtitles` (libass) check passes. Homebrew's plain
      `ffmpeg` lacks it: follow doctor's `ffmpeg-full` hint.

## First real work

**Join:** don't start `clipper worker`, `clipper serve` or `clipper bot` unattended here. The
[hub's](NAS-hub) containers already run them, and their single-instance locks don't see
another machine. Try things by hand instead. A new install runs its own serve, bot and worker,
once the steps below and the ones in the next section are done.

- [ ] **Worker:** re-render one Short on the test stream:
      `clipper shorts kick-131151926 --items v01 --force`. A new install needs a copy of that
      stream folder in its own streams folder first. That one command exercises the encoder,
      the caption font and libass, and it reuses the saved layout, so no model call is
      expected. Watch the result.
- [ ] **Worker on Modal:** if doctor says the Modal app isn't deployed, run
      `clipper transcribe --deploy` (several minutes, once per Modal account).
- [ ] **Control:** `clipper services status` lists serve, the bot, the worker and the recorder.
- [ ] The war room opens in a browser.

## Not part of setup

**Join:** these already live in the shared config, so there's nothing to redo.

**New:** each needs doing by hand:
- [ ] Kitsu and clipper's account on it ([Kitsu](Kitsu)).
- [ ] The Discord bot app and its ids ([Discord app](Discord-app)).
- [ ] The YouTube sign-in to the new channel, `clipper upload --sign-in`
      ([YouTube sign-in](YouTube-sign-in)).
- [ ] The shows, each with its own Kitsu production ([Adding a show](Adding-a-show)).

## Afterwards

Each confusing question, wrong default or error without a fix goes into an issue (or the
HANDOFF), so the next install doesn't hit it.

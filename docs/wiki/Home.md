<p align="center">
  <img src="https://github.com/byvfx/clipper/wiki/images/banner.png" alt="Clippa: long Kick streams in, reviewed YouTube videos out" width="100%">
</p>

**Clippa** turns long Kick streams into YouTube uploads: full-length segments, topic
compilations, Shorts, titles, descriptions, chapters and thumbnails, with a person approving
every one before it goes out. Everything is a `clipper <stage>` command, and the always-on
pieces (the recorder, the worker, serve and the Discord bot) run it unattended. (The app was
called clipper; the command, package, config files and containers still are.)

> [!TIP]
> New here? Start with **[Getting started](Getting-started)**: install a machine, run one
> stream end to end, and know where to look when something breaks.

```mermaid
flowchart TB
  subgraph find ["1. Find the moments"]
    direction LR
    rec["fetch / record"] --> sig["signals"] --> tr["transcribe"] --> sc["score"] --> seg["segments"]
  end
  subgraph make ["2. Make the videos"]
    direction LR
    fa["faces"] --> cut["cut"] --> me["meta"] --> th["thumbs"] --> sh["shorts"]
  end
  subgraph ship ["3. Ship"]
    direction LR
    rv{{"review<br/>Kitsu · Discord · war room"}} -->|approved| sch["schedule"] --> up["upload to YouTube"]
    rv -. "note" .-> rt["retake<br/>re-cut or re-cover, back to review"]
  end
  find --> make --> ship
```

<table>
<tr>
<td width="33%" valign="top">

### Run it
[Getting started](Getting-started)<br>
[Roles and services](Roles-and-services)<br>
[Configuration](Configuration)<br>
[Pipeline](Pipeline)<br>
[Review](Review) · [Reviewing](Reviewing) · [War room](War-room)<br>
[Scheduling and uploads](Scheduling-and-uploads)<br>
[Costs](Costs) · [Checkup](Checkup)

</td>
<td width="33%" valign="top">

### Install
[Install](Install) (`clipper setup`)<br>
[Kitsu](Kitsu) · [Discord app](Discord-app) · [YouTube sign-in](YouTube-sign-in)<br>
[Windows](Install-Windows) · [macOS](Install-macOS)<br>
[Transcribe on Modal](Transcribe-on-Modal)<br>
[NAS recorder](NAS-recorder) · [NAS hub](NAS-hub)<br>
[Claude on the NAS](Claude-on-the-NAS)<br>
[Phone access](Phone-access)<br>
[Model providers](Model-providers)<br>
[Adding a show](Adding-a-show) · [YouTube channel](YouTube-channel)

</td>
<td width="33%" valign="top">

### Features
[Pre-shows](Pre-shows) and one-offs<br>
[Speakers](Speakers) · [Faces](Faces)<br>
[Covers](Covers) · [Cast library](Cast-library) · [B-roll](B-roll)<br>
[Weekly issue](Weekly-issue)<br>
[X posts](X-posts) · [TikTok](TikTok)<br>
[News watcher](News-watcher)<br>
[Character pieces](Character-pieces)

</td>
</tr>
<tr>
<td valign="top">

### Research
[Archives](Archives): other channels' back catalogs, searchable<br>
[People](People): one record per person, their names and channels<br>
[Receipts](Receipts) · [Investigations](Investigations)<br>
[Search swarm](Search-swarm) · [Story dossiers](Story-dossiers)

</td>
<td valign="top">

### Working on Clippa
[Developing](Developing): layout, code shape, checks, and how these pages are built<br>
[How the processes talk](How-the-processes-talk): locks, heartbeats, the outbox, serve's API, which log to read

</td>
<td valign="top">

### Something broke?
`clipper doctor` prints a fix for each problem; the bot DMs you; `clipper checkup` finds what
never raised an error; logs are in `logs/`
([more](Getting-started#when-something-breaks))

</td>
</tr>
</table>

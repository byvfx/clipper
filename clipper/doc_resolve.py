"""The DaVinci Resolve half of `clipper doc`: a build (clipper/doc.py's layout) becomes a new
timeline in the open project, and a timeline becomes a render.

Resolve Studio must be running on this machine with external scripting on (Preferences >
System > General > External scripting using: Local). Its scripting module is found from
$RESOLVE_SCRIPT_API / $RESOLVE_SCRIPT_LIB, else [doc] resolve_api / resolve_lib, else where
Blackmagic's installer puts it. Every build is a new timeline, "<title> vNN": an earlier one
is never changed.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from . import wiki
from .doc import A_CLIP, A_TONE, V_TEXT
from .errors import ClipperError

V_NAMES = ["Picture", "Labels and credits", "Text and cards"]
A_TRACKS = [("Clip sound", "stereo"), ("Narration", "mono"), ("Effects", "stereo"),
            ("Clip sound under the narration", "stereo"), ("Score A", "stereo"), ("Score B", "stereo"),
            ("Room tone", "stereo")]
INSTALL = {   # platform -> (the scripting API folder, the fusionscript library), as installed
    "win32": (r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
              r"%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\fusionscript.dll"),
    "darwin": ("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
               "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"),
    "linux": ("/opt/resolve/Developer/Scripting", "/opt/resolve/libs/Fusion/fusionscript.so"),
}


def api_paths(dc: dict) -> tuple[str, str]:
    api, lib = INSTALL.get(sys.platform, INSTALL["linux"])
    return (os.environ.get("RESOLVE_SCRIPT_API") or dc["resolve_api"] or os.path.expandvars(api),
            os.environ.get("RESOLVE_SCRIPT_LIB") or dc["resolve_lib"] or os.path.expandvars(lib))


def connect(dc: dict):
    """The running Resolve, or a ClipperError saying what is missing."""
    api, lib = api_paths(dc)
    if not (Path(api) / "Modules").is_dir():
        raise ClipperError(f"Resolve's scripting module isn't at {api}: install DaVinci Resolve Studio, or set "
                           f"[doc] resolve_api ({wiki.page('Documentaries', 'resolve')})")
    os.environ["RESOLVE_SCRIPT_API"], os.environ["RESOLVE_SCRIPT_LIB"] = api, lib
    if str(Path(api) / "Modules") not in sys.path:
        sys.path.append(str(Path(api) / "Modules"))
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        raise ClipperError(f"Resolve's scripting module didn't load ({e})") from None
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ClipperError("Resolve isn't running, or external scripting is off (Preferences > System > General > "
                           f"External scripting using: Local; {wiki.page('Documentaries', 'resolve')})")
    return resolve


def open_project(resolve, want: str = ""):
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise ClipperError("no project is open in Resolve")
    if want and project.GetName() != want:
        raise ClipperError(f"Resolve has {project.GetName()!r} open; plan.toml's project is {want!r}")
    return project


def check_rate(project, fps: int) -> None:
    """A new timeline takes the project's frame rate, and every frame of a build is counted at
    the plan's: refuse before anything is placed when they differ."""
    got = project.GetSetting("timelineFrameRate")
    try:
        same_rate = abs(float(got) - fps) < 0.01
    except (TypeError, ValueError):
        same_rate = False
    if not same_rate:
        raise ClipperError(f"the Resolve project's timeline frame rate is {got or 'unknown'}; the plan is built at "
                           f"{fps} fps: set fps in plan.toml (or [doc] fps) to the project's, or use a {fps} fps project")


def timelines(project) -> dict:
    return {t.GetName(): t for t in (project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1))}


def next_name(title: str, names) -> str:
    """"<title> vNN", one past the highest version there is."""
    used = [int(m.group(1)) for n in names if (m := re.fullmatch(re.escape(title) + r" v(\d+)", n))]
    return f"{title} v{max(used, default=0) + 1:02d}"


def walk(folder):
    yield from folder.GetClipList()
    for sub in folder.GetSubFolderList():
        yield from walk(sub)


def same(path: str) -> str:
    return str(path).replace("\\", "/").lower()


def pool_items(pool, media: dict[str, str]) -> dict:
    """Each media name's item in the media pool, importing the files that aren't there yet."""
    by_path = {same(c.GetClipProperty("File Path") or ""): c for c in walk(pool.GetRootFolder())}
    new = [str(Path(p)) for p in media.values() if same(p) not in by_path]
    if new:
        pool.ImportMedia(sorted(set(new)))
        by_path = {same(c.GetClipProperty("File Path") or ""): c for c in walk(pool.GetRootFolder())}
    missing = sorted(n for n, p in media.items() if same(p) not in by_path)
    if missing:
        raise ClipperError(f"Resolve didn't import: {', '.join(missing)}")
    return {n: by_path[same(p)] for n, p in media.items()}


def source_frames(row: dict, fps: int, item) -> tuple[int, int]:
    """A row's in and out in its source's own frames (a 60 fps reel in a 30 fps timeline), its
    sound rows too. Not tried against Resolve with a reel at another rate: conform such reels first."""
    try:
        rate = float(item.GetClipProperty("FPS") or fps)
    except (TypeError, ValueError):
        rate = fps
    if abs(rate - fps) < 0.01:
        return row["start"], row["end"]
    start = round(row["start"] * rate / fps)
    return start, start + round((row["end"] - row["start"]) * rate / fps)


def used_media(b: dict) -> dict[str, str]:
    names = {r["clip"] for k in ("sync", "v1", "v2", "v3", "audio") for r in b[k]}
    return {n: b["media"][n] for n in names}


def build(resolve, b: dict, project_name: str = "") -> str:
    """A new timeline from a build; returns its name. Read back afterwards: every row must be
    on its track at its frame with its length."""
    project = open_project(resolve, project_name)
    check_rate(project, b["fps"])
    pool = project.GetMediaPool()
    items = pool_items(pool, used_media(b))
    name = next_name(b["title"], timelines(project))
    tl = pool.CreateEmptyTimeline(name)
    if tl is None:
        raise ClipperError(f"Resolve refused to make the timeline {name!r}")
    project.SetCurrentTimeline(tl)
    tl.SetStartTimecode("00:00:00:00")
    t0 = tl.GetStartFrame()
    while tl.GetTrackCount("video") < V_TEXT:
        tl.AddTrack("video")
    while tl.GetTrackCount("audio") < A_TONE:
        tl.AddTrack("audio", A_TRACKS[tl.GetTrackCount("audio")][1])
    for i, n in enumerate(V_NAMES, 1):
        tl.SetTrackName("video", i, n)
    for i, (n, _) in enumerate(A_TRACKS, 1):
        tl.SetTrackName("audio", i, n)
    for key in ("sync", "v1", "v2", "v3", "audio"):
        rows = []
        for r in b[key]:
            start, end = source_frames(r, b["fps"], items[r["clip"]])
            d = {"mediaPoolItem": items[r["clip"]], "startFrame": start, "endFrame": end,
                 "recordFrame": t0 + r["record"], "trackIndex": r["track"]}
            if "type" in r:
                d["mediaType"] = r["type"]
            rows.append(d)
        got = pool.AppendToTimeline(rows) if rows else []
        placed = len([g for g in got or [] if g])
        if placed != len(rows):
            raise ClipperError(f"{name}: Resolve placed {placed} of {len(rows)} {key} rows; the timeline is left "
                               "as it is for a look, delete it before building again")
    for m in b["markers"]:
        tl.AddMarker(m["frame"], m["color"], m["name"], m["note"], m["duration"])
    unset = set_sound(tl, t0, b)
    bad = check(tl, t0, b)
    saved = resolve.GetProjectManager().SaveProject()
    print(f"{name}: end frame {tl.GetEndFrame() - t0} (planned {b['total']}); {bad} rows not where planned, "
          f"{unset} gains or fades Resolve refused; saved {saved}")
    if bad:
        raise ClipperError(f"{name}: {bad} rows aren't where the plan put them; look at the timeline before using it")
    if unset:
        raise ClipperError(f"{name}: Resolve refused {unset} gains or fades, so its mix isn't the plan's; delete the "
                           "timeline and build again")
    return name


def set_sound(tl, t0: int, b: dict) -> int:
    """Gains and fades, by where each piece of sound sits; how many Resolve refused."""
    want = {(r["track"], r["record"]): r for r in b["audio"]}
    want |= {(A_CLIP, r["record"]): r for r in b["sync"] if r.get("type") is None}
    unset = 0
    for track in range(1, A_TONE + 1):
        for it in tl.GetItemListInTrack("audio", track) or []:
            r = want.get((track, it.GetStart() - t0))
            if r is None:
                continue
            if r.get("gain"):
                unset += not it.SetProperty("AudioVolume", r["gain"])
            if r.get("fade_in") or r.get("fade_out"):
                unset += not it.SetFades({"FadeIn": r.get("fade_in", 0), "FadeOut": r.get("fade_out", 0)})
    return unset


def check(tl, t0: int, b: dict) -> int:
    """How many planned rows aren't on their track at their frame with their length."""
    bad = 0
    for kind, keys in (("video", ("sync", "v1", "v2", "v3")), ("audio", ("audio",))):
        for track in range(1, (V_TEXT if kind == "video" else A_TONE) + 1):
            on = {it.GetStart() - t0: it.GetDuration() for it in tl.GetItemListInTrack(kind, track) or []}
            for r in (r for k in keys for r in b[k] if r["track"] == track):
                bad += on.get(r["record"]) != r["end"] - r["start"]
    return bad


def render(resolve, name: str, folder: Path, dc: dict, project_name: str = "") -> Path:
    """Render a whole timeline to folder/<name>.mp4 and wait for it."""
    project = open_project(resolve, project_name)
    tl = timelines(project).get(name)
    if tl is None:
        raise ClipperError(f"no timeline {name!r} in {project.GetName()}")
    project.SetCurrentTimeline(tl)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    project.SetCurrentRenderFormatAndCodec(dc["render_format"], dc["render_codec"])
    if not project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(folder), "CustomName": stem,
                                      "ExportVideo": True, "ExportAudio": True}):
        raise ClipperError("Resolve refused the render settings")
    job = project.AddRenderJob()
    if not job:
        raise ClipperError("Resolve didn't queue the render")
    project.StartRendering([job])
    while project.IsRenderingInProgress():
        time.sleep(5)
    status = project.GetRenderJobStatus(job)
    project.DeleteRenderJob(job)
    out = folder / f"{stem}.{dc['render_format']}"
    if (status or {}).get("JobStatus") != "Complete" or not out.exists():
        raise ClipperError(f"render of {name!r} didn't finish: {status}")
    print(f"rendered {out}")
    return out

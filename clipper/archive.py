"""`clipper archive`: other people's back catalogs, listed and transcribed on Modal.

Usage:
    clipper archive add <subject> <url>...     list a source's long-form videos into the catalog
    clipper archive transcribe <subject> [--limit N] [--oldest] [--gpu L4] [--parallel N]
                                         [--dry-run]
    clipper archive status [<subject>]
    clipper archive check <subject> [--items ID...] [--all] [--limit N]
    clipper archive links <subject> [--status dead|restricted|alive|all]

A subject (e.g. "jer", issue #48) is paths.archive/<subject>/: catalog.json (every item
found: source, url, title, duration) and one folder per item with meta.json and the same
transcript.json / words.jsonl a stream gets, so later work (#53 search, #48 receipts) reads
them like streams. It is kept apart from paths.streams, so the worker, the bot and uploads
never see archive items.

Sources:
    YouTube channel urls (a channel, or its /videos or /streams tab; never /shorts): listed
        with yt-dlp; each item's audio is downloaded here as YouTube serves it and uploaded to
        Modal untouched (YouTube blocks downloads from datacenter IPs).
    Odysee channels (https://odysee.com/@name): listed with the Odysee API; Modal fetches the
        media itself (WhisperX.transcribe_url), so nothing passes through this machine.
    Kick VODs (a `clipper story` item, #84, with its HLS `m3u8`): the smallest variant
        ([archive] kick_format) is downloaded here, its audio sent as flac.
Items shorter than [archive] min_duration_s are skipped. Catalog order is newest first.
`transcribe` logs progress and failures to <subject>/transcribe.log as well as the console,
and a failed item leaves <subject>/<id>/error.txt until a rerun succeeds.

Link checks (`check`) ask whether each item's source url still plays, without downloading
anything: YouTube's oEmbed endpoint (no key, no quota) and Odysee's resolve. Results go in
<subject>/links.json: status alive | restricted (private, embedding off, or blocked) | dead,
the reason, when it was checked and since when it has had that status. A link that cannot be
checked (network, rate limit) keeps its last result. Items not checked within
[archive] link_recheck_days are checked again; `transcribe` skips items whose link is dead.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from . import hei, jsonstate, speakers, transcribe, ytdl
from .errors import ClipperError
from .modalmon import head_record, tracked

log = logging.getLogger(__name__)

ODYSEE_API = "https://api.na-backend.odysee.com/api/v1/proxy"
YT_OEMBED = "https://www.youtube.com/oembed"


def subject_dir(cfg: dict, subject: str) -> Path:
    return cfg["paths"]["archive"] / subject


def load_catalog(d: Path) -> dict[str, dict]:
    p = d / "catalog.json"
    return jsonstate.read(p, {})


def save_json(d: Path, name: str, data: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{name}.tmp"
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / name)


def save_catalog(d: Path, cat: dict[str, dict]) -> None:
    save_json(d, "catalog.json", cat)


def item_dir(d: Path, it: dict) -> Path:
    """Where an item's transcript is: its own folder, or `dir` for one transcribed elsewhere
    (a `clipper story` item that is one of our own streams)."""
    return Path(it["dir"]) if it.get("dir") else d / it["id"]


def item_cfg(cfg: dict, idir: Path) -> dict:
    """An item's config for `speakers`: named against its subject's own voices
    (voices/<subject>.json), as a stream is against its show's, never the base show's."""
    return {**cfg, "show": {**cfg["show"], "key": idir.parent.name}}


def is_item(cfg: dict, d: Path) -> bool:
    return d.resolve().parent.parent == Path(cfg["paths"]["archive"]).resolve()


def load_links(d: Path) -> dict[str, dict]:
    p = d / "links.json"
    return jsonstate.read(p, {})


# ---------- listing ----------

def list_youtube(url: str) -> list[dict]:
    url = url.rstrip("/")
    tabs = [url] if url.endswith(("/videos", "/streams")) else [f"{url}/videos", f"{url}/streams"]
    items = []
    for tab in tabs:
        r = ytdl.run("--flat-playlist", "-J", tab)
        if r.returncode and len(tabs) > 1 and "does not have" in r.stderr:
            continue  # e.g. no streams tab
        if r.returncode:
            raise ClipperError(f"yt-dlp {tab}: {ytdl.last_error(r)}")
        data = json.loads(r.stdout)
        for e in data.get("entries") or []:
            items.append({"id": f"yt-{e['id']}", "source": "youtube",
                          "url": f"https://www.youtube.com/watch?v={e['id']}",
                          "title": e.get("title") or "", "duration_s": float(e.get("duration") or 0),
                          "channel": data.get("channel") or "", "kind": tab.rsplit("/", 1)[1]})
    return items


def odysee(method: str, params: dict) -> dict:
    r = requests.post(f"{ODYSEE_API}?m={method}", timeout=60,
                      json={"jsonrpc": "2.0", "method": method, "params": params})
    r.raise_for_status()
    return r.json()["result"]


def list_odysee(url: str, limit: int | None = None, pace=None) -> list[dict]:
    """A channel's videos, newest first; limit: stop after about that many (a page of 50).
    pace(), when given, is called before each request (the swarm's per-source rate)."""
    pace = pace or (lambda: None)
    name = "@" + url.split("@", 1)[1].split("/")[0].split(":")[0]
    pace()
    ch = odysee("resolve", {"urls": [f"lbry://{name}"]})[f"lbry://{name}"]
    if "error" in ch:
        raise ClipperError(f"odysee {name}: {ch['error'].get('name')}")
    items, page = [], 1
    while True:
        pace()
        r = odysee("claim_search", {"channel_ids": [ch["claim_id"]], "page": page, "page_size": 50,
                                    "order_by": ["release_time"], "stream_types": ["video"]})
        for c in r["items"]:
            v = c.get("value", {})
            items.append({"id": f"od-{c['claim_id']}", "source": "odysee",
                          "url": f"lbry://{c['name']}#{c['claim_id']}", "title": v.get("title") or "",
                          "duration_s": float((v.get("video") or {}).get("duration") or 0),
                          "channel": (ch.get("value") or {}).get("title") or name,
                          "date": datetime.fromtimestamp(int(v.get("release_time") or c["timestamp"]),
                                                         UTC).date().isoformat()})
        if page >= r.get("total_pages", 1) or (limit and len(items) >= limit):
            return items[:limit] if limit else items
        page += 1


def list_source(cfg: dict, url: str) -> list[dict]:
    if hei.is_url(url):
        return hei.list_items(cfg["paths"]["hei_cookies"], cfg["archive"]["hei_pace_s"])
    if "odysee.com" in url or url.startswith("lbry://"):
        return list_odysee(url)
    return list_youtube(url)


def add(cfg: dict, subject: str, urls: list[str], match: str | None = None) -> None:
    """match: keep only items whose title matches this regex (case-insensitive), for a big
    channel with a few of the subject's videos in it (Adult Swim's On Cinema)."""
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    min_s = cfg["archive"]["min_duration_s"]
    pattern = re.compile(match, re.I) if match else None
    for url in urls:
        found = list_source(cfg, url)
        if pattern:
            found = [it for it in found if pattern.search(it["title"])]
        keep = [it for it in found if not 0 < it["duration_s"] < min_s]
        new = [it for it in keep if it["id"] not in cat]
        # ahead of what was there: a source lists newest first, so the catalog stays newest first
        cat = {**{it["id"]: it for it in new}, **cat}
        matching = f" matching {match!r}" if match else ""
        print(f"{url}: {len(found)} found{matching}, {len(found) - len(keep)} under {min_s} s skipped, "
              f"{len(new)} new ({sum(i['duration_s'] for i in new) / 3600:.0f} h)")
    save_catalog(d, cat)
    print(f"{subject}: {len(cat)} items, {sum(i['duration_s'] for i in cat.values()) / 3600:.0f} h")


# ---------- transcribing ----------

def estimate_usd(hours: float, gpu: str, diarize: bool = False) -> float:
    from .modal_app import DIARIZE_SPEED, GPU_PRICES, SPEED

    speed = SPEED.get(gpu, 30.0)
    gpu_h = hours / speed + (hours / (DIARIZE_SPEED * speed / SPEED["L40S"]) if diarize else 0)
    return gpu_h * GPU_PRICES.get(gpu, 0.0)


def transcribe_item(cfg: dict, idir: Path, it: dict, gpu: str, retry_on: tuple[str, ...] = ytdl.AGE) -> dict:
    """One item: words.jsonl and transcript.json in idir; returns the cost record. retry_on: the
    YouTube errors retried signed in (ytdl.AGE; a story adds ytdl.BOT when [story] allows it)."""
    tc = dict(cfg["transcribe"], backend="modal", diarize=cfg["archive"]["diarize"])
    settings = transcribe.modal_settings(tc, diarize=tc["diarize"])
    idir.mkdir(parents=True, exist_ok=True)
    meta = dict(it)
    t0 = time.time()
    worker = transcribe.modal_worker(tc, gpu)
    signed_in = False

    def call(fn, *args) -> tuple[dict, dict]:  # logged for `clipper modal` and the war room
        with tracked(cfg["paths"]["modal_runs"], "archive", f"{idir.parent.name}/{it['id']}", gpu,
                     meta.get("duration_s") or 0) as rec:
            t1 = time.time()
            out = fn.remote(*args)
            cost = transcribe.modal_cost(gpu, time.time() - t1, out)
            rec.update(cost, audio_s=round(out["dur"], 1))
        return out, cost

    if it["source"] == "youtube":
        args = ["-f", cfg["archive"]["youtube_format"], "--write-info-json", "--no-playlist",
                "-o", str(idir / "audio.%(ext)s"), it["url"]]
        r, signed_in = ytdl.run_retrying(cfg, *args, retry_on=retry_on)  # see ytdl's docstring
        audio = [p for p in idir.glob("audio.*") if p.suffix not in (".json", ".part")]
        if r.returncode or not audio:
            raise RuntimeError(f"yt-dlp: {ytdl.last_error(r)}")
        info_path = idir / "audio.info.json"
        info = jsonstate.load(info_path)
        info_path.unlink()
        ts = info.get("timestamp") or info.get("release_timestamp")
        meta.update(title=info.get("title") or it["title"], description=info.get("description") or "",
                    channel=info.get("channel") or info.get("uploader") or it.get("channel") or "",
                    duration_s=float(info.get("duration") or it["duration_s"]),
                    date=datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else None)
        data = audio[0].read_bytes()
        out, cost = call(worker.transcribe, data, audio[0].suffix, settings)
        audio[0].unlink()
    elif it["source"] in ("kick", "hei"):
        # The video `archive video` kept, else the stream's smallest download: Kick's HLS
        # variants carry video and audio together (no audio-only format), HEI's has an audio
        # rendition. Its audio goes to Modal as flac, as a stream's does.
        kept = kept_video(idir)
        src = kept or idir / "source.ts"
        if not kept:
            fmt, referer = ((cfg["archive"]["kick_format"], "https://kick.com/") if it["source"] == "kick"
                            else (cfg["archive"]["hei_audio_format"], hei.SITE + "/"))
            m3u8 = hei.fresh_m3u8(cfg["paths"]["hei_cookies"], it) if it["source"] == "hei" else it["m3u8"]
            # a missing HEI segment fails the item (retried next run) rather than leaving a gap
            whole = ["--abort-on-unavailable-fragments"] if it["source"] == "hei" else []
            r = ytdl.run("--referer", referer, "-f", fmt, "--hls-use-mpegts", *whole,
                         "--no-part", "-N", "4", "-o", str(src), m3u8)
            if r.returncode or not src.exists():
                raise RuntimeError(hei.scrub(f"yt-dlp: {ytdl.last_error(r)}"))
        wav = transcribe.extract_audio(idir)
        if not kept:
            src.unlink()
        enc = idir / "audio.flac"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "flac",
                        str(enc)], check=True)
        wav.unlink()
        out, cost = call(worker.transcribe, enc.read_bytes(), ".flac", settings)
        enc.unlink()
    else:
        media = odysee("get", {"uri": it["url"]})["streaming_url"]
        out, cost = call(worker.transcribe_url, media, settings)
    meta["transcribed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (idir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    transcribe.write(idir, tc, out["segments"], out["dur"], t0, {"modal": cost})
    if out.get("diarization"):
        transcribe.write_diarization(idir, out["diarization"], cost)
        speakers.apply(idir, item_cfg(cfg, idir))
    (idir / "error.txt").unlink(missing_ok=True)
    return {**cost, "signed_in": signed_in}


def run_transcribe(cfg: dict, subject: str, limit: int | None, oldest: bool, gpu: str,
                   parallel: int, dry_run: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    todo = [it for it in cat.values() if not (d / it["id"] / "words.jsonl").exists()]
    links = load_links(d)
    dead = {i for i, lk in links.items() if lk["status"] == "dead"}
    if skipped := sum(1 for it in todo if it["id"] in dead):
        print(f"{subject}: {skipped} skipped, source link dead (clipper archive links {subject})")
        todo = [it for it in todo if it["id"] not in dead]
    if oldest:
        todo.reverse()
    todo = todo[:limit] if limit else todo
    hours = sum(it["duration_s"] for it in todo) / 3600
    diarize = " with speaker turns" if cfg["archive"]["diarize"] else ""
    print(f"{subject}: {len(todo)} to transcribe{diarize}, {hours:.1f} h, on {gpu} x{parallel}: "
          f"~${estimate_usd(hours, gpu, bool(diarize)):.2f} (measured speed; upload and cold starts extra)")
    if dry_run or not todo:
        return
    # Our own log next to the data: a detached run's shell redirect once wrote nothing (2026-10-23).
    fh = logging.FileHandler(d / "transcribe.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    try:
        _transcribe_all(cfg, d, subject, todo, gpu, parallel)
    finally:
        log.removeHandler(fh)
        fh.close()


def _transcribe_all(cfg: dict, d: Path, subject: str, todo: list[dict], gpu: str, parallel: int,
                    retry_on: tuple[str, ...] = ytdl.AGE) -> None:
    log.info("%s: started, %d items on %s x%d", subject, len(todo), gpu, parallel)
    spent, done, failed, streak, signed_in = 0.0, 0, 0, 0, 0
    stop_after = cfg["archive"]["stop_after_failures"]
    with ThreadPoolExecutor(parallel) as pool:
        jobs = {pool.submit(transcribe_item, cfg, d / it["id"], it, gpu, retry_on): it for it in todo}
        for job in as_completed(jobs):
            it = jobs[job]
            if job.cancelled():
                continue
            try:
                res = job.result()
                spent += res["est_usd"]
                signed_in += res["signed_in"]
                done += 1
                streak = 0
            except Exception as e:  # noqa: BLE001
                failed += 1
                streak += 1
                (d / it["id"]).mkdir(parents=True, exist_ok=True)
                (d / it["id"] / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                log.warning("%s failed: %s: %s", it["id"], type(e).__name__, str(e)[:200])
            log.info("[%d/%d] %s %r  (spent ~$%.2f)", done + failed, len(todo), it["id"],
                     it["title"][:50], spent)
            if stop_after and streak >= stop_after:
                # e.g. YouTube's bot check on this IP: stop before it also blocks pre-show grabs
                log.warning("%d failures in a row: stopping (rerun to resume)", streak)
                pool.shutdown(wait=True, cancel_futures=True)
                break
    log.info("%s: %d transcribed (%d signed in), %d failed, ~$%.2f",
             subject, done, signed_in, failed, spent)


# ---------- keeping videos ----------

VIDEO_SOURCES = ("youtube", "hei")


def kept_video(idir: Path) -> Path | None:
    """The video `archive video` kept for an item, if any (written under another name and
    renamed when complete, so this is never a partial download)."""
    p = idir / "source.mp4"
    return p if p.is_file() else None


def keep_video(cfg: dict, idir: Path, it: dict) -> Path:
    """An item's best video as source.mp4 in idir, its caption tracks as captions.<lang>.<ext>."""
    idir.mkdir(parents=True, exist_ok=True)
    # merged or single-file, always an mp4; a missing segment fails it (fetched again next run)
    args = ["-f", cfg["archive"]["video_format"], "--merge-output-format", "mp4", "--remux-video", "mp4",
            "--abort-on-unavailable-fragments", "--no-playlist", "-N", "4", "--write-subs", "--sub-langs", "all,-live_chat",
            "-o", str(idir / "download.%(ext)s"), "-o", f"subtitle:{idir / 'captions'}.%(ext)s"]
    if it["source"] == "hei":
        r = ytdl.run("--referer", hei.SITE + "/", *args, hei.fresh_m3u8(cfg["paths"]["hei_cookies"], it))
    else:
        r, _ = ytdl.run_retrying(cfg, *args, it["url"], retry_on=ytdl.AGE)
    out = idir / "download.mp4"
    if r.returncode or not out.is_file():
        raise RuntimeError(hei.scrub(f"yt-dlp: {ytdl.last_error(r)}"))
    out.replace(idir / "source.mp4")
    for leftover in idir.glob("download.*"):
        leftover.unlink(missing_ok=True)
    (idir / "video_error.txt").unlink(missing_ok=True)
    return idir / "source.mp4"


def videos_todo(d: Path, cat: dict[str, dict], ids: list[str] | None = None) -> list[dict]:
    """Catalog items whose video can be kept and isn't yet, minus dead links (catalog order)."""
    dead = {i for i, lk in load_links(d).items() if lk["status"] == "dead"}
    return [it for it in cat.values()
            if it["source"] in VIDEO_SOURCES and not kept_video(d / it["id"]) and it["id"] not in dead
            and (ids is None or it["id"] in ids)]


def run_videos(cfg: dict, subject: str, ids: list[str] | None, limit: int | None, oldest: bool,
               parallel: int, dry_run: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    todo = videos_todo(d, cat, ids)  # catalog order: newest first (`add` puts new items ahead)
    if oldest:
        todo.reverse()
    todo = todo[:limit] if limit else todo
    hours = sum(it["duration_s"] for it in todo) / 3600
    print(f"{subject}: {len(todo)} videos to keep, {hours:.1f} h, "
          f"~{hours * cfg['archive']['video_gb_per_hour']:.0f} GB, {parallel} at once (no model cost)")
    if dry_run or not todo:
        return
    fh = logging.FileHandler(d / "video.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    done = failed = 0
    try:
        with ThreadPoolExecutor(parallel) as pool:
            jobs = {pool.submit(keep_video, cfg, d / it["id"], it): it for it in todo}
            for job in as_completed(jobs):
                it = jobs[job]
                try:
                    size = job.result().stat().st_size
                    done += 1
                    log.info("[%d/%d] %s %r  %.2f GB", done + failed, len(todo), it["id"], it["title"][:50],
                             size / 1e9)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    (d / it["id"]).mkdir(parents=True, exist_ok=True)
                    (d / it["id"] / "video_error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                    log.warning("[%d/%d] %s failed: %s", done + failed, len(todo), it["id"], str(e)[:200])
        log.info("%s: %d videos kept, %d failed", subject, done, failed)
    finally:
        log.removeHandler(fh)
        fh.close()


# ---------- speakers ----------

def relabel(cfg: dict, subject: str) -> None:
    """Name every diarized item's speakers again against the subject's enrolled voices (after
    `clipper speakers <item folder> --name SPEAKER_00=Name --enroll`), and say who talks how much."""
    d = subject_dir(cfg, subject)
    items = sorted(p.parent for p in d.glob("*/diarization.json"))
    if not items:
        raise ClipperError(f"no diarized items in {subject}: clipper archive transcribe {subject} --diarize")
    talk: dict[str, float] = {}
    seen: dict[str, int] = {}
    unnamed = 0.0
    for idir in items:
        for e in speakers.apply(idir, item_cfg(cfg, idir)).values():
            if name := e.get("name"):
                talk[name] = talk.get(name, 0.0) + e["talk_s"]
                seen[name] = seen.get(name, 0) + 1
            else:
                unnamed += e["talk_s"]
    print(f"{subject}: {len(items)} items named again")
    for name, s in sorted(talk.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<28} {s / 3600:6.1f} h in {seen[name]} items")
    print(f"  {'(not matched)':<28} {unnamed / 3600:6.1f} h")


# ---------- posts ----------

def sheet_csv_url(url: str) -> str:
    """A Google Sheet's CSV export url (the tab in the link's gid, else the first tab)."""
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        raise ClipperError(f"{url}: not a Google Sheets link")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=csv" + (f"&gid={gid[1]}" if gid else "")


def save_posts(cfg: dict, subject: str, url: str) -> None:
    """A public sheet of the subject's posts (On Cinema's: every tweet and Bluesky post by Tim,
    Gregg and the show, kept by the fans) as posts.csv as exported and posts.json, one row per
    post keyed by the sheet's header. Rerun to refresh: the sheet is kept up to date."""
    r = requests.get(sheet_csv_url(url), timeout=120)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    if "csv" not in r.headers.get("content-type", "") or text.lstrip().startswith("<"):
        # a sign-in page or Drive's interstitial: keep the posts saved last time
        raise ClipperError(f"{url}: Google sent a page, not CSV (is the sheet still shared publicly?)")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ClipperError(f"{url}: no rows (is the sheet shared publicly?)")
    d = subject_dir(cfg, subject)
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write_text(d / "posts.csv", text)
    jsonstate.write(d / "posts.json", {"source": url, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                       "posts": rows}, indent=1)
    first = next(iter(rows[0]))
    by = {}
    for row in rows:
        by[row[first]] = by.get(row[first], 0) + 1
    top = ", ".join(f"{k or '(blank)'} {n}" for k, n in sorted(by.items(), key=lambda kv: -kv[1])[:6])
    print(f"{subject}: {len(rows)} posts saved to {d / 'posts.json'} ({top})")


def status(cfg: dict, subject: str | None) -> None:
    root = cfg["paths"]["archive"]
    subjects = [subject] if subject else sorted(p.name for p in root.glob("*") if (p / "catalog.json").exists())
    for s in subjects:
        d = subject_dir(cfg, s)
        cat = load_catalog(d)
        done = [it for it in cat.values() if (d / it["id"] / "words.jsonl").exists()]
        errors = [it for it in cat.values() if (d / it["id"] / "error.txt").exists()]
        cost = 0.0
        for it in done:  # the cost record heads the file: skip reading the segments
            cost += (head_record(d / it["id"] / "transcript.json") or {}).get("est_usd", 0.0)
        hours = sum(i["duration_s"] for i in done) / 3600
        total = sum(i["duration_s"] for i in cat.values()) / 3600
        print(f"{s}: {len(done)}/{len(cat)} transcribed ({hours:.0f}/{total:.0f} h), "
              f"{len(errors)} errors, ~${cost:.2f} spent")
        links = load_links(d)
        if links:
            n = {k: sum(1 for i in cat if links.get(i, {}).get("status") == k)
                 for k in ("alive", "restricted", "dead")}
            kept = sum(1 for i in cat if links.get(i, {}).get("status") == "dead"
                       and (d / i / "words.jsonl").exists())
            print(f"  links: {n['alive']} alive, {n['restricted']} restricted, {n['dead']} dead "
                  f"({kept} of them transcribed), {len(cat) - sum(n.values())} unchecked")


# ---------- link checks ----------

def check_youtube(url: str, session: requests.Session) -> tuple[str, str]:
    """(status, reason) from YouTube's oEmbed endpoint; unknown when it can't be asked."""
    reason = ""
    for attempt in range(3):
        try:
            r = session.get(YT_OEMBED, params={"url": url, "format": "json"}, timeout=30)
        except requests.RequestException as e:
            reason = type(e).__name__
        else:
            if r.status_code == 200:
                return "alive", ""
            if r.status_code in (401, 403):  # private, or embedding turned off (still plays on YouTube)
                return "restricted", f"http {r.status_code}: private or embedding off"
            if r.status_code in (400, 404, 410):  # deleted, removed, or the channel terminated
                return "dead", f"http {r.status_code}"
            reason = f"http {r.status_code}"  # 429 and 5xx: back off and retry
        time.sleep(5 * 2 ** attempt)
    return "unknown", reason


def check_odysee(urls: list[str]) -> dict[str, tuple[str, str]]:
    try:
        res = odysee("resolve", {"urls": urls})
    except (requests.RequestException, KeyError, ValueError) as e:
        return {u: ("unknown", type(e).__name__) for u in urls}
    out = {}
    for u in urls:
        c = res.get(u)
        err = (c or {}).get("error")
        if c is None:
            out[u] = ("unknown", "not in resolve result")
        elif not err:
            out[u] = ("alive", "")
        elif err.get("name") == "NOT_FOUND":
            out[u] = ("dead", "NOT_FOUND")
        else:  # e.g. BLOCKED (filtered by Odysee)
            out[u] = ("restricted", err.get("name") or "error")
    return out


def check_links(cfg: dict, subject: str, ids: list[str] | None, everything: bool,  # noqa: C901
                limit: int | None) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    ac = cfg["archive"]
    links = load_links(d)
    if ids:
        missing = [i for i in ids if i not in cat]
        if missing:
            raise ClipperError(f"not in {subject}'s catalog: {' '.join(missing)}")
        todo = [cat[i] for i in ids]
    else:
        cutoff = datetime.now(UTC) - timedelta(days=ac["link_recheck_days"])
        todo = [it for it in cat.values() if everything or it["id"] not in links
                or datetime.fromisoformat(links[it["id"]]["checked_at"]) < cutoff]
    todo = todo[:limit] if limit else todo
    print(f"{subject}: checking {len(todo)} links")
    counts = {"alive": 0, "restricted": 0, "dead": 0, "unknown": 0}
    changed: list[tuple[dict, str, str]] = []
    streak = 0
    stop_after = ac["stop_after_failures"]

    def record(it: dict, status: str, reason: str) -> None:
        nonlocal streak
        counts[status] += 1
        if status == "unknown":  # keep the last result; it is checked again next time
            streak += 1
            log.warning("%s: could not check (%s)", it["id"], reason)
            return
        streak = 0
        now = datetime.now(UTC).isoformat(timespec="seconds")
        old = links.get(it["id"], {})
        if old and old["status"] != status:
            changed.append((it, old["status"], status))
        links[it["id"]] = {"status": status, "reason": reason, "checked_at": now,
                           "since": old["since"] if old.get("status") == status else now}

    def progress() -> None:
        n = sum(counts.values())
        if n % 200 == 0 or n == len(todo):
            save_json(d, "links.json", links)
            print(f"[{n}/{len(todo)}] {counts}", flush=True)

    od = [it for it in todo if it["source"] == "odysee"]
    for i in range(0, len(od), 50):
        batch = od[i:i + 50]
        res = check_odysee([it["url"] for it in batch])
        for it in batch:
            record(it, *res[it["url"]])
            progress()

    yt = [it for it in todo if it["source"] == "youtube"]
    local = threading.local()

    def one(it: dict) -> tuple[str, str]:
        if not hasattr(local, "session"):
            local.session = requests.Session()
        return check_youtube(it["url"], local.session)

    with ThreadPoolExecutor(ac["link_parallel"]) as pool:
        jobs = {pool.submit(one, it): it for it in yt}
        for job in as_completed(jobs):
            if job.cancelled():
                continue
            record(jobs[job], *job.result())
            progress()
            if stop_after and streak >= stop_after:
                # YouTube refusing this IP: stop before it also blocks pre-show grabs
                log.warning("%d links in a row could not be checked: stopping (rerun to resume)", streak)
                pool.shutdown(wait=True, cancel_futures=True)
                break
    save_json(d, "links.json", links)
    print(f"{subject}: {counts['alive']} alive, {counts['restricted']} restricted, "
          f"{counts['dead']} dead, {counts['unknown']} could not be checked")
    for it, was, now in changed:
        kept = " (transcript kept)" if (d / it["id"] / "words.jsonl").exists() else ""
        print(f"  {was} -> {now}{kept}: {it['id']} {it['title'][:60]!r} {it['url']}")
    if ids:
        for it in todo:
            lk = links.get(it["id"], {"status": "unknown", "reason": "could not be checked"})
            print(f"  {lk['status']:<10} {it['id']} {it['title'][:60]!r} {it['url']} {lk['reason']}")
    elif counts["dead"] + counts["restricted"]:
        print(f"list them: clipper archive links {subject}")


def list_links(cfg: dict, subject: str, status: str | None) -> None:
    """Items by link status, most recent change first; default every link that isn't alive."""
    d = subject_dir(cfg, subject)
    cat, links = load_catalog(d), load_links(d)
    if not links:
        raise ClipperError(f"no link checks for {subject!r} yet: clipper archive check {subject}")
    rows = [(i, lk) for i, lk in links.items()
            if (lk["status"] != "alive" if status is None else status in ("all", lk["status"]))]
    for i, lk in sorted(rows, key=lambda r: r[1]["since"], reverse=True):
        it = cat.get(i, {})
        kept = "transcribed" if (d / i / "words.jsonl").exists() else ""
        print(f"{lk['status']:<10} since {lk['since'][:10]}  {kept:<11}  {i}  "
              f"{it.get('title', '')[:60]!r}  {it.get('url', '')}  {lk['reason']}")
    print(f"{len(rows)} items")

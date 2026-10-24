"""`clipper archive`: other people's back catalogs, listed and transcribed on Modal.

Usage:
    clipper archive add <subject> <url>...     list a source's long-form videos into the catalog
    clipper archive transcribe <subject> [--limit N] [--oldest] [--gpu L4] [--parallel N]
                                         [--dry-run]
    clipper archive status [<subject>]

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
Items shorter than [archive] min_duration_s are skipped. Catalog order is newest first.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests

from . import transcribe
from .modalmon import head_record, tracked
from .preshow import ytdlp

log = logging.getLogger(__name__)

ODYSEE_API = "https://api.na-backend.odysee.com/api/v1/proxy"


def subject_dir(cfg: dict, subject: str) -> Path:
    return cfg["paths"]["archive"] / subject


def load_catalog(d: Path) -> dict[str, dict]:
    p = d / "catalog.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_catalog(d: Path, cat: dict[str, dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "catalog.json.tmp"
    tmp.write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "catalog.json")


# ---------- listing ----------

def list_youtube(url: str) -> list[dict]:
    url = url.rstrip("/")
    tabs = [url] if url.endswith(("/videos", "/streams")) else [f"{url}/videos", f"{url}/streams"]
    items = []
    for tab in tabs:
        r = ytdlp("--flat-playlist", "-J", tab)
        if r.returncode and len(tabs) > 1 and "does not have" in r.stderr:
            continue  # e.g. no streams tab
        if r.returncode:
            raise SystemExit(f"yt-dlp {tab}: {r.stderr.strip().splitlines()[-1:]}")
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


def list_odysee(url: str) -> list[dict]:
    name = "@" + url.split("@", 1)[1].split("/")[0].split(":")[0]
    ch = odysee("resolve", {"urls": [f"lbry://{name}"]})[f"lbry://{name}"]
    if "error" in ch:
        raise SystemExit(f"odysee {name}: {ch['error'].get('name')}")
    items, page = [], 1
    while True:
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
        if page >= r.get("total_pages", 1):
            return items
        page += 1


def add(cfg: dict, subject: str, urls: list[str]) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    min_s = cfg["archive"]["min_duration_s"]
    for url in urls:
        found = list_odysee(url) if "odysee.com" in url or url.startswith("lbry://") else list_youtube(url)
        keep = [it for it in found if not 0 < it["duration_s"] < min_s]
        new = [it for it in keep if it["id"] not in cat]
        cat.update({it["id"]: it for it in new})
        print(f"{url}: {len(found)} found, {len(found) - len(keep)} under {min_s} s skipped, "
              f"{len(new)} new ({sum(i['duration_s'] for i in new) / 3600:.0f} h)")
    save_catalog(d, cat)
    print(f"{subject}: {len(cat)} items, {sum(i['duration_s'] for i in cat.values()) / 3600:.0f} h")


# ---------- transcribing ----------

def estimate_usd(hours: float, gpu: str) -> float:
    from .modal_app import GPU_PRICES, SPEED

    return hours / SPEED.get(gpu, 30.0) * GPU_PRICES.get(gpu, 0.0)


def transcribe_item(cfg: dict, idir: Path, it: dict, gpu: str) -> dict:
    """One item: words.jsonl and transcript.json in idir; returns the cost record."""
    tc = dict(cfg["transcribe"], backend="modal")
    idir.mkdir(parents=True, exist_ok=True)
    meta = dict(it)
    t0 = time.time()
    worker = transcribe.modal_worker(tc, gpu)

    def call(fn, *args) -> tuple[dict, dict]:  # logged for `clipper modal` and the war room
        with tracked(cfg["paths"]["modal_runs"], "archive", f"{idir.parent.name}/{it['id']}", gpu,
                     meta.get("duration_s") or 0) as rec:
            t1 = time.time()
            out = fn.remote(*args)
            cost = transcribe.modal_cost(gpu, time.time() - t1, out)
            rec.update(cost, audio_s=round(out["dur"], 1))
        return out, cost

    if it["source"] == "youtube":
        r = ytdlp("-f", cfg["archive"]["youtube_format"], "--write-info-json", "--no-playlist",
                  "-o", str(idir / "audio.%(ext)s"), it["url"])
        audio = [p for p in idir.glob("audio.*") if p.suffix not in (".json", ".part")]
        if r.returncode or not audio:
            raise RuntimeError(f"yt-dlp: {r.stderr.strip().splitlines()[-1:]}")
        info_path = idir / "audio.info.json"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info_path.unlink()
        ts = info.get("timestamp") or info.get("release_timestamp")
        meta.update(title=info.get("title") or it["title"], description=info.get("description") or "",
                    duration_s=float(info.get("duration") or it["duration_s"]),
                    date=datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else None)
        data = audio[0].read_bytes()
        out, cost = call(worker.transcribe, data, audio[0].suffix, transcribe.modal_settings(tc))
        audio[0].unlink()
    else:
        media = odysee("get", {"uri": it["url"]})["streaming_url"]
        out, cost = call(worker.transcribe_url, media, transcribe.modal_settings(tc))
    meta["transcribed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (idir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    transcribe.write(idir, tc, out["segments"], out["dur"], t0, {"modal": cost})
    (idir / "error.txt").unlink(missing_ok=True)
    return cost


def run_transcribe(cfg: dict, subject: str, limit: int | None, oldest: bool, gpu: str,
                   parallel: int, dry_run: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise SystemExit(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    todo = [it for it in cat.values() if not (d / it["id"] / "words.jsonl").exists()]
    if oldest:
        todo.reverse()
    todo = todo[:limit] if limit else todo
    hours = sum(it["duration_s"] for it in todo) / 3600
    print(f"{subject}: {len(todo)} to transcribe, {hours:.1f} h, on {gpu} x{parallel}: "
          f"~${estimate_usd(hours, gpu):.2f} (measured speed; upload and cold starts extra)")
    if dry_run or not todo:
        return
    spent, done, failed, streak = 0.0, 0, 0, 0
    stop_after = cfg["archive"]["stop_after_failures"]
    with ThreadPoolExecutor(parallel) as pool:
        jobs = {pool.submit(transcribe_item, cfg, d / it["id"], it, gpu): it for it in todo}
        for job in as_completed(jobs):
            it = jobs[job]
            if job.cancelled():
                continue
            try:
                spent += job.result()["est_usd"]
                done += 1
                streak = 0
            except Exception as e:  # noqa: BLE001
                failed += 1
                streak += 1
                (d / it["id"]).mkdir(parents=True, exist_ok=True)
                (d / it["id"] / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                log.warning("%s failed: %s: %s", it["id"], type(e).__name__, str(e)[:200])
            print(f"[{done + failed}/{len(todo)}] {it['id']} {it['title'][:50]!r}  "
                  f"(spent ~${spent:.2f})", flush=True)
            if stop_after and streak >= stop_after:
                # e.g. YouTube's bot check on this IP: stop before it also blocks pre-show grabs
                log.warning("%d failures in a row: stopping (rerun to resume)", streak)
                pool.shutdown(wait=True, cancel_futures=True)
                break
    print(f"{subject}: {done} transcribed, {failed} failed, ~${spent:.2f}")


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

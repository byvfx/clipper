"""Decisions and edits from the page: one item (through clipper/actions.py, so Kitsu stays the
record), bulk actions, the next open slot, find and replace, undo, and the sync/replan
requests the bot and the page send serve."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import actions, jsonstate, review, schedule
from ..config import for_stream
from ..errors import ClipperError
from ..states import RELEASE_KIND
from .data import all_rows, row_json
from .state import HOOKS, LOCK, SYNC, WHO


def start_retake(cfg: dict) -> bool:
    """A retake in the background: on `clipper serve`'s job runner when this page is its
    (HOOKS), else a detached `clipper retake` (the page run alone)."""
    if HOOKS["retake"]:
        HOOKS["retake"]()
        return True
    if (cfg["paths"]["streams"] / ".retake.lock").exists():
        return False
    cmd = actions.clipper_cmd(cfg)
    log = Path(cfg["paths"]["serve_log"]).with_name("retake.log") if cfg["paths"]["serve_log"] else Path(os.devnull)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} war room\n")
        subprocess.Popen([*cmd, "retake"], stdout=out, stderr=subprocess.STDOUT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True

REPLANS = actions.REPLANS

COLUMN_OF = {"title": "YouTube title", "description": "YouTube description", "hashtags": "Hashtags",
             "tags": "Tags", "playlist": "Playlist"}  # row field -> Kitsu column

ROW_OF = {c: f for f, c in COLUMN_OF.items()}

UNDO_MAX = 20

def _apply(cfg: dict, item: actions.Item, action: str, body: dict, who: str) -> dict:
    return actions.decide(cfg, item, action, who, text=body.get("text", ""),
                          on=bool(body.get("on", True)), values=body.get("values"))

def _before(cfg: dict, item: actions.Item) -> dict:
    """What an undo puts back: the item's status, hold, pin and texts as the page shows them."""
    r = actions.describe(cfg, item.stream_dir)[item.key]
    return {k: r[k] for k in ("status", "decision", "hold", "publish_at", "thumb_text", "uploaded",
                              *COLUMN_OF)}

def undo_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["warroom_undo"])

def remember(cfg: dict, action: str, label: str, done: list[dict]) -> str:
    """done: [{"ref", "before", "values"?}]. Returns the undo id."""
    uid = f"{int(time.time() * 1000):x}"
    entry = {"id": uid, "at": datetime.now(UTC).isoformat(timespec="seconds"), "action": action,
             "label": label, "items": done, "undone": False}
    undo_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(undo_path(cfg), {"actions": []}) as d:
        d["actions"] = [entry, *d["actions"]][:UNDO_MAX]
    return uid

def recent_actions(cfg: dict) -> list[dict]:
    d = jsonstate.read(undo_path(cfg), {"actions": []})
    return [{k: a[k] for k in ("id", "at", "action", "label", "undone")}
            | {"n": len(a["items"]), "uploaded": sum(1 for x in a["items"] if x["before"].get("uploaded"))}
            for a in d["actions"]]

def _inverse(cfg: dict, item: actions.Item, action: str, before: dict, values: dict, who: str) -> dict:
    if action in ("approve", "reject", "changes", "reopen"):
        return actions.restore(cfg, item, before["status"], who)
    if action == "hold":
        return actions.hold(cfg, item, before["hold"], who)
    if action == "pin":
        return actions.pin(cfg, item, before["publish_at"], who)
    if action in ("edit", "replace"):
        return actions.edit_meta(cfg, item, {c: before[ROW_OF[c]] for c in values}, who)
    if action == "thumbtext":
        return actions.thumbtext(cfg, item, before["thumb_text"], who)
    raise ClipperError(f"{action} can't be undone")

def do_undo(cfg: dict, uid: str) -> dict:
    d = jsonstate.read(undo_path(cfg), {"actions": []})
    entry = next((a for a in d["actions"] if a["id"] == uid), None)
    if not entry:
        raise ClipperError(f"that action is too old to undo here (only the last {UNDO_MAX} are kept)")
    if entry["undone"]:
        raise ClipperError("already undone")
    who = f"{WHO} (undo)"
    ok, failed, rows = 0, [], []
    with LOCK:
        for x in entry["items"]:
            try:
                item = actions.resolve(cfg, x["ref"])
                rows.append(row_json(item.stream_dir, _inverse(cfg, item, entry["action"], x["before"],
                                                               x.get("values") or {}, who)))
                ok += 1
            except Exception as e:  # noqa: BLE001  # report each, undo the rest
                failed.append({"ref": x["ref"], "error": str(e)[:200]})
        if ok and cfg["discord"]["replan"] and entry["action"] in REPLANS:
            schedule.run(cfg, sync_first=False)
    with jsonstate.update(undo_path(cfg), {"actions": []}) as d:
        for a in d["actions"]:
            if a["id"] == uid:
                a["undone"] = not failed
    up = sum(1 for x in entry["items"] if x["before"].get("uploaded"))
    note = f"{up} already on YouTube: change {'it' if up == 1 else 'those'} in Studio too" if up else ""
    return {"undone": ok, "failed": failed, "rows": rows, "note": note}

REF = re.compile(r"(?:[\w.-]+/)?[\w.-]+")  # <stream>/<key> or a key: names, never paths (#76)

def checked_ref(ref) -> str:
    if not isinstance(ref, str) or not REF.fullmatch(ref) or ".." in ref:
        raise ClipperError(f"not an item name: {str(ref)[:60]!r}")
    return ref

def do_action(cfg: dict, body: dict) -> dict:
    action, ref = body.get("action", ""), checked_ref(body.get("ref", ""))
    with LOCK:
        item = actions.resolve(cfg, ref)
        before = _before(cfg, item)
        row, note = actions.replan(cfg, item, action, _apply(cfg, item, action, body, WHO))
    uid = remember(cfg, action, f"{action} {item.short}",
                   [{"ref": item.ref, "before": before, "values": body.get("values") or {}}])
    if action == "changes" and cfg["retake"]["auto"]:
        note = "clipper is working on the retake" if start_retake(cfg) else \
            "a retake run is going; this one is next"
    return {"row": row_json(item.stream_dir, row), "note": note, "undo": uid}

BULK = ("approve", "reopen", "hold", "pin", "changes", "reject")

def do_bulk(cfg: dict, body: dict) -> dict:
    action, refs = body.get("action", ""), [checked_ref(r) for r in body.get("refs") or []]
    if action not in BULK:
        raise ClipperError(f"{action!r} isn't a bulk action")
    if not refs:
        raise ClipperError("nothing selected")
    done, failed, rows = [], [], []
    with LOCK:
        for ref in refs:
            try:
                item = actions.resolve(cfg, ref)
                before = _before(cfg, item)
                rows.append(row_json(item.stream_dir, _apply(cfg, item, action, body, WHO)))
                done.append({"ref": item.ref, "before": before})
            except Exception as e:  # noqa: BLE001  # report each, do the rest
                failed.append({"ref": ref, "error": str(e)[:200]})
        if done and cfg["discord"]["replan"] and action in REPLANS:
            schedule.run(cfg, sync_first=False)
    uid = remember(cfg, action, f"{action} {len(done)} items", done) if done else ""
    if done and action == "changes" and cfg["retake"]["auto"]:
        start_retake(cfg)
    return {"done": len(done), "failed": failed, "rows": rows, "undo": uid}

SLOT_KINDS = ("clip", "short", "segment")  # a compilation waits for its segments: pin it by hand

def slot_plan(cfg: dict, refs: list[str] | None = None, kind: str = "") -> dict:
    """refs: these items; or kind: every approved one of that kind without a slot, not held.
    {"plan": [{ref, title, kind, at, text, let_go}], "refused": [{ref, title, why}],
    "bumped": [{ref, title, at, why}]}; let_go: why the scheduler won't send it (a pin will)."""
    sc = cfg["schedule"]
    tz = ZoneInfo(sc["timezone"])
    now = datetime.now(tz)
    rows = {r["ref"]: r for r in all_rows(cfg)}
    every = refs is None
    if every:
        refs = [ref for ref, r in rows.items() if RELEASE_KIND[r["kind"]] == kind
                and r["decision"] == "approved" and not r["scheduled"] and not r["hold"]]
    entries = (jsonstate.read_or(cfg["paths"]["schedule"], {}) or {}).get("entries", [])
    mine = set(refs)
    lead = now + timedelta(minutes=cfg["youtube"]["min_lead_minutes"])
    windows: dict[str, tuple] = {}
    refused, todo = [], []
    for ref in refs:
        r = rows.get(ref)
        k = RELEASE_KIND[r["kind"]] if r else ""
        why = ("not in review" if not r else "not approved" if r["decision"] != "approved"
               else "held" if r["hold"] else "already on YouTube" if r["uploaded"]
               else f"a {k} goes after its segments: pin it by hand" if k not in SLOT_KINDS else "")
        if why:
            refused.append({"ref": ref, "title": r["title"] if r else ref, "why": why})
        else:
            todo.append(r)
    keeps, full = None, set()  # full: streams where a pin would bump an automatic entry
    while True:
        taken = [datetime.fromisoformat(e["publish_at"]).astimezone(tz) for e in entries
                 if e["key"] not in mine]
        plan, late = [], []
        for r in todo:
            k, stream = RELEASE_KIND[r["kind"]], r["stream"]
            if stream not in windows:
                sdir = cfg["paths"]["streams"] / stream
                windows[stream] = schedule.release_window(cfg, sdir, jsonstate.read_or(sdir / "meta.json", {}))
            earliest, expires = windows[stream]
            when = schedule.next_free(k, taken, sc, tz, max(lead, earliest))
            why = ("its stream's budget is full: a pin would only bump another" if stream in full
                   else f"no open {k} slot in {sc['horizon_days']} days" if not when
                   else f"stale: no open {k} slot before {expires.astimezone(tz):%m-%d %H:%M}"
                   if every and expires and when > expires else "")
            if why:
                late.append({"ref": r["ref"], "title": r["title"], "why": why})
                continue
            taken.append(when)
            plan.append({"ref": r["ref"], "title": r["title"], "kind": k, "at": when.isoformat(),
                         "text": f"{when:%Y-%m-%d %H:%M}", "let_go": r.get("dropped") or ""})
        bumped = []
        if plan:  # what a replan with the pins drops that one without them keeps
            keeps = keeps if keeps is not None else schedule.with_pins(cfg, {})[0]
            placed, skipped = schedule.with_pins(cfg, {p["ref"]: p["text"] for p in plan})
            reason = {s["key"]: s["reason"] for s in skipped}
            bumped = [{"ref": k, "title": e["title"], "at": e["publish_at"], "why": reason.get(k, "")}
                      for k, e in keeps.items() if k not in placed and k not in mine]
        more = ({b["ref"].split("/")[0] for b in bumped}
                & {p["ref"].split("/")[0] for p in plan}) - full
        if not every or not more:
            return {"plan": plan, "refused": refused + late, "bumped": bumped}
        full |= more

def slot_print(out: dict) -> str:
    """What a preview showed, to tell whether Apply would do the same: the pins and the bumps."""
    return json.dumps([sorted((p["ref"], p["text"]) for p in out["plan"]),
                       sorted(b["ref"] for b in out["bumped"])])

def do_slot(cfg: dict, body: dict) -> dict:
    """{"refs": [...]} or {"kind": "segment"} (every one without a slot); dry_run: only the
    plan, with its "print". Otherwise pin each item to its slot and replan once, if the plan
    is still the previewed one (body "print"); if not: the new plan, "changed", nothing done."""
    refs = [checked_ref(r) for r in body["refs"]] if body.get("refs") else None
    kind = body.get("kind", "")
    if refs is None and kind not in SLOT_KINDS:
        raise ClipperError("slot what? refs, or a kind: " + ", ".join(SLOT_KINDS))
    if body.get("dry_run"):
        out = slot_plan(cfg, refs, kind)
        return {**out, "print": slot_print(out)}
    done, failed, rows = [], [], []
    with LOCK:  # planned again: the schedule may have moved since the preview
        out = slot_plan(cfg, refs, kind)
        if slot_print(out) != body.get("print"):
            return {**out, "print": slot_print(out), "changed": True, "done": 0}
        for p in out["plan"]:
            try:
                item = actions.resolve(cfg, p["ref"])
                before = _before(cfg, item)
                rows.append(row_json(item.stream_dir, _apply(cfg, item, "pin", p, WHO)))
                done.append({"ref": item.ref, "before": before})
            except Exception as e:  # noqa: BLE001  # report each, do the rest
                failed.append({"ref": p["ref"], "error": str(e)[:200]})
        if done and cfg["discord"]["replan"]:
            schedule.run(cfg, sync_first=False)
    uid = remember(cfg, "pin", f"pin {len(done)} to the next open slot{'s' * (len(done) > 1)}",
                   done) if done else ""
    return {**out, "done": len(done), "failed": failed, "rows": rows, "undo": uid}

def replace_plan(rows: list[dict], fields: list[str], find: str, repl: str, regex: bool,
                 case: bool, word: bool = False) -> list[dict]:
    """[{ref, short, values: {column: new}, lines: [{field, before, after}]}] for the rows
    whose fields change."""
    import re

    if not find:
        raise ClipperError("find what?")
    if not fields or any(f not in COLUMN_OF for f in fields):
        raise ClipperError(f"fields are {', '.join(COLUMN_OF)}")
    try:
        pat = find if regex else re.escape(find)
        pat = re.compile(rf"\b(?:{pat})\b" if word else pat, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ClipperError(f"bad pattern: {e}") from None
    out = []
    for r in rows:
        values, lines = {}, []
        for f in fields:
            old = r.get(f) or ""
            try:
                new = pat.sub(repl if regex else lambda _m: repl, old)
            except (re.error, IndexError) as e:
                raise ClipperError(f"bad replacement: {e}") from None
            if new == old:
                continue
            values[COLUMN_OF[f]] = new
            olds, news = old.splitlines() or [""], new.splitlines() or [""]
            lines += [{"field": f, "before": a, "after": b}
                      for a, b in zip(olds, news, strict=False) if a != b]
            if len(olds) != len(news):
                lines.append({"field": f, "before": f"({len(olds)} lines)", "after": f"({len(news)} lines)"})
        if values:
            out.append({"ref": r["ref"], "short": r["short"], "values": values, "lines": lines,
                        "uploaded": r.get("uploaded", False)})
    return out

def do_replace(cfg: dict, body: dict) -> dict:
    refs = {checked_ref(r) for r in body.get("refs") or []}
    if not refs:
        raise ClipperError("nothing selected")
    rows = [r for r in all_rows(cfg) if r["ref"] in refs]
    plan = replace_plan(rows, body.get("fields") or [], body.get("find", ""), body.get("replace", ""),
                        bool(body.get("regex")), bool(body.get("case")), bool(body.get("word")))
    if body.get("dry_run", True):
        return {"dry_run": True, "items": plan, "checked": len(rows)}
    done, failed, out_rows = [], [], []
    with LOCK:
        for p in plan:
            try:
                item = actions.resolve(cfg, p["ref"])
                before = _before(cfg, item)
                out_rows.append(row_json(item.stream_dir, actions.edit_meta(cfg, item, p["values"], WHO)))
                done.append({"ref": item.ref, "before": before, "values": p["values"]})
            except Exception as e:  # noqa: BLE001
                failed.append({"ref": p["ref"], "error": str(e)[:200]})
    uid = remember(cfg, "replace", f"replace {body.get('find', '')!r} in {len(done)} items", done) if done else ""
    return {"dry_run": False, "done": len(done), "failed": failed, "rows": out_rows, "undo": uid}

def sync_all(cfg: dict, stream: str | None = None) -> None:
    if SYNC["running"]:
        return
    SYNC.update(running=True, error="")

    def run():
        try:
            dirs = ([cfg["paths"]["streams"] / stream] if stream else actions.review_dirs(cfg))
            for d in dirs:
                review.sync(d, cfg, quiet=True)
        except Exception as e:  # noqa: BLE001
            SYNC["error"] = str(e)[:200]
        SYNC.update(running=False, last=datetime.now(UTC).isoformat(timespec="seconds"))

    threading.Thread(target=run, daemon=True).start()

def _sync(cfg: dict, body: dict) -> dict:
    stream = body.get("stream")
    sync_all(cfg, checked_ref(stream) if stream else None)
    return {"started": True}

def _replan(cfg: dict, body: dict) -> dict:
    """`clipper schedule` here (serve owns schedule.json); "sync": true reads Kitsu first."""
    with LOCK:
        schedule.run(cfg, sync_first=bool(body.get("sync")))
    return {"ok": True}

def _check(cfg: dict, body: dict) -> dict:  # #19: style warnings while typing
    item = actions.resolve(cfg, checked_ref(body.get("ref", "")))
    return {"issues": actions.style_issues(for_stream(cfg, item.stream_dir), body.get("values") or {})}

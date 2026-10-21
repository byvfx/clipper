"""clipper command line: one subcommand per pipeline stage."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import config
from .config import stream_dir


def cmd_fetch(cfg: dict, args: argparse.Namespace) -> None:
    from . import kick

    meta = kick.resolve(args.url, cfg)
    d = cfg["paths"]["streams"] / meta["stream_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{meta['stream_id']}: {meta['title']} ({meta['duration_s'] / 3600:.2f} h)")
    if not args.no_chat:
        kick.fetch_chat(d, meta, cfg)
    if not args.no_video:
        kick.fetch_video(d, meta, cfg)


def cmd_record(cfg: dict, args: argparse.Namespace) -> None:
    from . import record

    record.setup_logging()
    if args.action == "finalize":
        record.finalize(stream_dir(cfg, args.stream), cfg)
        return
    chans = record.channel_list(cfg["record"])
    if args.action == "now":
        known = {c["slug"]: c for c in chans}
        chans = [known.get(args.channel) or {**cfg["record"], "slug": args.channel}]
        if args.max_s:
            chans[0]["end_grace_s"] = 0
    elif args.channel:
        chans = [c for c in chans if c["slug"] in args.channel]
    if not chans:
        raise SystemExit("record: no channels; set [record] channels in clipper.toml")
    once = args.action == "now"
    record.Watcher(cfg, chans, max_s=getattr(args, "max_s", None), once=once).run(until_done=once)


def cmd_preshow(cfg: dict, args: argparse.Namespace) -> None:
    from . import preshow

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.action == "list":
        preshow.show_list(cfg)
    elif args.action == "check":
        got = preshow.check(cfg)
        print(f"{len(got)} new: {', '.join(d.name for d in got)}" if got else "nothing new")
        if args.run:
            preshow.run_pending(cfg, config_path(args))
    else:
        keys = [k for k, _ in preshow.watched(cfg)]
        key = cfg["show"]["key"] or (keys[0] if len(keys) == 1 else "")
        if not key:
            raise SystemExit(f"preshow fetch: say which show with --show ({', '.join(cfg['shows'])})")
        d = preshow.fetch(cfg, args.url, key)
        print(f"{d.name}: grabbed; the worker runs it, or: clipper run {d.name}")


def cmd_transcribe(cfg: dict, args: argparse.Namespace) -> None:
    from . import transcribe

    transcribe.run(stream_dir(cfg, args.stream), cfg["transcribe"])


def cmd_signals(cfg: dict, args: argparse.Namespace) -> None:
    from . import signals

    signals.run(stream_dir(cfg, args.stream), cfg["signals"])


def cmd_score(cfg: dict, args: argparse.Namespace) -> None:
    from . import scoring

    scoring.run(stream_dir(cfg, args.stream), cfg, dry_run=args.dry_run)


def cmd_segments(cfg: dict, args: argparse.Namespace) -> None:
    from . import segments

    segments.run(stream_dir(cfg, args.stream), cfg, force=args.force)


def cmd_cut(cfg: dict, args: argparse.Namespace) -> None:
    from . import cut

    d = stream_dir(cfg, args.stream)
    what = args.what or ["segments", "compilations", "picks"]
    bad = set(what) - {"segments", "compilations", "picks"}
    if bad:
        raise SystemExit(f"cut: unknown {sorted(bad)}; use segments, compilations, picks")
    if "segments" in what:
        cut.cut_segments(d, cfg, args.ids.split(",") if args.ids else None)
    if "compilations" in what:
        cut.cut_compilations(d, cfg)
    if "picks" in what:
        cut.cut_picks(d, cfg)


def cmd_meta(cfg: dict, args: argparse.Namespace) -> None:
    from . import meta

    d = stream_dir(cfg, args.stream)
    meta.captions_only(d, cfg) if args.captions_only else meta.run(d, cfg, missing=args.missing)


def cmd_thumbs(cfg: dict, args: argparse.Namespace) -> None:
    from . import thumbs

    if args.photo is not None or args.teaser_photo is not None:
        if not args.ids:
            raise SystemExit("--photo/--teaser-photo need --ids")
        changed = thumbs.set_photos(stream_dir(cfg, args.stream), cfg, args.ids.split(","),
                                    photo=args.photo, teaser_photo=args.teaser_photo,
                                    kitsu=not args.no_kitsu)
        print(f"{len(changed)} changed: {', '.join(changed)}" if changed else "nothing to change")
        return
    if args.shorts:
        n = thumbs.shorts_only(stream_dir(cfg, args.stream), cfg, kitsu=not args.no_kitsu)
        print(f"{n} Short covers")
        return
    thumbs.run(stream_dir(cfg, args.stream), cfg, ids=args.ids.split(",") if args.ids else None,
               force=args.force, compose_only=args.compose_only, kitsu=not args.no_kitsu,
               missing=args.missing)


def cmd_shorts(cfg: dict, args: argparse.Namespace) -> None:
    from . import shorts

    if args.save_example:
        if args.at is None:
            raise SystemExit("--save-example needs --at <seconds into the stream>")
        shorts.save_example(stream_dir(cfg, args.stream), cfg, args.save_example, args.at)
        return
    shorts.run(stream_dir(cfg, args.stream), cfg, ids=args.ids.split(",") if args.ids else None,
               layout=args.layout, force=args.force, rescene=args.rescene, check=args.check)


def cmd_retake(cfg: dict, args: argparse.Namespace) -> None:
    from . import retake

    retake.run(cfg, args.stream, item=args.item, comment=args.comment, dry_run=args.dry_run,
               sync=not args.no_sync)


def cmd_review(cfg: dict, args: argparse.Namespace) -> None:
    from . import actions, review

    if args.action in ("publish", "sync", "notify"):
        d = stream_dir(cfg, args.stream)
        if args.action == "publish":
            review.publish(d, cfg, force=args.force, notify=not args.no_notify)
        elif args.action == "sync":
            review.sync(d, cfg)
        else:
            from . import notify
            notify.post(d, cfg, keys=args.keys.split(",") if args.keys else None,
                        force=args.force)
        return
    if args.action == "status":
        print("\n".join(actions.status_lines(actions.status(cfg, sync=args.sync))))
        return
    if args.action == "list":
        actions.print_rows(actions.list_items(cfg, args.stream, everything=args.all,
                                              sync=not args.no_sync))
        return
    item = actions.resolve(cfg, args.item)
    text = " ".join(getattr(args, "text", None) or [])
    if getattr(args, "file", None):  # multi-line text, e.g. a description
        text = (sys.stdin.read() if args.file == "-"
                else Path(args.file).read_text(encoding="utf-8-sig"))
    who = "clipper CLI"
    r = {"approve": lambda: actions.approve(cfg, item, text, who),
         "reject": lambda: actions.reject(cfg, item, text, who),
         "changes": lambda: actions.changes(cfg, item, text, who),
         "reopen": lambda: actions.reopen(cfg, item, who),
         "hold": lambda: actions.hold(cfg, item, not args.off, who),
         "pin": lambda: actions.pin(cfg, item, "" if args.clear else text, who),
         "retitle": lambda: actions.retitle(cfg, item, text, who),
         "thumbtext": lambda: actions.thumbtext(cfg, item, text, who),
         "description": lambda: actions.description(cfg, item, text, who),
         "hashtags": lambda: actions.hashtags(cfg, item, text, who),
         "tags": lambda: actions.tags(cfg, item, text, who),
         "playlist": lambda: actions.playlist(cfg, item, text, who)}[args.action]()
    actions.print_rows([r])


def cmd_schedule(cfg: dict, args: argparse.Namespace) -> None:
    from . import schedule

    if args.show:
        schedule.show(cfg, args.days)
        return
    schedule.run(cfg, sync_first=not args.no_sync, write_kitsu=not args.no_kitsu,
                 dry_run=args.dry_run, reshuffle=args.reshuffle)


def cmd_upload(cfg: dict, args: argparse.Namespace) -> None:
    from . import youtube

    if args.next:
        youtube.next_up(cfg, args.next)
        return
    if args.attach:
        youtube.attach(cfg, args.attach[0], args.attach[1], dry_run=args.dry_run)
        return
    youtube.run(cfg, sign_in=args.sign_in, dry_run=args.dry_run, limit=args.max,
                thumbnails_only=args.thumbnails)


def cmd_rumble(cfg: dict, args: argparse.Namespace) -> None:
    from . import rumble

    if args.done or args.undo:
        rumble.mark_done(cfg, args.done or args.undo, args.url or "", undo=bool(args.undo))
    elif args.status:
        rumble.status(cfg, args.stream)
    else:
        rumble.run(cfg, args.stream, args.items.split(",") if args.items else None,
                   open_after=args.open, dry_run=args.dry_run)


def config_path(args: argparse.Namespace) -> str:
    return str(Path(args.config or os.environ.get("CLIPPER_CONFIG") or "clipper.toml").resolve())


def cmd_run(cfg: dict, args: argparse.Namespace) -> None:
    from . import worker

    worker.setup_logging()
    stages = None
    if args.only or args.from_:
        stages = args.only.split(",") if args.only else worker.STAGES[worker.STAGES.index(args.from_):]
        bad = set(stages) - set(worker.STAGES)
        if bad:
            raise SystemExit(f"run: unknown stage {sorted(bad)}; stages: {', '.join(worker.STAGES)}")
    try:
        ok = worker.run_stream(cfg, stream_dir(cfg, args.stream), config_path(args), stages,
                               force=args.force or bool(args.from_))
    except worker.Busy as e:
        raise SystemExit(str(e))
    if not ok:
        raise SystemExit(1)


def cmd_worker(cfg: dict, args: argparse.Namespace) -> None:
    from . import worker

    worker.setup_logging()
    if args.status:
        worker.status(cfg)
    else:
        worker.watch(cfg, config_path(args), once=args.once)


def cmd_shows(cfg: dict, args: argparse.Namespace) -> None:
    from .review import project_name

    by_show: dict[str, list[str]] = {}
    streams = cfg["paths"]["streams"]
    for meta in sorted(streams.glob("*/meta.json")) if streams.is_dir() else []:
        ch = config.stream_channel(meta.parent)
        by_show.setdefault(config.show_of_channel(cfg, ch) or f"(none: {ch or '?'})", []).append(
            meta.parent.name)
    for key, show in cfg["shows"].items():
        sc = config.for_show(cfg, key)
        ids = by_show.pop(key, [])
        print(f"{key:12} {sc['meta']['show_name'] or '(no show_name)':24} "
              f"channels {', '.join(show['channels']) or '-'}; Kitsu {project_name(sc)!r}; "
              f"{len(ids)} streams")
    for key, ids in by_show.items():
        print(f"{key:12} {len(ids)} streams: {', '.join(ids)}")


def cmd_analytics(cfg: dict, args: argparse.Namespace) -> None:
    from . import analytics

    analytics.show(cfg) if args.show else analytics.run(cfg, sign_in=args.sign_in)


def cmd_warroom(cfg: dict, args: argparse.Namespace) -> None:
    from . import warroom

    warroom.run(cfg, port=args.port, open_browser=args.open)


def cmd_doctor(cfg: dict, args: argparse.Namespace) -> None:
    from . import doctor

    raise SystemExit(doctor.main(cfg, offline=args.offline, role=args.role))


def cmd_bot(cfg: dict, args: argparse.Namespace) -> None:
    from . import bot

    bot.run(cfg)


def main() -> None:
    ap = argparse.ArgumentParser(prog="clipper")
    ap.add_argument("--config", help="path to clipper.toml (default: $CLIPPER_CONFIG or ./clipper.toml)")
    ap.add_argument("--show", help="use this [shows.<key>] profile (default: the stream's, by Kick channel)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="resolve a Kick VOD url, pull chat and video")
    p.add_argument("url")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-chat", action="store_true")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("record", help="record live streams with real-time chat (NAS service)")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_record)
    r = rsub.add_parser("watch", help="watch [record] channels; record each stream they go live with")
    r.add_argument("--channel", action="append", help="only this channel (repeatable)")
    r = rsub.add_parser("now", help="record one channel's current (or next) stream, then exit")
    r.add_argument("channel", help="Kick channel slug")
    r.add_argument("--max-s", type=float, help="stop after this many seconds and finalize")
    r = rsub.add_parser("finalize", help="join an interrupted recording's parts and write its files")
    r.add_argument("stream", help="stream id under paths.streams, or a stream dir")

    p = sub.add_parser("preshow", help="grab a show's pre-show from its YouTube channel, as one whole video")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_preshow)
    r = rsub.add_parser("check", help="grab new uploads matching [preshow] title_pattern (the bot does this)")
    r.add_argument("--run", action="store_true", help="then run the pipeline on grabbed pre-shows not done yet")
    rsub.add_parser("list", help="recent pre-shows in the feeds, and which are grabbed")
    r = rsub.add_parser("fetch", help="grab one video by url, any age")
    r.add_argument("url")

    p = sub.add_parser("transcribe", help="WhisperX -> words.jsonl")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.set_defaults(fn=cmd_transcribe)

    p = sub.add_parser("signals", help="chat + loudness heat curve -> windows.json")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.set_defaults(fn=cmd_signals)

    p = sub.add_parser("score", help="Claude Pass A + Pass B -> candidates.json")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("--dry-run", action="store_true", help="write Pass A requests, no API calls")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("segments", help="split the stream into topic segments -> segments.json")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("--force", action="store_true", help="re-split instead of reusing segments.json")
    p.set_defaults(fn=cmd_segments)

    p = sub.add_parser("cut", help="cut segments, topic compilations and Pass B picks")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("what", nargs="*", help="segments, compilations and/or picks (default: all)")
    p.add_argument("--ids", help="only these segment ids, e.g. s03,s07")
    p.set_defaults(fn=cmd_cut)

    p = sub.add_parser("meta", help="upload metadata (SEO title, description, chapters, tags) + SRT")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("--captions-only", action="store_true",
                   help="rewrite .srt and re-mask metadata text, no model calls")
    p.add_argument("--missing", action="store_true",
                   help="only videos with no draft yet; existing drafts stay as they are")
    p.set_defaults(fn=cmd_meta)

    p = sub.add_parser("thumbs", help="thumbnails: pick a frame, write the text -> <video>.jpg")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("--ids", help="only these assets, e.g. t20,s02,comp-the-quartering")
    p.add_argument("--force", action="store_true", help="pick again even if the video is unchanged")
    p.add_argument("--compose-only", action="store_true",
                   help="re-render from thumbnails.json (brand or hand edits), no model calls")
    p.add_argument("--no-kitsu", action="store_true", help="don't attach them in Kitsu")
    p.add_argument("--missing", action="store_true",
                   help="only videos with no thumbnail yet; leave the others' covers alone")
    p.add_argument("--shorts", action="store_true",
                   help="only the Shorts' 9:16 covers, from their clips' choices (no model calls)")
    p.add_argument("--photo", metavar="PERSON",
                   help="enquirer: use [thumbnail] photos/<PERSON> as the --ids covers' face "
                        "(\"\" = back to the frame); re-renders, no model calls")
    p.add_argument("--teaser-photo", metavar="PERSON",
                   help="enquirer: show that photo beside the --ids videos' teasers on the other covers")
    p.set_defaults(fn=cmd_thumbs)

    p = sub.add_parser("shorts", help="vertical Shorts of the picks: layout crop, captions, loudness -> shorts/")
    p.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    p.add_argument("--ids", help="only these picks, e.g. t33,w19 or v01")
    p.add_argument("--layout", help="use this layout (from [shorts] layouts) for the chosen picks")
    p.add_argument("--force", action="store_true", help="re-render even if unchanged (keeps layouts)")
    p.add_argument("--rescene", action="store_true",
                   help="label scene layouts again where the model chose them; re-render what changes")
    p.add_argument("--check", action="store_true",
                   help="print each Short's scene layouts (labels frames again), render nothing")
    p.add_argument("--save-example", metavar="LAYOUT",
                   help="save the stream's frame at --at as LAYOUT's reference screenshot (scene matching)")
    p.add_argument("--at", type=float, help="with --save-example: seconds into the stream")
    p.set_defaults(fn=cmd_shorts)

    p = sub.add_parser("review", help="Kitsu review: publish, sync, and per-item decisions")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_review)
    r = rsub.add_parser("publish", help="upload previews to Kitsu (and post to Discord)")
    r.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    r.add_argument("--force", action="store_true", help="publish new revisions even if unchanged")
    r.add_argument("--no-notify", action="store_true", help="don't post to Discord")
    r = rsub.add_parser("sync", help="read Kitsu decisions and columns into review.json")
    r.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    r = rsub.add_parser("notify", help="post a stream's review cards to Discord")
    r.add_argument("stream", help="stream id under paths.streams, or a stream dir")
    r.add_argument("--keys", help="only these items, e.g. c01,s02")
    r.add_argument("--force", action="store_true", help="repost items already posted")
    r = rsub.add_parser("status", help="decision counts per stream, and what's scheduled")
    r.add_argument("--sync", action="store_true", help="read Kitsu first")
    r = rsub.add_parser("list", help="items waiting for a decision, with Kitsu links and previews")
    r.add_argument("stream", nargs="?", help="only this stream")
    r.add_argument("--all", action="store_true", help="every item, decided or not")
    r.add_argument("--no-sync", action="store_true", help="use review.json as is")
    item_help = "item: kick-131151926/c03_t01, or c03 / s02 when only one stream has it"
    for name, help_, text_help in [
            ("approve", "approve an item", "optional comment"),
            ("reject", "reject an item", "the reason (required; feeds scoring examples)"),
            ("changes", "ask for changes (Kitsu retake)", "what to change"),
            ("reopen", "back to waiting for approval", None),
            ("hold", "keep an item out of the schedule", None),
            ("pin", "pin a release time (Kitsu Publish at)", "YYYY-MM-DD HH:MM or YYYY-MM-DD"),
            ("retitle", "change the YouTube title", "the new title"),
            ("thumbtext", "change the words on the YouTube thumbnail", "2-4 words"),
            ("description", "change the YouTube description (without its hashtag line)",
             "the new description (or --file)"),
            ("hashtags", "change the hashtags closing the description", "#Kino #Casino (# optional)"),
            ("tags", "change the YouTube tags", "comma-separated tags (about 500 chars in all)"),
            ("playlist", "change the YouTube playlist", "playlist name")]:
        r = rsub.add_parser(name, help=help_)
        r.add_argument("item", help=item_help)
        if text_help:
            r.add_argument("text", nargs="*", help=text_help)
    rsub.choices["hold"].add_argument("--off", action="store_true", help="release the hold")
    rsub.choices["pin"].add_argument("--clear", action="store_true", help="unpin")
    for name in ("description", "hashtags", "tags", "playlist"):
        rsub.choices[name].add_argument("--file", help="read the text from this file (- for stdin)")

    p = sub.add_parser("retake", help="act on retake notes: re-cut / re-thumbnail, republish for review")
    p.add_argument("stream", nargs="?", help="only this stream (default: every stream)")
    p.add_argument("--item", help="only this item, e.g. c03 or kick-131151926/s02")
    p.add_argument("--comment", help="with --item: use this note instead of the one in Kitsu")
    p.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    p.add_argument("--no-sync", action="store_true", help="use review.json as is")
    p.set_defaults(fn=cmd_retake)

    p = sub.add_parser("schedule", help="approved items -> release slots -> schedule.json")
    p.add_argument("--no-sync", action="store_true", help="use review.json as is, skip Kitsu sync")
    p.add_argument("--no-kitsu", action="store_true", help="don't write Scheduled back to Kitsu")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--reshuffle", action="store_true",
                   help="re-place automatic entries not yet uploaded (pinned/uploaded stay)")
    p.add_argument("--show", action="store_true", help="list upcoming schedule.json entries, no replan")
    p.add_argument("--days", type=int, default=7, help="with --show: how far ahead (default 7)")
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("upload", help="upload due schedule.json entries to YouTube (private + publishAt)")
    p.add_argument("--sign-in", action="store_true", help="one-time YouTube sign-in in your browser")
    p.add_argument("--dry-run", action="store_true", help="show what would upload, upload nothing")
    p.add_argument("--max", type=int, help="upload at most this many this run")
    p.add_argument("--thumbnails", action="store_true",
                   help="only set thumbnails on uploaded videos that lack one (after channel verification)")
    p.add_argument("--next", type=int, metavar="N",
                   help="list the next N videos to upload, with their files (for uploading in Studio)")
    p.add_argument("--attach", nargs=2, metavar=("ITEM", "LINK"),
                   help="a video you uploaded in Studio is ITEM (e.g. kick-132380636/s12): set its "
                        "details, thumbnail, captions, playlist, Kitsu link; record it as uploaded")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("rumble", help="ready-to-upload Rumble folders for scheduled items; mark them done")
    p.add_argument("stream", nargs="?", help="stream id (default: every scheduled item)")
    p.add_argument("--items", help="only these, e.g. s02,c03 or a full schedule key")
    p.add_argument("--open", action="store_true", help="open the folder and Rumble's upload page")
    p.add_argument("--dry-run", action="store_true", help="show what would be prepared, write nothing")
    p.add_argument("--status", action="store_true", help="Rumble status of scheduled items")
    p.add_argument("--done", metavar="ITEM", help="mark ITEM uploaded to Rumble (with --url)")
    p.add_argument("--url", help="with --done: the video's Rumble link")
    p.add_argument("--undo", metavar="ITEM", help="clear ITEM's uploaded mark")
    p.set_defaults(fn=cmd_rumble)

    p = sub.add_parser("run", help="run every pipeline stage not done yet: transcribe ... review publish")
    p.add_argument("stream")
    p.add_argument("--only", help="just these stages, e.g. transcribe,score")
    p.add_argument("--from", dest="from_", metavar="STAGE", help="rerun from this stage on")
    p.add_argument("--force", action="store_true", help="rerun stages even if done")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("worker", help="GPU machine: `clipper run` each finished recording")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--status", action="store_true", help="run state of each recording")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("bot", help="Discord review bot: buttons on review cards, slash commands")
    p.set_defaults(fn=cmd_bot)

    p = sub.add_parser("analytics", help="YouTube stats of uploads -> analytics.json (feeds scoring)")
    p.add_argument("--sign-in", action="store_true", help="once: grant read-only YouTube Analytics")
    p.add_argument("--show", action="store_true", help="print analytics.json, best first")
    p.set_defaults(fn=cmd_analytics)

    p = sub.add_parser("warroom", help="local dashboard: live shows, pipeline, review queue, schedule")
    p.add_argument("--port", type=int, help="default [warroom] port (8765)")
    p.add_argument("--open", action="store_true", help="open it in the browser")
    p.set_defaults(fn=cmd_warroom)

    p = sub.add_parser("doctor", help="check this machine: ffmpeg, GPU, fonts, keys, Kitsu/Discord/NAS")
    p.add_argument("--offline", action="store_true", help="skip the network checks")
    p.add_argument("--role", choices=["all", "gpu", "bot", "nas"], default="all",
                   help="only what this machine runs (gpu worker, bot, nas recorder)")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("shows", help="list the [shows] profiles and which streams belong to each")
    p.set_defaults(fn=cmd_shows)

    args = ap.parse_args()
    cfg = config.load(args.config)
    if args.show:
        cfg = config.for_show(cfg, args.show)
    elif getattr(args, "stream", None):
        cfg = config.for_stream(cfg, stream_dir(cfg, args.stream))
    args.fn(cfg, args)


if __name__ == "__main__":
    main()

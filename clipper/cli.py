"""clipper command line: one subcommand per pipeline stage."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import config, jsonstate
from .config import stream_dir
from .errors import ClipperError

STREAM_HELP = "stream id under paths.streams, or a stream dir"


class Items(argparse.Action):
    """--items s02,c03 (or repeated, or space-separated where nargs allows): one list."""

    def __call__(self, parser, ns, values, option_string=None):
        got = getattr(ns, self.dest) or []
        values = values if isinstance(values, list) else [values]
        setattr(ns, self.dest, got + [x.strip() for v in values for x in v.split(",") if x.strip()])


def add_stream(p, optional: bool = False, help: str = STREAM_HELP) -> None:
    p.add_argument("stream", nargs="?" if optional else None, help=help)


def add_items(p, help: str, old: tuple[str, ...] = (), many: bool = False) -> None:
    """The one flag that picks items. Spellings it replaced (--ids, --keys, ...) still work,
    unlisted: scripts, notes and an older NAS image may still pass them."""
    nargs = "+" if many else None
    p.add_argument("--items", action=Items, nargs=nargs, metavar="ITEMS", help=help)
    for flag in old:
        p.add_argument(flag, dest="items", action=Items, nargs=nargs, help=argparse.SUPPRESS)


def time_arg(s: str) -> float:
    """Every time flag reads the same way: H:MM:SS, H:MM, 4h13m or seconds."""
    from .timefmt import parse_time

    try:
        return parse_time(s)
    except (ClipperError, ValueError) as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def refuse(args: argparse.Namespace, mode: str, *others: str) -> None:
    """Refuse `others` next to the mode flag `mode`, for combinations argparse's exclusive
    groups can't express (--photo with --teaser-photo is fine, neither with --shorts)."""
    given = [o for o in others if getattr(args, o.lstrip("-").replace("-", "_")) not in (None, False)]
    if given:
        raise ClipperError(f"{mode} doesn't combine with {', '.join(given)}")


def cmd_fetch(cfg: dict, args: argparse.Namespace) -> None:
    from . import kick

    meta = kick.resolve(args.url, cfg)
    d = cfg["paths"]["streams"] / meta["stream_id"]
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write(d / "meta.json", meta)
    print(f"{meta['stream_id']}: {meta['title']} ({meta['duration_s'] / 3600:.2f} h)")
    if not args.no_chat:
        kick.fetch_chat(d, meta, cfg)
    if not args.no_video:
        kick.fetch_video(d, meta, cfg)


def cmd_record(cfg: dict, args: argparse.Namespace) -> None:
    from . import record

    if args.action == "status":
        from . import services

        return services.record_status(cfg)
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
        raise ClipperError("record: no channels; set [record] channels in clipper.toml")
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
            raise ClipperError(f"preshow fetch: say which show with --show ({', '.join(cfg['shows'])})")
        window = None
        if args.start or args.end or args.about:
            from .timefmt import parse_time

            window = {"start_s": parse_time(args.start) if args.start else 0.0,
                      "end_s": parse_time(args.end) if args.end else None, "about": args.about}
        d = preshow.fetch(cfg, args.url, key, window)
        print(f"{d.name}: grabbed; the worker runs it, or: clipper run {d.name}")


def cmd_narrate(cfg: dict, args: argparse.Namespace) -> None:
    from . import narrate

    nc = narrate.settings(cfg)
    if args.action == "voice":
        narrate.voice(Path(args.take), nc, args.voice_id or "", Path(args.out) if args.out else None)
    elif args.action == "speak":
        src = Path(args.script)
        ext = narrate.ext(nc["output_format"])
        overrides = {k: v for k, v in (("stability", args.stability), ("style", args.style),
                                       ("speed", args.speed)) if v is not None}
        narrate.speak(src.read_text(encoding="utf-8").strip(), nc,
                      Path(args.out) if args.out else src.with_suffix(f".read.{ext}"), args.voice_id or "",
                      overrides)
    elif args.add:
        narrate.add_voice(nc, args.add, args.name)
    else:
        narrate.voices(nc, args.search or "", args.accent or "", args.gender or "", args.age or "")


def cmd_perform(cfg: dict, args: argparse.Namespace) -> None:
    from . import perform

    pc = dict(cfg["perform"], sync_key_file=cfg["paths"]["sync_key"])
    if args.action == "lipsync":
        if args.deploy:
            return perform.deploy_lipsync()
        if not args.target:
            raise ClipperError("clipper perform lipsync <dir or video> [--engine ...], or --deploy")
        chosen = args.engine or pc["lipsync_engine"]
        for engine in (["latentsync", "sync"] if chosen == "both" else [chosen]):
            perform.lipsync(Path(args.target), pc, engine, Path(args.audio) if args.audio else None)
    elif args.action == "prepare":
        perform.prepare(Path(args.recording), Path(args.read), pc, args.take, Path(args.out) if args.out else None)
    elif args.action == "status":
        perform.status(Path(args.dir))
    else:
        perform.stitch(Path(args.dir), pc)


def cmd_archive(cfg: dict, args: argparse.Namespace) -> None:
    from . import archive

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.action == "add":
        archive.add(cfg, args.subject, args.url)
    elif args.action == "transcribe":
        ac = cfg["archive"]
        ac["diarize"] = ac["diarize"] or args.diarize
        archive.run_transcribe(cfg, args.subject, args.limit, args.oldest, args.gpu or ac["gpu"],
                               args.parallel or ac["parallel"], args.dry_run)
    elif args.action == "check":
        archive.check_links(cfg, args.subject, args.items, args.all, args.limit)
    elif args.action == "links":
        archive.list_links(cfg, args.subject, args.status)
    elif args.action == "index":
        from . import archive_index

        archive_index.build(cfg, args.subject, args.rebuild)
    elif args.action == "search":
        from . import archive_index

        archive_index.search(cfg, args.subject, args.query, args.limit, args.since, args.before,
                             args.by, args.years)
    elif args.action == "ask":
        from . import archive_index

        archive_index.ask(cfg, args.subject, args.question, args.since, args.before, args.limit)
    else:
        archive.status(cfg, args.subject)


def cmd_modal(cfg: dict, args: argparse.Namespace) -> None:
    from . import modalmon

    if args.backfill:
        modalmon.backfill(cfg)
    else:
        modalmon.print_status(cfg)


def cmd_cast(cfg: dict, args: argparse.Namespace) -> None:
    from . import cast

    a = args.action
    if a == "list":
        return cast.show_list(cfg)
    if a in ("keep", "drop"):
        return cast.set_status(cfg, args.person, args.ids, a)
    if a == "label":
        return cast.set_label(cfg, args.person, args.id, args.label)
    if a == "set":
        return cast.set_info(cfg, args.person, args.alias, args.channel)
    if a == "grab":  # the stream's show may set its own [cast] (label_model, target)
        sdir = stream_dir(cfg, args.stream)
        cfg = config.for_stream(cfg, sdir)
    claude = None
    if cfg["cast"]["label_model"] and not args.no_label:
        from .llm import Claude

        claude = Claude(cfg)
    if a == "add":
        cast.add_file(cfg, args.person, args.src, claude)
    elif a == "grab":
        print(f"{cast.grab(cfg, sdir, args.person, args.n, claude, needed=args.needed)} new photo(s)")
    elif a == "auto":
        print(f"{cast.auto(cfg, claude)} new photo(s)")
    elif a == "find":
        print(f"{cast.find(cfg, args.person, args.source or list(cast.SOURCES), claude)} new photo(s)")
    if claude and claude.cost:
        print(f"labels: ${claude.cost:.3f}")
    return None


def cmd_news(cfg: dict, args: argparse.Namespace) -> None:
    from . import news

    a = args.news_cmd
    if a == "check":
        ids = news.check(cfg, judge=bool(cfg["news"]["judge_model"]) and not args.no_judge)
        print(f"{len(ids)} story(ies) to post" + (f": {', '.join(map(str, ids))}" if ids else ""))
    elif a == "list":
        news.show_list(cfg, args.days, args.person or "", args.all)
    elif a == "search":
        for x in news.search(cfg, args.query, args.days):
            print(f"{(x['published'] or '')[:16]} {x['source']}: {x['title']}\n    {x['url']}")
    elif a == "feeds":
        for f in news.feeds(cfg):
            print(f"{f['name']}: {f['items']} item(s), fetched {f['fetched_at'] or 'never'}"
                  + (f", {f['error']}" if f["error"] else ""))
    elif a == "watch":
        if not news.watchlist(cfg):
            print("nothing to watch: add terms to [news] watch (or turn on [news] auto_watch)")
        for w in sorted(news.watchlist(cfg).values(), key=lambda w: w.name.lower()):
            print(f"{w.name}: {', '.join(w.terms)}" + (f" [{', '.join(w.shows)}]" if w.shows else "")
                  + (" (Google News)" if w.search and cfg["news"]["google_news"] else ""))
    elif a == "dismiss":
        news.dismiss(cfg, args.ids)
        print(f"dismissed {', '.join(map(str, args.ids))}")
    elif a == "context":
        e = news.set_context(cfg, args.ids, args.context_show or "", who="cli")
        print(f"for {e['show']}'s next stream: {e['text']}")


def cmd_costs(cfg: dict, args: argparse.Namespace) -> None:
    from . import costs

    if args.backfill:
        costs.backfill(cfg, args.dry_run)
        return
    rep = costs.report(cfg, args.month or "")
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        costs.print_report(rep)


def cmd_transcribe(cfg: dict, args: argparse.Namespace) -> None:
    from . import transcribe

    if args.deploy:
        return transcribe.deploy()
    if not args.stream:
        raise ClipperError("clipper transcribe <stream>, or --deploy")
    sdir = stream_dir(cfg, args.stream)
    scfg = config.for_stream(cfg, sdir)
    if (sdir / "words.jsonl").exists() and not args.force:  # #75: a GPU or Modal job already paid for
        raise ClipperError(f"{sdir.name} is transcribed already (words.jsonl); --force to do it again")
    transcribe.run(sdir, scfg["transcribe"], cfg["paths"]["modal_runs"])
    if (sdir / "diarization.json").exists():
        from . import speakers
        speakers.apply(sdir, scfg)


def cmd_speakers(cfg: dict, args: argparse.Namespace) -> None:
    from . import speakers, transcribe

    sdir = stream_dir(cfg, args.stream)
    scfg = config.for_stream(cfg, sdir)
    if args.diarize:
        transcribe.diarize(sdir, scfg["transcribe"], cfg["paths"]["modal_runs"])
    elif not (sdir / "diarization.json").exists():
        raise ClipperError(f"no diarization.json in {sdir}: clipper speakers {args.stream} --diarize")
    if args.name:
        names = dict(n.split("=", 1) for n in args.name if "=" in n)
        if len(names) != len(args.name):
            raise ClipperError("--name takes SPEAKER_xx=Name")
        speakers.apply(sdir, scfg)  # speakers.json must exist before naming
        speakers.set_names(sdir, names)
    found = speakers.apply(sdir, scfg)
    if args.enroll:
        speakers.enroll(sdir, scfg, [k for k, v in names.items() if v] if args.name else
                        [k for k, v in found.items() if v.get("by") == "hand"])
    speakers.show(sdir, scfg, found)


def cmd_faces(cfg: dict, args: argparse.Namespace) -> None:
    from . import faces

    if not args.stream:
        if args.forget:
            print(f"forgot {args.forget} ({faces.forget(cfg, args.forget)} sample(s))")
        if args.enroll_photos:
            return faces.enroll_photos(cfg)
        if not args.forget:
            raise ClipperError("clipper faces <stream>, --enroll-photos or --forget Name")
        return None
    sdir = stream_dir(cfg, args.stream)
    scfg = config.for_stream(cfg, sdir)  # its show's [thumbnail] photos and [paths] faces
    if args.forget:
        print(f"forgot {args.forget} ({faces.forget(scfg, args.forget)} sample(s))")
    if args.enroll_photos:
        faces.enroll_photos(scfg)
    if args.box or args.at or args.person:
        return _face_box(sdir, scfg, args)
    if args.rescan or not (sdir / "faces.json").exists():
        faces.scan(sdir, scfg)
    names = {}
    if args.name:
        names = dict(n.split("=", 1) for n in args.name if "=" in n)
        if len(names) != len(args.name):
            raise ClipperError("--name takes F03=Name")
        faces.set_names(sdir, names)
    found = faces.apply(sdir, scfg)
    if args.enroll:
        faces.enroll(sdir, scfg, [k for k, v in names.items() if v] if args.name else
                     [k for k, v in found.items() if v.get("by") == "hand"])
        found = faces.apply(sdir, scfg)  # the new samples may name other groups
    faces.show(sdir, scfg, found)


def _face_box(sdir: Path, cfg: dict, args: argparse.Namespace) -> None:
    """clipper faces <stream> --at T --box x,y,w,h [--person Name]."""
    import tempfile

    from . import faces
    from .media import source_file
    from .timefmt import parse_time

    if not (args.box and args.at):
        raise ClipperError("a face by hand takes --at <stream time> and --box x,y,w,h")
    try:
        box = [float(v) for v in args.box.split(",")]
    except ValueError:
        box = []
    if len(box) != 4 or not all(0 <= v <= 1 for v in box) or box[2] <= 0 or box[3] <= 0:
        raise ClipperError("--box takes x,y,width,height as fractions of the frame (0-1)")
    with tempfile.TemporaryDirectory(prefix="clipper-face-") as tmp:
        try:
            t = parse_time(args.at)
        except ValueError as e:
            raise ClipperError(str(e)) from None
        frame = faces.grab(source_file(sdir), t, Path(tmp) / "frame.jpg")
        found = faces.pick(frame, box, cfg)
    who = f"looks like {found['closest']} ({found['match']:.2f})" if found["closest"] else "nobody known yet"
    print(f"face at {found['box']}, {found['px']} px: {who}")
    if args.person:
        n = faces.enroll_pick(sdir, cfg, args.person, found["embedding"])
        print(f"enrolled {args.person} ({n} sample(s)); {sdir.name}'s groups named again")


def cmd_signals(cfg: dict, args: argparse.Namespace) -> None:
    from . import signals

    sdir = stream_dir(cfg, args.stream)
    signals.run(sdir, config.for_stream(cfg, sdir)["signals"])


def cmd_score(cfg: dict, args: argparse.Namespace) -> None:
    from . import scoring

    scoring.run(stream_dir(cfg, args.stream), cfg, dry_run=args.dry_run, force=args.force)


def cmd_segments(cfg: dict, args: argparse.Namespace) -> None:
    from . import segments

    segments.run(stream_dir(cfg, args.stream), cfg, force=args.force)


def cmd_cut(cfg: dict, args: argparse.Namespace) -> None:
    from . import cut

    d = stream_dir(cfg, args.stream)
    what = args.what or ["segments", "compilations", "picks"]
    bad = set(what) - {"segments", "compilations", "picks"}
    if bad:
        raise ClipperError(f"cut: unknown {sorted(bad)}; use segments, compilations, picks")
    if config.for_stream(cfg, d)["cut"]["backend"] == "nas" and {"segments", "compilations"} & set(what):
        # the copies run beside the source; picks re-encode a few minutes each, so stay here
        # --ids, not --items: the NAS image may be older than this checkout (both spellings work)
        cut.on_nas(d, cfg, [w for w in what if w != "picks"] + (["--ids", ",".join(args.items)] if args.items else []))
        what = [w for w in what if w == "picks"]
    if "segments" in what:
        cut.cut_segments(d, cfg, args.items)
    if "compilations" in what:
        cut.cut_compilations(d, cfg)
    if "picks" in what:
        cut.cut_picks(d, cfg, args.items, force=args.force)


def cmd_meta(cfg: dict, args: argparse.Namespace) -> None:
    from . import meta

    d = stream_dir(cfg, args.stream)
    meta.captions_only(d, cfg) if args.captions_only else meta.run(d, cfg, missing=args.missing)


def cmd_thumbs(cfg: dict, args: argparse.Namespace) -> None:
    from . import thumbs

    if args.photo is not None or args.teaser_photo is not None:
        refuse(args, "--photo/--teaser-photo", "--shorts", "--compose-only", "--missing", "--force")
        if not args.items:
            raise ClipperError("--photo/--teaser-photo need --items")
        changed = thumbs.set_photos(stream_dir(cfg, args.stream), cfg, args.items,
                                    photo=args.photo, teaser_photo=args.teaser_photo,
                                    kitsu=not args.no_kitsu)
        print(f"{len(changed)} changed: {', '.join(changed)}" if changed else "nothing to change")
        return
    if args.shorts:
        refuse(args, "--shorts", "--items", "--compose-only", "--missing", "--force")
        n = thumbs.shorts_only(stream_dir(cfg, args.stream), cfg, kitsu=not args.no_kitsu)
        print(f"{n} Short covers")
        return
    thumbs.run(stream_dir(cfg, args.stream), cfg, ids=args.items,
               force=args.force, compose_only=args.compose_only, kitsu=not args.no_kitsu,
               missing=args.missing)


def cmd_pick(cfg: dict, args: argparse.Namespace) -> None:
    from . import pick

    d = stream_dir(cfg, args.stream)
    if args.find:
        pick.find(d, cfg, args.find, args.clock)
        return
    pick.run(d, cfg, about=args.about, start=args.start, end=args.end, at=args.at,
             clock_use=args.clock, dry_run=args.dry_run, make=not args.no_run,
             notify=not args.no_notify, allow_overlap=args.allow_overlap)


def cmd_shorts(cfg: dict, args: argparse.Namespace) -> None:
    from . import shorts

    if args.save_example:
        if args.at is None:
            raise ClipperError("--save-example needs --at <time into the stream>")
        shorts.save_example(stream_dir(cfg, args.stream), cfg, args.save_example, args.at)
        return
    shorts.run(stream_dir(cfg, args.stream), cfg, ids=args.items,
               layout=args.layout, force=args.force, rescene=args.rescene, check=args.check)


def cmd_retake(cfg: dict, args: argparse.Namespace) -> None:
    from . import retake

    items = args.items or [None]
    if args.comment and len(items) != 1:
        raise ClipperError("--comment needs exactly one item in --items")
    for n, item in enumerate(items):  # one Kitsu sync is enough
        retake.run(cfg, args.stream, item=item, comment=args.comment, dry_run=args.dry_run,
                   sync=not args.no_sync and n == 0)


def cmd_review(cfg: dict, args: argparse.Namespace) -> None:
    from . import actions, review

    if args.action in ("publish", "sync", "notify"):
        d = stream_dir(cfg, args.stream)
        if args.action == "publish":
            review.publish(d, cfg, force=args.force, notify=not args.no_notify, only=args.only)
        elif args.action == "sync":
            review.sync(d, cfg)
        else:
            from . import notify
            notify.post(d, cfg, keys=args.items, force=args.force)
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

    if args.list_only:
        schedule.show(cfg, args.days)
        return
    schedule.run(cfg, sync_first=not args.no_sync, write_kitsu=not args.no_kitsu,
                 dry_run=args.dry_run, reshuffle=args.reshuffle, kitsu_all=args.kitsu_all)


def cmd_upload(cfg: dict, args: argparse.Namespace) -> None:
    from . import youtube

    if args.next:
        youtube.next_up(cfg, args.next)
        return
    if args.attach:
        youtube.attach(cfg, args.attach[0], args.attach[1], dry_run=args.dry_run)
        return
    if args.related is not None:
        youtube.related(cfg, args.related)
        return
    youtube.run(cfg, sign_in=args.sign_in, dry_run=args.dry_run, limit=args.limit,
                thumbnails_only=args.thumbnails, playlists_only=args.playlists,
                links_only=args.links, captions_only=args.captions)


def cmd_rumble(cfg: dict, args: argparse.Namespace) -> None:
    from . import rumble

    if args.url and not args.done:
        raise ClipperError("--url goes with --done ITEM")
    if args.done or args.undo:
        refuse(args, "--done/--undo", "--items", "--open", "--dry-run")
        rumble.mark_done(cfg, args.done or args.undo, args.url or "", undo=bool(args.undo))
    elif args.status:
        refuse(args, "--status", "--items", "--open", "--dry-run")
        rumble.status(cfg, args.stream)
    else:
        rumble.run(cfg, args.stream, args.items,
                   open_after=args.open, dry_run=args.dry_run)


def cmd_tiktok(cfg: dict, args: argparse.Namespace) -> None:
    from . import tiktok

    if args.resend and not args.items:
        raise ClipperError("--resend goes with --items")
    if args.sign_in:
        tiktok.sign_in(cfg)
        print(f"signed in to TikTok as {tiktok.whoami(cfg)}")
    if args.items:
        tiktok.send(cfg, args.items, args.stream, dry_run=args.dry_run, resend=args.resend)
    elif args.auto:
        tiktok.auto(cfg, dry_run=args.dry_run)
    elif args.caption:
        tiktok.repost_caption(cfg, args.caption if "/" in args.caption or not args.stream
                              else f"{args.stream}/{args.caption}")
    elif not args.sign_in:
        tiktok.status(cfg, args.stream, check=not args.dry_run)


def cmd_x(cfg: dict, args: argparse.Namespace) -> None:
    from . import xpost

    if args.sign_in:
        xpost.sign_in(cfg)
        print(f"signed in to X as @{xpost.whoami(cfg)}")
    if args.items:
        xpost.post(cfg, args.items, args.stream, dry_run=args.dry_run)
    elif args.mark:
        xpost.mark(cfg, *args.mark)
    elif args.issue:
        xpost.post_issue(cfg, None if args.issue == "latest" else args.issue, dry_run=args.dry_run)
    elif args.auto:
        xpost.auto(cfg, dry_run=args.dry_run)
    elif not args.sign_in:
        xpost.status(cfg, args.stream)


def cmd_receipts(cfg: dict, args: argparse.Namespace) -> None:
    from . import receipts

    d = stream_dir(cfg, args.stream)
    if args.action == "find":
        receipts.find(d, cfg, args.items)
    elif args.action == "script":
        receipts.script(d, cfg)
    elif args.action == "cut":
        receipts.cut(d, cfg)
    elif args.action == "upload":
        receipts.upload(d, cfg, args.again)
    else:
        receipts.show(d)


def cmd_investigate(cfg: dict, args: argparse.Namespace) -> None:
    from . import costs, investigate

    costs.set_context(f"investigate {args.action}", f"investigate-{args.subject}")
    a = args.action
    if a == "facts":
        investigate.run_facts(cfg, args.subject, args.since, args.before, args.limit, args.new, args.dry_run)
    elif a == "leads":
        investigate.run_leads(cfg, args.subject, args.about, args.since, args.before, args.max, args.confirm)
    elif a == "confirm":
        investigate.run_confirm(cfg, args.subject, args.lead)
    elif a == "check" and args.trail:
        investigate.show_trail(cfg, args.subject, args.lead)
    elif a == "check":
        investigate.run_check(cfg, args.subject, args.lead)
    elif a == "show":
        investigate.show_lead(cfg, args.subject, args.lead)
    elif a == "post":
        investigate.post_discord(cfg, args.subject, args.new_thread)
    elif a == "run":
        investigate.run_all(cfg, args.subject, args.since, args.before, args.about, args.max)
    else:
        investigate.show_list(cfg, args.subject)


def cmd_story(cfg: dict, args: argparse.Namespace) -> None:
    from . import costs, story

    a = args.action
    if a in ("new", "run"):
        over = {k: v for k, v in (("max_videos", args.max_videos), ("max_hours", args.max_hours)) if v is not None}
        cfg = {**cfg, "story": {**cfg["story"], **over}}
    if a == "new":
        if args.days is not None and args.since:
            raise ClipperError("--days or --since, not both")
        slug = story.new(cfg, args.brief, args.slug, args.days, args.since, args.before, args.person, args.url)
        costs.set_context("story new", f"story-{slug}")
        story.run(cfg, slug, dry_run=args.dry_run)
        return
    if a == "status":
        story.status(cfg, args.slug)
        return
    costs.set_context(f"story {a}", f"story-{args.slug}")
    if a == "run":
        story.run(cfg, args.slug, args.start, args.dry_run)
    elif a == "add":
        story.add(cfg, args.slug, args.urls)
    elif a == "trail":
        story.trail(cfg, args.slug)
    elif a == "plan":
        from . import story_edit

        story_edit.plan(cfg, args.slug, args.force)
    elif a == "cut":
        from . import story_edit

        story_edit.cut(cfg, args.slug, args.force, review=not args.no_review)
    else:
        story.show(cfg, args.slug)


def cmd_weekly(cfg: dict, args: argparse.Namespace) -> None:
    from . import weekly

    if args.discord:
        from . import notify

        if any([args.headline, args.gather, args.draft, args.render, args.only, args.write, args.format]):
            raise ClipperError("--discord posts the pages already rendered; build the issue first, "
                               "then run --discord on its own (with --week for an older issue)")

        notify.post_issue(cfg, args.week)
        return
    weekly.run(cfg, day=args.week, headline=args.headline or "", gather_only=args.gather,
               redraft=args.draft, render_only=args.render, only=args.only or "", write=args.write or "",
               fmt=args.format or "")


def config_path(args: argparse.Namespace) -> str:
    return str(Path(args.config or os.environ.get("CLIPPER_CONFIG") or "clipper.toml").resolve())


def cmd_run(cfg: dict, args: argparse.Namespace) -> None:
    from . import stages as stage_table
    from . import worker

    worker.setup_logging()
    stages = None
    if args.only or args.from_:
        if args.from_ and args.from_ not in worker.STAGES:  # #77 L14
            raise ClipperError(f"run: unknown stage {args.from_!r}; stages: {', '.join(worker.STAGES)}")
        if args.only and args.from_:
            raise ClipperError("run: --only or --from, not both")
        stages = args.only.split(",") if args.only else stage_table.from_stage(args.from_, cfg["worker"])
        bad = set(stages) - set(worker.STAGES)
        if bad:
            raise ClipperError(f"run: unknown stage {sorted(bad)}; stages: {', '.join(worker.STAGES)}")
    try:
        ok = worker.run_stream(cfg, stream_dir(cfg, args.stream), config_path(args), stages,
                               force=args.force or bool(args.from_))
    except worker.Busy as e:
        raise ClipperError(str(e))
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

    if args.report:
        if args.refresh:
            analytics.run(cfg)
        rep = analytics.report(cfg, args.month or "")
        print(analytics.report_markdown(rep))
        print(f"-> {analytics.write_report(cfg, rep)}")
    else:
        analytics.show(cfg) if args.list_only else analytics.run(cfg, sign_in=args.sign_in)


def cmd_warroom(cfg: dict, args: argparse.Namespace) -> None:
    from . import warroom

    if args.tailnet:
        warroom.tailnet(cfg, args.tailnet)
        return
    warroom.run(cfg, port=args.port, open_browser=args.open)


def cmd_alert(cfg: dict, args: argparse.Namespace) -> None:
    from . import alerts

    if args.what == "test":
        alerts.send(cfg, "test", "Test from `clipper alert test`: clipper's DMs reach you.")
        print(f"queued in {alerts.outbox(cfg)}; the bot DMs it within a minute "
              f"(not during quiet hours {cfg['notify']['quiet_hours'] or 'none'})")
    else:
        text = alerts.digest(cfg)
        print(text)
        if args.send:
            alerts.send(cfg, "digest", text, key=f"digest-now:{time.time():.0f}")
            print("\nqueued; the bot DMs it within a minute")


def cmd_config(cfg: dict, args: argparse.Namespace) -> None:
    sdir = stream_dir(cfg, args.for_stream) if args.for_stream else None
    e = config.explain(cfg, args.name, sdir)
    show = f"show {e['show']}" if e["show"] else "base config, no show"
    where = f"stream {sdir.name}, {show}" if sdir else show
    print(f"{e['name']} = {json.dumps(e['value'], default=str)}    ({where})")
    for i, (layer, value) in enumerate(e["layers"]):
        last = i == len(e["layers"]) - 1
        print(f"  {'->' if last else '  '} {layer:<28} {json.dumps(value, default=str)}")


def cmd_doctor(cfg: dict, args: argparse.Namespace) -> None:
    from . import doctor

    raise SystemExit(doctor.main(cfg, offline=args.offline, role=args.role))


def cmd_setup(cfg: dict, args: argparse.Namespace) -> None:
    from . import setup

    raise SystemExit(setup.main(args.config, step=args.step, answers=args.answers, check_only=args.check,
                                print_sync=args.sync_args, offline=args.offline))


def cmd_services(cfg: dict, args: argparse.Namespace) -> None:
    from . import services

    if args.action == "status" and args.force:
        raise ClipperError("--force goes with stop/restart recorder")
    if args.action != "status" and not args.which:
        raise ClipperError(f"services {args.action}: say which: bot, worker, all (bot and worker) or recorder")
    status = services.run(cfg, args.action, args.which or "all", force=args.force)
    if status:
        raise ClipperError("", status=status)


def cmd_serve(cfg: dict, args: argparse.Namespace) -> None:
    from . import serve

    serve.run(cfg, port=args.port)


def cmd_bot(cfg: dict, args: argparse.Namespace) -> None:
    from . import bot

    bot.run(cfg)


COMMAND_GROUPS = [
    ('Pipeline (in run order)', ['fetch', 'preshow', 'transcribe', 'speakers', 'faces', 'signals', 'score', 'segments', 'cut', 'meta', 'thumbs', 'shorts', 'pick', 'run']),
    ('Review and publish', ['review', 'retake', 'schedule', 'upload', 'rumble', 'tiktok', 'x', 'analytics']),
    ('Services (long-running)', ['services', 'record', 'worker', 'serve', 'bot', 'warroom', 'alert']),
    ('Extras', ['weekly', 'cast', 'news', 'receipts', 'archive', 'investigate', 'story', 'narrate', 'perform', 'modal',
                'costs']),
    ('Setup and checks', ['setup', 'doctor', 'config', 'shows']),
]


def command_list() -> str:
    """`clipper -h`'s list of commands by group (argparse prints one flat list)."""
    return "\n\n".join(f"{title}:\n  " + " ".join(names) for title, names in COMMAND_GROUPS)


def add_pipeline(sub) -> None:
    """Pipeline: a stream from recording to review, in run order."""
    p = sub.add_parser("fetch", help="resolve a Kick VOD url, pull chat and video")
    p.add_argument("url")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-chat", action="store_true")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("preshow", help="grab a show's pre-show from its YouTube channel, as one whole video")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_preshow)
    r = rsub.add_parser("check", help="grab new uploads matching [preshow] title_pattern (the bot does this)")
    r.add_argument("--run", action="store_true", help="then run the pipeline on grabbed pre-shows not done yet")
    rsub.add_parser("list", help="recent pre-shows in the feeds, and which are grabbed")
    r = rsub.add_parser("fetch", help="grab one video by url, any age; --from/--to/--about: a one-off, "
                                      "one long segment out of that stretch")
    r.add_argument("url")
    r.add_argument("--from", dest="start", metavar="TIME", help="one-off: the stretch starts about here (1:30:00)")
    r.add_argument("--to", dest="end", metavar="TIME", help="one-off: ...and ends about here (default: the end)")
    r.add_argument("--about", default="", help="one-off: what the stretch is (the model finds the exact bounds)")

    p = sub.add_parser("transcribe", help="WhisperX -> words.jsonl")
    add_stream(p, optional=True)
    p.add_argument("--deploy", action="store_true",
                   help="deploy the Modal app for [transcribe] backend = \"modal\"")
    p.add_argument("--force", action="store_true", help="transcribe again over an existing words.jsonl")
    p.set_defaults(fn=cmd_transcribe)

    p = sub.add_parser("speakers", help="name the diarized speakers (enrolled voices), fill words.jsonl speaker")
    add_stream(p)
    p.add_argument("--diarize", action="store_true",
                   help="diarize first (a stream transcribed without [transcribe] diarize; one GPU call)")
    p.add_argument("--name", action="append", metavar="SPEAKER_xx=Name",
                   help="name a speaker by hand (repeatable; SPEAKER_xx= clears)")
    p.add_argument("--enroll", action="store_true",
                   help="keep the named voices (these --name ones, else every hand-named one) for the show")
    p.set_defaults(fn=cmd_speakers)

    p = sub.add_parser("faces", help="who is on screen: group the keyframes' faces, name them -> faces.json")
    add_stream(p, optional=True)
    p.add_argument("--rescan", action="store_true", help="scan the keyframes again (the first run always scans)")
    p.add_argument("--name", action="append", metavar="F03=Name",
                   help="name a group by hand (repeatable; F03= clears); see faces/F03.jpg")
    p.add_argument("--enroll", action="store_true",
                   help="keep the named faces (these --name ones, else every hand-named one) for every show")
    p.add_argument("--enroll-photos", action="store_true", help="add the [thumbnail] photos portraits to the known faces")
    p.add_argument("--at", metavar="H:MM:SS", help="with --box: the stream time of the frame")
    p.add_argument("--box", metavar="X,Y,W,H", help="a face by hand: its box in the source frame, 0-1")
    p.add_argument("--person", metavar="NAME", help="with --box: keep that face as NAME's")
    p.add_argument("--forget", metavar="NAME", help="drop NAME from the known faces")
    p.set_defaults(fn=cmd_faces)

    p = sub.add_parser("signals", help="chat + loudness heat curve -> windows.json")
    add_stream(p)
    p.set_defaults(fn=cmd_signals)

    p = sub.add_parser("score", help="Claude Pass A + Pass B -> candidates.json")
    add_stream(p)
    p.add_argument("--dry-run", action="store_true", help="write Pass A requests, no API calls")
    p.add_argument("--force", action="store_true", help="score again although candidates.json exists (pays Pass A again)")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("segments", help="split the stream into topic segments -> segments.json")
    add_stream(p)
    p.add_argument("--force", action="store_true", help="re-split instead of reusing segments.json")
    p.set_defaults(fn=cmd_segments)

    p = sub.add_parser("cut", help="cut segments, topic compilations and Pass B picks")
    add_stream(p)
    p.add_argument("what", nargs="*", help="segments, compilations and/or picks (default: all)")
    add_items(p, "only these segments or picks, e.g. s03,s07 or t03", old=("--ids",))
    p.add_argument("--force", action="store_true", help="re-cut picks whose in/out points haven't changed")
    p.set_defaults(fn=cmd_cut)

    p = sub.add_parser("meta", help="upload metadata (SEO title, description, chapters, tags) + SRT")
    add_stream(p)
    p.add_argument("--captions-only", action="store_true",
                   help="rewrite .srt and re-mask metadata text, no model calls")
    p.add_argument("--missing", action="store_true",
                   help="only videos with no draft yet; existing drafts stay as they are")
    p.set_defaults(fn=cmd_meta)

    p = sub.add_parser("thumbs", help="thumbnails: pick a frame, write the text -> <video>.jpg")
    add_stream(p)
    add_items(p, "only these videos, e.g. t20,s02,comp-the-quartering", old=("--ids",))
    p.add_argument("--force", action="store_true", help="pick again even if the video is unchanged")
    p.add_argument("--compose-only", action="store_true",
                   help="re-render from thumbnails.json (brand or hand edits), no model calls")
    p.add_argument("--no-kitsu", action="store_true", help="don't attach them in Kitsu")
    p.add_argument("--missing", action="store_true",
                   help="only videos with no thumbnail yet; leave the others' covers alone")
    p.add_argument("--shorts", action="store_true",
                   help="only the Shorts' 9:16 covers, from their clips' choices (no model calls)")
    p.add_argument("--photo", metavar="PERSON",
                   help="enquirer: use [thumbnail] photos/<PERSON> as the --items covers' face "
                        "(\"\" = back to the frame); re-renders, no model calls")
    p.add_argument("--teaser-photo", metavar="PERSON",
                   help="enquirer: show that photo beside the --items videos' teasers on the other covers")
    p.set_defaults(fn=cmd_thumbs)

    p = sub.add_parser("shorts", help="vertical Shorts of the picks: layout crop, captions, loudness -> shorts/")
    add_stream(p)
    add_items(p, "only these picks, e.g. t33,w19 or v01", old=("--ids",))
    p.add_argument("--layout", help="use this layout (from [shorts] layouts) for the chosen picks")
    p.add_argument("--force", action="store_true", help="re-render even if unchanged (keeps layouts)")
    p.add_argument("--rescene", action="store_true",
                   help="label scene layouts again where the model chose them; re-render what changes")
    p.add_argument("--check", action="store_true",
                   help="print each Short's scene layouts (labels frames again), render nothing")
    p.add_argument("--save-example", metavar="LAYOUT",
                   help="save the stream's frame at --at as LAYOUT's reference screenshot (scene matching)")
    p.add_argument("--at", type=time_arg, metavar="TIME",
                   help="with --save-example: time into the recording (H:MM:SS, H:MM or seconds)")
    p.set_defaults(fn=cmd_shorts)

    p = sub.add_parser("pick", help="Shorts from a stretch you name: find, cut, render, send to review")
    add_stream(p)
    p.add_argument("--from", dest="start", metavar="TIME", help="start, e.g. 4:05:00 or 4:05 (H:MM)")
    p.add_argument("--to", dest="end", metavar="TIME", help="end, e.g. 4:25:00")
    p.add_argument("--at", metavar="TIME", help="a [pick] span_s stretch around this time")
    p.add_argument("--about", default="", help="what to look for (without a time: located "
                                               "in the stream summary first)")
    p.add_argument("--find", metavar="TEXT", help="list transcript lines with TEXT, no model calls")
    p.add_argument("--clock", choices=["stream", "video"], default="stream",
                   help="times as Kick's VOD shows them (default) or the recording's own")
    p.add_argument("--dry-run", action="store_true", help="show the moments found, save nothing")
    p.add_argument("--no-run", action="store_true", help="only add the picks to candidates.json")
    p.add_argument("--no-notify", action="store_true", help="don't post review cards to Discord")
    p.add_argument("--allow-overlap", action="store_true",
                   help="keep moments that overlap an existing pick")
    p.set_defaults(fn=cmd_pick)

    p = sub.add_parser("run", help="run every pipeline stage not done yet: transcribe ... review publish")
    p.add_argument("stream")
    from .stages import NAMES as STAGE_NAMES
    p.add_argument("--only", help="just these stages, comma-separated: " + ", ".join(STAGE_NAMES))
    p.add_argument("--from", dest="from_", choices=STAGE_NAMES, metavar="STAGE",
                   help="rerun from this stage on: " + ", ".join(STAGE_NAMES))
    p.add_argument("--force", action="store_true", help="rerun stages even if done")
    p.set_defaults(fn=cmd_run)


def add_review(sub) -> None:
    """Review and publish."""
    p = sub.add_parser("review", help="Kitsu review: publish, sync, and per-item decisions")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_review)
    r = rsub.add_parser("publish", help="upload previews to Kitsu (and post to Discord)")
    add_stream(r)
    r.add_argument("--force", action="store_true", help="publish new revisions even if unchanged")
    r.add_argument("--no-notify", action="store_true", help="don't post to Discord")
    r.add_argument("--only", action="append", choices=["segments", "picks", "shorts", "compilations"],
                   help="publish only these kinds of [review] include (repeatable), e.g. --only shorts")
    r = rsub.add_parser("sync", help="read Kitsu decisions and columns into review.json")
    add_stream(r)
    r = rsub.add_parser("notify", help="post a stream's review cards to Discord")
    add_stream(r)
    add_items(r, "only these items, e.g. c01,s02", old=("--keys",))
    r.add_argument("--force", action="store_true", help="repost items already posted")
    r = rsub.add_parser("status", help="decision counts per stream, and what's scheduled")
    # Reads review.json unless asked: a sync writes it and can post to Kitsu, not for a status.
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
    add_items(p, "only these items, e.g. c03 or kick-131151926/s02", old=("--item",))
    p.add_argument("--comment", help="with one --items item: use this note instead of the one in Kitsu")
    p.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    p.add_argument("--no-sync", action="store_true", help="use review.json as is")
    p.set_defaults(fn=cmd_retake)

    p = sub.add_parser("schedule", help="approved items -> release slots -> schedule.json")
    p.add_argument("--no-sync", action="store_true", help="use review.json as is, skip Kitsu sync")
    p.add_argument("--no-kitsu", action="store_true", help="don't write Scheduled back to Kitsu")
    p.add_argument("--kitsu-all", action="store_true",
                   help="check every item's Scheduled in Kitsu, not only the changed slots")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--reshuffle", action="store_true",
                   help="re-place automatic entries not yet uploaded (pinned/uploaded stay)")
    p.add_argument("--list", dest="list_only", action="store_true",
                   help="list upcoming schedule.json entries, no replan")
    p.add_argument("--days", type=int, default=7, help="with --list: how far ahead (default 7)")
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("upload", help="upload due schedule.json entries to YouTube (private + publishAt)")
    p.add_argument("--dry-run", action="store_true", help="show what would upload, upload nothing")
    p.add_argument("--limit", type=int, help="upload (or with --playlists, add) at most this many this run")
    p.add_argument("--max", dest="limit", type=int, help=argparse.SUPPRESS)  # old spelling
    m = p.add_mutually_exclusive_group()  # one job per run; plain `upload` is the scheduled uploads
    m.add_argument("--sign-in", action="store_true", help="one-time YouTube sign-in in your browser")
    m.add_argument("--thumbnails", action="store_true",
                   help="only set thumbnails on uploaded videos that lack one (after channel verification)")
    m.add_argument("--playlists", action="store_true",
                   help="only add uploaded videos to playlists they're missing from, e.g. their show's "
                        "([youtube] show_playlist); 50 quota units each, --limit N to spread it out")
    m.add_argument("--links", action="store_true",
                   help="only add, fix or drop source links (clip -> segment, segment -> compilation) "
                        "on uploaded videos; every upload run does this too ([youtube] source_links)")
    m.add_argument("--captions", action="store_true",
                   help="only retry captions that failed to go up (400 quota units each, 3 tries); "
                        "every upload run does this too, from the day's spare quota")
    m.add_argument("--next", type=int, metavar="N",
                   help="list the next N videos to upload, with their files (for uploading in Studio)")
    m.add_argument("--attach", nargs=2, metavar=("ITEM", "LINK"),
                   help="a video you uploaded in Studio is ITEM (e.g. kick-132380636/s12): set its "
                        "details, thumbnail, captions, playlist, Kitsu link; record it as uploaded")
    m.add_argument("--related", nargs="*", metavar="ITEM",
                   help="list uploaded Shorts whose segment is out, to set its related video in Studio "
                        "(the API can't); with ITEMs (keys or Short links), record them as set")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("rumble", help="ready-to-upload Rumble folders for scheduled items; mark them done")
    p.add_argument("stream", nargs="?", help="stream id (default: every scheduled item)")
    add_items(p, "only these, e.g. s02,c03 or a full schedule key")
    p.add_argument("--open", action="store_true", help="open the folder and Rumble's upload page")
    p.add_argument("--dry-run", action="store_true", help="show what would be prepared, write nothing")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--status", action="store_true", help="Rumble status of scheduled items")
    m.add_argument("--done", metavar="ITEM", help="mark ITEM uploaded to Rumble (with --url)")
    m.add_argument("--undo", metavar="ITEM", help="clear ITEM's uploaded mark")
    p.add_argument("--url", help="with --done: the video's Rumble link")
    p.set_defaults(fn=cmd_rumble)

    p = sub.add_parser("tiktok", help="send scheduled Shorts to the TikTok app's drafts; post from the phone")
    p.add_argument("stream", nargs="?", help="stream id, for short --items like s02 (default: any)")
    m = p.add_mutually_exclusive_group()  # none of them: the status of sent drafts
    add_items(m, "send these as drafts, e.g. t20,v01_t20 or a full schedule key")
    m.add_argument("--auto", action="store_true",
                   help="send the next unsent Shorts in YouTube-schedule order, up to max_per_24h")
    m.add_argument("--caption", metavar="ITEM", help="post a sent draft's caption to Discord again")
    p.add_argument("--resend", action="store_true",
                   help="with --items: send again drafts that expired unposted in the app")
    p.add_argument("--sign-in", action="store_true", help="one-time TikTok sign-in in your browser (then "
                                                         "carries on with --items/--auto if given)")
    p.add_argument("--dry-run", action="store_true",
                   help="show files and captions, send nothing; with no mode, list drafts without asking TikTok")
    p.set_defaults(fn=cmd_tiktok)

    p = sub.add_parser("x", help="post scheduled Shorts to X as native video")
    p.add_argument("stream", nargs="?", help="stream id, for short --items like s02 (default: any)")
    m = p.add_mutually_exclusive_group()  # none of them: what was posted
    add_items(m, "post these, e.g. t20,v01_t20 or a full schedule key")
    m.add_argument("--auto", action="store_true",
                   help="post the Shorts live on YouTube and not on X yet, up to max_per_24h")
    m.add_argument("--issue", nargs="?", const="latest", metavar="DAY",
                   help="post a weekly issue's pages as a thread (any day of its week, YYYY-MM-DD; "
                        "default the newest issue)")
    m.add_argument("--mark", nargs=2, metavar=("KEY", "STATUS"),
                   help="after a hand check of a post that timed out (\"unknown\"): posted[:URL] or failed")
    p.add_argument("--sign-in", action="store_true", help="one-time X sign-in in your browser (then "
                                                         "carries on with --items/--auto/--issue if given)")
    p.add_argument("--dry-run", action="store_true", help="show files and text, post nothing")
    p.set_defaults(fn=cmd_x)

    p = sub.add_parser("analytics", help="YouTube stats of uploads -> analytics.json (feeds scoring)")
    p.add_argument("--sign-in", action="store_true", help="once: grant read-only YouTube Analytics")
    p.add_argument("--list", dest="list_only", action="store_true", help="print analytics.json, best first")
    p.add_argument("--report", action="store_true",
                   help="the month's report -> reports/analytics/<month>.md + .csv")
    p.add_argument("--month", help="with --report: YYYY-MM (default: this month)")
    p.add_argument("--refresh", action="store_true", help="with --report: refresh analytics.json first")
    p.set_defaults(fn=cmd_analytics)


def add_services(sub) -> None:
    """Services: long-running."""
    p = sub.add_parser("record", help="record live streams with real-time chat (NAS service)")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_record)
    r = rsub.add_parser("watch", help="watch [record] channels; record each stream they go live with")
    r.add_argument("--channel", action="append", help="only this channel (repeatable)")
    r = rsub.add_parser("now", help="record one channel's current (or next) stream, then exit")
    r.add_argument("channel", help="Kick channel slug")
    r.add_argument("--max-s", type=float, help="stop after this many seconds and finalize")
    rsub.add_parser("status", help="what the recorder is doing, from its heartbeat on the share "
                                   "(exit 3 while recording)")
    r = rsub.add_parser("finalize", help="join an interrupted recording's parts and write its files")
    add_stream(r)

    p = sub.add_parser("services", help="status, stop or restart serve, the bot, the worker and the NAS recorder")
    p.add_argument("action", nargs="?", choices=["status", "stop", "restart"], default="status")
    p.add_argument("which", nargs="?", choices=["serve", "bot", "worker", "recorder", "all"],
                   help="default for status: everything; stop/restart need one (all = worker, serve "
                        "and bot; the recorder is only ever named)")
    p.add_argument("--force", action="store_true",
                   help="restart or stop the recorder even while it's recording (starts a new part)")
    p.set_defaults(fn=cmd_services)

    p = sub.add_parser("worker", help="GPU machine: `clipper run` each finished recording")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--status", action="store_true", help="run state of each recording")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("serve", help="the always-on process: war room, hourly uploads, pre-shows, retakes, daily jobs")
    p.add_argument("--port", type=int, help="the war room's port; default [warroom] port (8765)")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("bot", help="Discord review bot: buttons on review cards, slash commands, DMs")
    p.set_defaults(fn=cmd_bot)

    p = sub.add_parser("warroom", help="the dashboard alone (clipper serve runs it with the jobs)")
    p.add_argument("--port", type=int, help="default [warroom] port (8765)")
    p.add_argument("--open", action="store_true", help="open it in the browser")
    p.add_argument("--tailnet", choices=("on", "off", "status"),
                   help="serve it on this machine's tailnet over HTTPS (tailscale serve), for the phone")
    p.set_defaults(fn=cmd_warroom)

    p = sub.add_parser("alert", help="notifications (Discord DMs from the bot): test one, see the digest")
    p.add_argument("what", choices=("test", "digest"))
    p.add_argument("--send", action="store_true", help="digest: DM it now too")
    p.set_defaults(fn=cmd_alert)


def add_extras(sub) -> None:
    """Extras: side projects and reports."""
    p = sub.add_parser("weekly", help="the week's streams -> a ten-page tabloid issue (weekly/<saturday>/)")
    p.add_argument("--week", help="any day of the wanted Sun-Sat week, YYYY-MM-DD (default: this week)")
    p.add_argument("--headline", help="editor's note for the draft: the cover story, e.g. \"Ethan Klein\"")
    p.add_argument("--gather", action="store_true", help="only rebuild week.json and stats.json")
    p.add_argument("--draft", action="store_true", help="draft issue.json again (the old one is kept as a backup)")
    p.add_argument("--render", action="store_true", help="only render the pages from issue.json")
    p.add_argument("--only", help="render only these pages, e.g. 01,07")
    p.add_argument("--format", help="tiktok (1080x1440), youtube (1080x1080 in youtube/) or both, comma-separated; "
                                    "default [weekly] formats")
    p.add_argument("--write", nargs="?", const="03,04,05", metavar="PAGES",
                   help="write the articles again with the writer prompt (03 cover story, 04, 05; default all three)")
    p.add_argument("--discord", action="store_true",
                   help="post the issue's pages to [discord] weekly_channel (made if missing), to save on a "
                        "phone; the newest issue, or --week's")
    p.set_defaults(fn=cmd_weekly)

    p = sub.add_parser("receipts", help="old clips of the archive subject for a stream's stories, "
                                        "one video uploaded private (#48)")
    p.add_argument("action", choices=["find", "list", "script", "cut", "upload"])
    add_stream(p)
    add_items(p, "find: only these segments, e.g. s02,s05 (default: by topic/figure)", old=("--segments",))
    p.add_argument("--again", action="store_true", help="upload: a second copy even if one is up")
    p.set_defaults(fn=cmd_receipts)

    p = sub.add_parser("archive", help="back catalogs (YouTube, Odysee): list, transcribe on Modal")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_archive)
    r = rsub.add_parser("add", help="list a channel's long-form videos into a subject's catalog")
    r.add_argument("subject", help="short name, e.g. jer")
    r.add_argument("url", nargs="+", help="YouTube channel/tab or Odysee channel url")
    r = rsub.add_parser("transcribe", help="transcribe catalog items not done yet, on Modal")
    r.add_argument("subject")
    r.add_argument("--limit", type=int, help="at most this many items")
    r.add_argument("--oldest", action="store_true", help="oldest first (default newest first)")
    r.add_argument("--gpu", help="Modal GPU (default [archive] gpu)")
    r.add_argument("--parallel", type=int, help="items at once (default [archive] parallel)")
    r.add_argument("--dry-run", action="store_true", help="say how many hours and the estimated cost")
    r.add_argument("--diarize", action="store_true", help="speaker turns too (default [archive] diarize)")
    r = rsub.add_parser("status", help="items, hours transcribed, errors, cost, link checks")
    r.add_argument("subject", nargs="?")
    r = rsub.add_parser("check", help="are the source links still up? (no downloads, no cost)")
    r.add_argument("subject")
    add_items(r, "just these items (default: not checked in [archive] link_recheck_days)",
              old=("--ids",), many=True)
    r.add_argument("--all", action="store_true", help="every item, however recently checked")
    r.add_argument("--limit", type=int, help="at most this many items")
    r = rsub.add_parser("links", help="items by link status (default: every link not alive)")
    r.add_argument("subject")
    r.add_argument("--status", choices=["dead", "restricted", "alive", "all"])
    r = rsub.add_parser("index", help="build the subject's keyword index (index.sqlite; no cost)")
    r.add_argument("subject")
    r.add_argument("--rebuild", action="store_true", help="start the index over")
    r = rsub.add_parser("search", help="search the transcripts: hits with date, link at the moment")
    r.add_argument("subject")
    r.add_argument("query", help='FTS5: costco dog | "costco dog" | costco OR walmart | coff*')
    r.add_argument("--limit", type=int, default=20)
    r.add_argument("--since", help="items dated on or after YYYY[-MM-DD]")
    r.add_argument("--before", help="items dated before YYYY[-MM-DD]")
    r.add_argument("--by", choices=["rank", "date", "oldest"], default="rank",
                   help="best match (default), newest first, or oldest first")
    r.add_argument("--years", action="store_true", help="matching windows per year, not hits")
    r = rsub.add_parser("ask", help="plain question: a cheap model searches the index, picks the fits")
    r.add_argument("subject")
    r.add_argument("question", help="e.g. 'Jer saying he would never sell out to a sponsor'")
    r.add_argument("--since", help="items dated on or after YYYY[-MM-DD]")
    r.add_argument("--before", help="items dated before YYYY[-MM-DD]")
    r.add_argument("--limit", type=int, default=15, help="options shown")

    p = sub.add_parser("investigate", help="an archive subject's own videos -> facts -> leads nobody has "
                                           "picked up (#108; research only, never uploaded)")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_investigate)

    def span(r) -> None:
        r.add_argument("--since", help="items dated on or after YYYY[-MM-DD]")
        r.add_argument("--before", help="items dated before YYYY[-MM-DD]")

    r = rsub.add_parser("facts", help="read items for checkable facts, quotes verified (cached per item)")
    r.add_argument("subject")
    span(r)
    r.add_argument("--limit", type=int, help="at most this many items, newest first")
    r.add_argument("--new", action="store_true", help="only items newer than the newest one read")
    r.add_argument("--dry-run", action="store_true", help="say how many items and hours and the estimated cost")
    r = rsub.add_parser("leads", help="candidate threads in code, then the leads model writes them up")
    r.add_argument("subject")
    r.add_argument("--about", help='narrow to a focus, e.g. "lawsuit" or a name')
    span(r)
    r.add_argument("--max", type=int, help="leads kept (default [investigate] max_leads)")
    r.add_argument("--confirm", action="store_true", help="then test each lead against more of the archive")
    r = rsub.add_parser("confirm", help="test the leads against more of the archive (archive ask)")
    r.add_argument("subject")
    r.add_argument("--lead", help="just this lead, e.g. L03")
    r = rsub.add_parser("check", help="is anyone else on it? YouTube, web, Reddit, our streams, his own titles")
    r.add_argument("subject")
    r.add_argument("--lead", help="just this lead, e.g. L03")
    r.add_argument("--trail", action="store_true", help="print the last check's searches and results")
    r = rsub.add_parser("list", help="the leads: kind, confidence, coverage, receipts")
    r.add_argument("subject")
    r = rsub.add_parser("show", help="one lead's story and timeline with quotes and links")
    r.add_argument("subject")
    r.add_argument("lead", help="e.g. L03")
    r = rsub.add_parser("post", help="post the kept leads to [investigate] discord_channel: a thread per "
                                     "leads run, edited in place after check or confirm")
    r.add_argument("subject")
    r.add_argument("--new-thread", action="store_true", help="a fresh thread even if this run has one")
    r = rsub.add_parser("run", help="facts (--new unless --since) -> leads -> check; prints the total cost")
    r.add_argument("subject")
    span(r)
    r.add_argument("--about", help="narrow the leads to a focus")
    r.add_argument("--max", type=int, help="leads kept")

    p = sub.add_parser("story", help="a news brief -> the swarm -> transcripts -> the moments -> a dossier; "
                                     "then plan and cut a video of it for review (#84)")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_story)

    def limits(r) -> None:
        r.add_argument("--max-videos", type=int, help="videos to transcribe (default [story] max_videos)")
        r.add_argument("--max-hours", type=float, help="hours of audio to transcribe (default [story] max_hours)")
        r.add_argument("--dry-run", action="store_true", help="stop after triage: the list and the Modal estimate")

    r = rsub.add_parser("new", help="start a story from a brief and run every step")
    r.add_argument("brief", help='e.g. "Jer banned from Kick after the FDA call"')
    r.add_argument("--slug", help="the story's name (default: from the brief's words)")
    r.add_argument("--days", type=int, help="the window: the last N days (default [story] days)")
    r.add_argument("--since", help="the window starts YYYY-MM-DD")
    r.add_argument("--before", help="the window ends before YYYY-MM-DD")
    r.add_argument("--person", action="append", default=[], help="someone the brief doesn't name, by name or "
                   "nickname in streamers.json or [story] people (repeatable)")
    r.add_argument("--url", action="append", default=[], help="a video to transcribe whatever triage says "
                   "(YouTube, Kick VOD, Odysee; repeatable)")
    limits(r)
    r = rsub.add_parser("run", help="the steps not done yet (swarm, triage, ingest, find, dossier)")
    r.add_argument("slug")
    r.add_argument("--from", dest="start", choices=["swarm", "triage", "ingest", "find", "dossier"],
                   help="rerun this step and every one after it")
    limits(r)
    r = rsub.add_parser("add", help="videos to transcribe whatever triage says; then run --from triage")
    r.add_argument("slug")
    r.add_argument("urls", nargs="+")
    r = rsub.add_parser("status", help="each story's steps, transcripts, moments and model cost")
    r.add_argument("slug", nargs="?")
    r = rsub.add_parser("trail", help="the swarm's task tree: who found what, from which search")
    r.add_argument("slug")
    r = rsub.add_parser("show", help="the dossier, re-rendered from dossier.json (keeps hand edits; no model call)")
    r.add_argument("slug")
    r = rsub.add_parser("plan", help="the edit from the moments -> streams/story-<slug>/story.json (~$0.03; "
                                     "clipper --show <key> story plan ... picks the show)")
    r.add_argument("slug")
    r.add_argument("--force", action="store_true", help="plan again (the old edit is kept as story.<time>.json)")
    r = rsub.add_parser("cut", help="render story.json, then meta, thumbs and the Kitsu card; "
                                    "approved, it goes out asap")
    r.add_argument("slug")
    r.add_argument("--force", action="store_true", help="re-render every part and redo meta and thumbs")
    r.add_argument("--no-review", action="store_true", help="only the video: no meta, thumbs or Kitsu")

    p = sub.add_parser("narrate", help="re-voice a performance take with the ElevenLabs Voice Changer")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_narrate)
    r = rsub.add_parser("speak", help="script text file -> <script>.read.wav in [narrate] voice_id (perform to it)")
    r.add_argument("script", help="UTF-8 text; eleven_v3 tags like [sighs] [pause] allowed")
    r.add_argument("--voice-id", help="override [narrate] voice_id")
    r.add_argument("-o", "--out", help="output path")
    r.add_argument("--stability", type=float, help="0 creative | 0.5 natural | 1 steadiest (eleven_v3)")
    r.add_argument("--style", type=float, help="0-1 style exaggeration")
    r.add_argument("--speed", type=float, help="0.7-1.2")
    r = rsub.add_parser("voice", help="take (video or audio) -> <take>.voiced.mp4/.mp3 in [narrate] voice_id")
    r.add_argument("take")
    r.add_argument("--voice-id", help="override [narrate] voice_id")
    r.add_argument("-o", "--out", help="output path")
    r = rsub.add_parser("voices", help="list your voices and the shared library; --add one")
    r.add_argument("--search")
    r.add_argument("--accent", help="e.g. british")
    r.add_argument("--gender", help="male | female | neutral")
    r.add_argument("--age", help="young | middle_aged | old")
    r.add_argument("--add", metavar="OWNER:VOICE_ID", help="add a library voice to your account")
    r.add_argument("--name", default="Mother Moo", help="name for --add")

    p = sub.add_parser("perform", help="lip-sync take to a read: find takes, cut at pauses, stitch the renders")
    rsub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_perform)
    r = rsub.add_parser("prepare", help="find the takes, cut take N at the read's pauses -> DIR/pieceN.mp4")
    r.add_argument("recording", help="the phone/webcam video, the read playing out loud in it")
    r.add_argument("--read", required=True, help="the read the takes follow (clipper narrate speak)")
    r.add_argument("--take", type=int, default=1, help="which take (default 1)")
    r.add_argument("--out", help="output folder (default <recording>.perform)")
    r = rsub.add_parser("status", help="which pieces are rendered")
    r.add_argument("dir")
    r = rsub.add_parser("stitch", help="renderN.mp4 at their read times + the read -> final.mp4")
    r.add_argument("dir")
    r = rsub.add_parser("lipsync", help="redraw the mouth to match the read (LatentSync on Modal, or Sync)")
    r.add_argument("target", nargs="?", help="a perform dir (uses final.mp4 + its read) or a video")
    r.add_argument("--engine", choices=["latentsync", "sync", "both"],
                   help="default [perform] lipsync_engine")
    r.add_argument("--audio", help="the audio to sync to (default: the dir's read)")
    r.add_argument("--deploy", action="store_true", help="deploy the LatentSync Modal app")

    p = sub.add_parser("modal", help="Modal now: containers, runs, failures, this month's spend, archives")
    p.add_argument("--backfill", action="store_true",
                   help="add runs from before the run log (transcript.json cost records), once")
    p.set_defaults(fn=cmd_modal)

    p = sub.add_parser("cast", help="the cast library: a few good photos of each regular, from streams "
                                    "and the web, for covers and the weekly (#41)")
    csub = p.add_subparsers(dest="action", required=True)
    p.set_defaults(fn=cmd_cast)
    csub.add_parser("list", help="everyone: photos kept and new, who has fewer than [cast] min_photos")
    r = csub.add_parser("grab", help="the sharpest faces `clipper faces` named in a stream, as new photos")
    add_stream(r)
    r.add_argument("--person", help="only this person (default: everyone named)")
    r.add_argument("-n", type=int, default=4, help="photos tried per person (default 4)")
    r.add_argument("--needed", action="store_true", help="only people with fewer than [cast] target photos; "
                                                         "no faces.json is not an error (the worker's cast stage)")
    r.add_argument("--no-label", action="store_true", help="skip the label call")
    r = csub.add_parser("find", help="new photos from the web: Wikimedia Commons, the person's channels, "
                                     "image search ($BRAVE_API_KEY or [paths] brave_key)")
    r.add_argument("person")
    r.add_argument("--source", action="append", choices=["commons", "channels", "search"],
                   help="only these (repeatable; default all)")
    r.add_argument("--no-label", action="store_true", help="skip the label call")
    r = csub.add_parser("add", help="one photo from a file or a web address")
    r.add_argument("person")
    r.add_argument("src", metavar="FILE|URL")
    r.add_argument("--no-label", action="store_true", help="skip the label call")
    r = csub.add_parser("auto", help="a find from every source for everyone with fewer than [cast] target "
                                     "photos (serve runs it every [cast] auto_hours)")
    r.add_argument("--no-label", action="store_true", help="skip the label call")
    for act, what in (("keep", "keep these new photos"), ("drop", "delete these photos; their sources aren't fetched again")):
        r = csub.add_parser(act, help=what)
        r.add_argument("person")
        r.add_argument("ids", nargs="+", metavar="ID")
    r = csub.add_parser("label", help="set a photo's label (see [cast] labels)")
    r.add_argument("person")
    r.add_argument("id")
    r.add_argument("label")
    r = csub.add_parser("set", help="a person's other names and channels (YouTube, Kick) for find")
    r.add_argument("person")
    r.add_argument("--alias", action="append", help="another name (repeatable; replaces the list)")
    r.add_argument("--channel", action="append", metavar="URL", help="https://www.youtube.com/@name or "
                                                                     "https://kick.com/name (repeatable; replaces the list)")

    p = sub.add_parser("news", help="headlines about the shows' people: feeds, Reddit, Google News (#110)")
    nsub = p.add_subparsers(dest="news_cmd", required=True)
    p.set_defaults(fn=cmd_news)
    r = nsub.add_parser("check", help="fetch what's due, match the watchlist, judge the matches "
                                      "(the bot runs it every [news] check_minutes)")
    r.add_argument("--no-judge", action="store_true", help="fetch and match only, no model call")
    r = nsub.add_parser("list", help="recent stories, newest first")
    r.add_argument("--days", type=float, default=3)
    r.add_argument("--person", help="only stories about this person")
    r.add_argument("--all", action="store_true", help="dismissed ones too")
    r = nsub.add_parser("search", help="Google News now for a query, and the stored headlines")
    r.add_argument("query", help='e.g. "Ethan Klein"')
    r.add_argument("--days", type=int, default=7)
    nsub.add_parser("feeds", help="each source: last fetch, items, error")
    nsub.add_parser("watch", help="who is watched, under which names, for which shows")
    r = nsub.add_parser("dismiss", help="never post these stories")
    r.add_argument("ids", nargs="+", type=int, metavar="STORY")
    r = nsub.add_parser("context", help="these stories as the show's next stream_context")
    r.add_argument("ids", nargs="+", type=int, metavar="STORY")
    r.add_argument("--show", dest="context_show", help="the show (default: the story's)")

    p = sub.add_parser("costs", help="what clipper spent this month: models, Modal, fixed costs (#50)")
    p.add_argument("--month", help="YYYY-MM (default: this month, UTC)")
    p.add_argument("--json", action="store_true", help="the report as JSON")
    p.add_argument("--backfill", action="store_true",
                   help="add model spend earlier runs recorded in their output files, once")
    p.add_argument("--dry-run", action="store_true", help="with --backfill: list, write nothing")
    p.set_defaults(fn=cmd_costs)


def add_setup(sub) -> None:
    """Setup and checks."""
    from .setup import STEPS
    p = sub.add_parser("setup", help="guided first run on a new machine: roles, folders, encoder, keys")
    p.add_argument("--step", choices=STEPS, help="do this step again")
    p.add_argument("--check", action="store_true", help="each step's checks, no questions")
    p.add_argument("--answers", metavar="FILE",
                   help="answers file (TOML): no questions, defaults for what it leaves out; never keys")
    p.add_argument("--offline", action="store_true", help="the final check skips the network")
    p.add_argument("--sync-args", action="store_true", help=argparse.SUPPRESS)  # for deploy/bootstrap
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("doctor", help="check this machine: ffmpeg, GPU, fonts, keys, Kitsu/Discord/NAS")
    p.add_argument("--offline", action="store_true", help="skip the network checks")
    p.add_argument("--role", choices=["all", "gpu", "serve", "bot", "nas"], default="all",
                   help="only what this machine runs (gpu worker, serve, bot, nas recorder)")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("config", help="settings: where a value comes from (default, clipper.toml, show, ...)")
    csub = p.add_subparsers(dest="action", required=True)
    r = csub.add_parser("get", help="a setting's value and every layer that sets it, e.g. meta.model")
    r.add_argument("name", help="section.key, e.g. bleep.mode")
    r.add_argument("--stream", dest="for_stream", help="as this stream sees it (its show, its config.toml)")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("shows", help="list the [shows] profiles and which streams belong to each")
    p.set_defaults(fn=cmd_shows)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clipper", epilog=command_list(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to clipper.toml (default: $CLIPPER_CONFIG or ./clipper.toml)")
    ap.add_argument("--show", help="use this [shows.<key>] profile (default: the stream's, by Kick channel)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for add in (add_pipeline, add_review, add_services, add_extras, add_setup):
        add(sub)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_command(args)
    except ClipperError as e:
        if str(e):
            print(e, file=sys.stderr)
        raise SystemExit(e.status) from None


def run_command(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    if args.show:  # kept by every stage's for_stream, whatever the stream's channel (B4)
        cfg = config.pin_show(cfg, args.show)
    elif getattr(args, "stream", None):
        cfg = config.for_stream(cfg, stream_dir(cfg, args.stream))
    from . import costs  # what every model call this command makes is recorded under

    action = getattr(args, "action", None)
    stream = getattr(args, "stream", None)
    costs.set_context(args.cmd + (f" {action}" if isinstance(action, str) else ""),
                      Path(stream).name if isinstance(stream, str) else "")
    args.fn(cfg, args)


if __name__ == "__main__":
    main()

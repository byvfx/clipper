import json
import re

import pytest
from conftest import ROOT

from clipper import archive_index, jsonstate, story
from clipper.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["paths"].update(prompts=ROOT / "prompts", archive=tmp_path / "archive", streams=tmp_path / "streams")
    p = tmp_path / "streamers.json"
    p.write_text(json.dumps({"streamers": [
        {"name": "The Quartering (Jer)", "slug": "the-quartering",
         "channels": {"youtube": "@TheQuartering", "rumble": "https://rumble.com/c/TheQuartering"}},
        {"name": "DSP (DarkSydePhil)", "slug": "dsp", "channels": {"kick": "dspgamingreal", "youtube": "@DSPGaming"}},
    ]}), encoding="utf-8")
    defaults["paths"]["streamers"] = p
    defaults["story"]["people"] = {"Don Flurgundy": {"kick": "donflurgundy"}}
    return defaults


def transcribed(d, words: str, start: float = 100.0, **meta):
    """An item folder with words.jsonl, transcript.json and meta.json: a word a second."""
    d.mkdir(parents=True, exist_ok=True)
    ws = [{"word": w, "start": start + k, "end": start + k + 0.9} for k, w in enumerate(words.split())]
    (d / "words.jsonl").write_text("\n".join(json.dumps(w) for w in ws), encoding="utf-8")
    jsonstate.write(d / "transcript.json", {"segments": [{"start": start, "end": start + len(ws), "text": words}]})
    jsonstate.write(d / "meta.json", meta)


def test_people_come_from_the_brief_by_name_nickname_or_slug(cfg):
    people = story.roster(cfg)
    assert [p["name"] for p in story.named("Jer got banned from Kick", people)] == ["The Quartering (Jer)"]
    assert story.named("Jerry Springer is back", people) == []        # whole words only
    assert [p["name"] for p in story.named("the quartering and dsp feud", people)] == \
        ["The Quartering (Jer)", "DSP (DarkSydePhil)"]
    assert [p["name"] for p in story.named("a feud", people, ["Don Flurgundy"])] == ["Don Flurgundy"]
    with pytest.raises(ClipperError, match="nobody"):
        story.named("a feud", people, ["nobody"])


def test_seeds_are_the_platforms_the_swarm_can_list():
    p = {"name": "Jer", "channels": {"youtube": "@TheQuartering", "rumble": "https://rumble.com/c/x",
                                     "kick": "jer", "twitch": "jer"}}
    assert [(s["kind"], s["target"]) for s in story.seeds([p])] == \
        [("youtube_channel", "@TheQuartering"), ("kick_videos", "jer")]


def test_slugs(cfg):
    assert story.make_slug("Jer got BANNED from Kick after the FDA call!") == "jer-got-banned-kick-fda-call"
    with pytest.raises(ClipperError):
        story.check_slug("Bad Slug")
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    b = story.load_brief(cfg, slug)
    assert (b["since"], [p["name"] for p in b["people"]]) == ("2026-10-28", ["The Quartering (Jer)"])
    assert story.new(cfg, "Jer banned from Kick") == slug   # the same story again
    with pytest.raises(ClipperError, match="another brief"):
        story.new(cfg, "something else", slug=slug)


def test_findings_become_items_and_our_streams_are_linked(cfg, tmp_path):
    transcribed(tmp_path / "streams" / "kick-9", "jer got banned again chat",
                title="Kino", channel="kinocasinogaming", started_at="2026-11-01T01:00:00+00:00")
    yt = {"key": "yt:aaaaaaaaaaa", "source": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
          "title": "Jer banned", "channel": "Clips", "date": "2026-11-01", "duration_s": 600, "relevance": "strong"}
    found = [
        yt,
        {**yt, "key": "yt:bbbbbbbbbbb", "url": "https://youtu.be/bbbbbbbbbbb", "duration_s": 12},  # a Short
        {"key": "kick:" + "1" * 36, "source": "kick", "id": "kick-9", "url": "https://kick.com/x/videos/1",
         "m3u8": "https://stream/x.m3u8", "title": "our VOD", "duration_s": 3600, "relevance": "strong"},
        {"key": "kick:" + "2" * 36, "source": "kick", "id": "kick-7", "url": "https://kick.com/x/videos/2",
         "m3u8": "https://stream/y.m3u8", "title": "their VOD", "duration_s": 3600, "relevance": "maybe"},
        {"key": "kick-9 s03", "source": "our streams", "url": "kick-9 s03", "title": "segment"},
        {"key": "dexerto.com/jer", "source": "web", "url": "https://dexerto.com/jer", "title": "article"},
    ]
    ours, cands = story.candidates(cfg, found, {})
    assert [it["id"] for it in ours] == ["kick-9"]          # once, though found twice
    assert ours[0]["dir"].endswith("kick-9") and ours[0]["date"] == "2026-11-01"
    assert ours[0]["url"] == "https://kick.com/x/videos/1"   # the VOD, not the channel page
    assert [(it["id"], it["source"]) for it in cands] == [("yt-aaaaaaaaaaa", "youtube"), ("kick-7", "kick")]
    assert cands[1]["m3u8"] == "https://stream/y.m3u8"
    assert story.candidates(cfg, found, {"yt-aaaaaaaaaaa": {}})[1][0]["id"] == "kick-7"  # already have it


def test_triage_keeps_to_the_limits():
    its = [{"id": str(k), "duration_s": s} for k, s in enumerate([1800, 0, 3600, 600])]
    keep, left = story.fit(its, max_videos=3, max_hours=2.0)
    assert ([i["id"] for i in keep], [i["id"] for i in left]) == (["0", "1", "3"], ["2"])  # unknown length = 1 h
    assert [i["id"] for i in story.fit(its, max_videos=1, max_hours=9)[0]] == ["0"]


class FakeLLM:
    """Answers by schema; dossier answers in turn from `dossiers`."""

    def __init__(self, dossiers=()):
        self.cost, self.dossiers, self.users = 0.0, list(dossiers), {}

    def json_call(self, model, system, user, schema, effort):
        self.users.setdefault(id(schema), []).append(user)
        if schema is story.TRIAGE_SCHEMA:
            return {"picks": [{"n": 0, "why": "the stream"}]}, {}
        if schema is archive_index.EXPAND_SCHEMA:
            return {"queries": [{"fts": "banned", "why": "the ban"}]}, {}
        if schema is story.MOMENTS_SCHEMA:
            blocks = re.findall(r"\[(\d+)\] [^\n]*\n([^\n]*)", user)
            n = {text.split()[0]: int(k) for k, text in blocks}
            return {"picks": [
                {"n": n["today"], "role": "event", "fit": "strong", "quote": "I got banned from Kick for the FDA call",
                 "why": "his own words"},
                {"n": n["today"], "role": "event", "fit": "maybe", "quote": "he said he would sue everyone tomorrow",
                 "why": "made up"},
                {"n": n["jer"], "role": "reaction", "fit": "strong", "quote": "jer got banned again chat",
                 "why": "our show"}]}, {}
        return self.dossiers.pop(0), {}


def dossier_answer(cites):
    return {"summary": "He was banned.", "timeline": [{"date": "2026-11-01", "what": "banned", "status": "shown",
                                                       "cites": cites}],
            "people": [{"name": "Jer", "role": "banned"}], "who_said_what": [{"who": "Jer", "moment": "M01", "note": "x"}],
            "conflicts": [], "coverage": "Clips only.", "gaps": ["Kick's side"],
            "angles": [{"angle": "the ban", "moments": ["M01", "M02"]}]}


def test_a_story_from_findings_to_dossier(cfg, tmp_path, capsys):
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    transcribed(tmp_path / "streams" / "kick-9", "jer got banned again chat can you believe it", start=50,
                title="Kino", channel="kinocasinogaming", started_at="2026-11-01T01:00:00+00:00", source="source.mkv")
    jsonstate.write(d / "swarm.json", {"runs": [{"usd": 0}], "findings": [
        {"key": "yt:aaaaaaaaaaa", "source": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
         "title": "Jer banned", "channel": "TheQuartering", "date": "2026-11-01", "duration_s": 600,
         "relevance": "strong", "why": "his stream"},
        {"key": "dexerto.com/jer", "source": "web", "url": "https://dexerto.com/jer", "title": "article",
         "relevance": "maybe", "why": "news"},
        {"key": "kick-9 s03", "source": "our streams", "url": "kick-9 s03", "title": "segment", "relevance": "strong",
         "why": "we covered it"}]})

    llm = FakeLLM()
    story.run(cfg, slug, dry_run=True, claude=llm)
    cat = jsonstate.load(d / "catalog.json")
    assert set(cat) == {"yt-aaaaaaaaaaa", "kick-9"} and cat["kick-9"]["ours"]
    assert "dry run" in capsys.readouterr().out and not (d / "index.sqlite").exists()

    # what ingest would have transcribed
    transcribed(d / "yt-aaaaaaaaaaa", "today I got banned from kick for the FDA call and I am not happy")
    llm = FakeLLM([dossier_answer(["M01", "S99"]), dossier_answer(["M01", "S01"])])
    story.run(cfg, slug, claude=llm)
    m = jsonstate.load(d / "moments.json")
    assert m["unverified"] == 1      # the made-up quote
    assert [(x["id"], x["item"], x["role"], x["quote"]) for x in m["moments"]] == [
        ("M01", "kick-9", "reaction", "jer got banned again chat"),
        ("M02", "yt-aaaaaaaaaaa", "event", "I got banned from kick for the FDA call")]  # the transcript's wording
    assert m["moments"][1]["t"] == 101 and m["moments"][1]["link"].endswith("&t=98s")
    users = llm.users[id(story.DOSSIER_SCHEMA)]
    assert len(users) == 2 and "S99" in users[1]   # sent back once
    assert "S01 | video (transcribed)" in users[0]
    assert "S03 | our streams (transcribed)" in users[0]
    md = (d / "dossier.md").read_text(encoding="utf-8")
    assert "- 2026-11-01 [shown] banned (M01, S01)" in md and "> jer got banned again chat" in md

    # hand edits survive a rerun
    with jsonstate.update(d / "dossier.json", {}) as x:
        x["notes"] = "ask Don"
        x["sources"][1]["struck"] = True
    with pytest.raises(ClipperError, match="S02"):   # a struck source can't be cited
        story.run(cfg, slug, start="dossier", claude=FakeLLM([dossier_answer(["S02"]), dossier_answer(["S02"])]))
    (d / "yt-aaaaaaaaaaa" / "words.jsonl").unlink()   # as if its download had failed
    llm = FakeLLM([dossier_answer(["S01"])])
    story.run(cfg, slug, start="dossier", claude=llm)
    assert "S02" not in llm.users[id(story.DOSSIER_SCHEMA)][0]
    assert "S01 | video |" in llm.users[id(story.DOSSIER_SCHEMA)][0]   # picked, not transcribed
    x = jsonstate.load(d / "dossier.json")
    assert x["notes"] == "ask Don" and x["sources"][1]["struck"]
    story.show(cfg, slug)
    out = capsys.readouterr().out
    assert "Notes: ask Don" in out and "- S02 (struck) [web, maybe]" in out


def test_a_linked_stream_is_indexed_with_the_catalogs_meta(cfg, tmp_path):
    from clipper import archive

    sdir = tmp_path / "streams" / "kick-9"
    transcribed(sdir, "jer got banned", title="stream title", source="source.mkv")
    d = archive.subject_dir(cfg, "story-x")
    archive.save_catalog(d, {"kick-9": {"id": "kick-9", "source": "kick", "url": "https://kick.com/k",
                                        "title": "Kino", "date": "2026-11-01", "dir": str(sdir)}})
    archive_index.build(cfg, "story-x", rebuild=False)
    db = archive_index.open_index(cfg, "story-x")
    assert db.execute("SELECT title, source FROM items").fetchall() == [("Kino", "kick")]
    assert [h["item"] for h in archive_index.hits(db, "banned", None, None, 5)] == ["kick-9"]
    db.close()


def test_given_urls(cfg):
    it = story.given_item(cfg, "https://youtu.be/aaaaaaaaaaa?t=30")
    assert (it["id"], it["source"], it["key"]) == ("yt-aaaaaaaaaaa", "youtube", "yt:aaaaaaaaaaa")
    with pytest.raises(ClipperError, match="not a YouTube"):
        story.given_item(cfg, "https://rumble.com/v123-jer.html")


def test_a_failed_download_stops_before_the_dossier(cfg, monkeypatch):
    from clipper import archive

    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    archive.save_catalog(d, {"yt-aaaaaaaaaaa": {"id": "yt-aaaaaaaaaaa", "source": "youtube", "url": "u"}})
    jsonstate.write(d / "swarm.json", {"runs": [{}], "findings": []})
    jsonstate.write(d / "triage.json", {})
    seen = {}

    def fails(cfg, d, subject, todo, gpu, parallel, retry_on):
        seen["retry"] = retry_on
        for it in todo:
            (d / it["id"]).mkdir(parents=True, exist_ok=True)
            (d / it["id"] / "error.txt").write_text("RuntimeError: Sign in to confirm you're not a bot", encoding="utf-8")

    monkeypatch.setattr(archive, "_transcribe_all", fails)
    cfg["story"]["youtube_bot_retry"] = False
    with pytest.raises(ClipperError, match="1 of 1 failed.*bot check"):
        story.run(cfg, slug, claude=FakeLLM())
    assert seen["retry"] == story.ytdl.AGE and not (d / "moments.json").exists()
    cfg["story"]["youtube_bot_retry"] = True
    with pytest.raises(ClipperError):
        story.run(cfg, slug, claude=FakeLLM())
    assert seen["retry"] == story.ytdl.AGE + story.ytdl.BOT
    assert jsonstate.load(d / "runs.json")[0]["usd"] == 0   # logged though it failed


def test_a_triage_rerun_replaces_the_picks_still_waiting(cfg, tmp_path):
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    vid = {"source": "youtube", "title": "Jer banned", "channel": "c", "date": "2026-11-01", "duration_s": 600,
           "relevance": "strong"}
    found = [{**vid, "key": f"yt:{c * 11}", "url": f"https://youtu.be/{c * 11}"} for c in "ab"]
    jsonstate.write(d / "swarm.json", {"runs": [{}], "findings": found[:1]})
    story.run(cfg, slug, dry_run=True, claude=FakeLLM())
    transcribed(d / "zz", "an old one")
    with jsonstate.update(d / "catalog.json", {}) as cat:
        cat["zz"] = {"id": "zz", "source": "youtube", "url": "x"}
    jsonstate.write(d / "swarm.json", {"runs": [{}], "findings": found[1:]})
    story.run(cfg, slug, start="triage", dry_run=True, claude=FakeLLM())
    assert set(jsonstate.load(d / "catalog.json")) == {"zz", "yt-bbbbbbbbbbb"}


def test_each_video_counts_once_against_the_limits():
    its = [{"id": "a", "duration_s": 3600}, {"id": "a", "duration_s": 3600, "why": "again"}, {"id": "b", "duration_s": 3600}]
    assert [i["id"] for i in story.fit(story.unique(its), max_videos=2, max_hours=2)[0]] == ["a", "b"]


def test_a_kick_vod_given_by_hand_that_we_recorded_is_linked(cfg, tmp_path, monkeypatch):
    transcribed(tmp_path / "streams" / "kick-9", "jer got banned", title="Kino", started_at="2026-11-01T01:00:00+00:00")
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28", urls=["https://kick.com/x/videos/1"])
    d = story.story_dir(cfg, slug)
    jsonstate.write(d / "swarm.json", {"runs": [{}], "findings": []})
    monkeypatch.setattr(story, "given_item", lambda cfg, u: {"id": "kick-9", "source": "kick", "url": u,
                                                             "duration_s": 99999, "m3u8": "m"})
    cfg["story"]["max_hours"] = 1
    story.run(cfg, slug, dry_run=True, claude=FakeLLM())
    t = jsonstate.load(d / "triage.json")
    assert (t["linked"], t["picked"], t["over_limits"]) == (["kick-9"], [], [])
    assert jsonstate.load(d / "catalog.json")["kick-9"]["url"] == "https://kick.com/x/videos/1"


def test_no_windows_still_replaces_the_moments(cfg):
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    from clipper import archive

    transcribed(d / "yt-x", "nothing about it here")
    archive.save_catalog(d, {"yt-x": {"id": "yt-x", "source": "youtube", "url": "u"}})
    archive_index.build(cfg, d.name, rebuild=False)
    jsonstate.write(d / "moments.json", {"moments": [{"id": "M01"}]})   # an older run's
    assert story.find(cfg, story.load_brief(cfg, slug), d, FakeLLM(), story.Budget(1, FakeLLM()), _prompts(cfg)) == []
    m = jsonstate.load(d / "moments.json")
    assert m["moments"] == [] and m["queries"] == ["banned"]


def _prompts(cfg):
    from clipper.prompts import Prompts

    p = Prompts(cfg)
    p.version = cfg["story"]["prompt_version"]
    return p


def test_a_claim_citing_nothing_is_sent_back():
    out = dossier_answer([])
    assert story.unresolved(out, {"M01", "M02"}, {"M01", "M02"}) == ["no cite: timeline 'banned'"]
    assert story.unresolved(dossier_answer(["M01"]), {"M01", "M02"}, {"M01", "M02"}) == []


def test_a_sites_name_is_not_a_channel():
    f = {"key": "yt:ccccccccccc", "channel": "YouTube", "title": "x"}
    assert story.as_item(f)["channel"] == ""
    assert story.as_item({**f, "channel": "DSP Meltdown"})["channel"] == "DSP Meltdown"

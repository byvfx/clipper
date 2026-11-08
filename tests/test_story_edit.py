import json

import pytest
from conftest import ROOT
from test_story import transcribed

from clipper import (
    config,
    jsonstate,
    media,
    meta,
    receipts,
    retake,
    review,
    story,
    story_edit,
)
from clipper.archive import save_catalog
from clipper.errors import ClipperError

TOML = """
[shows.kino]
channels = ["kinocasinogaming"]
[shows.kino.meta]
show_name = "Kino Casino"

[shows.c404]
channels = ["donflurgundy"]
[shows.c404.meta]
show_name = "Channel 404"
"""
QUOTE = "so last night I pulled my own credit report to confirm that it was fine"


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "clipper.toml").write_text(TOML, encoding="utf-8")
    cfg = config.load(tmp_path / "clipper.toml", use_local=False)
    cfg["paths"].update(prompts=ROOT / "prompts", archive=tmp_path / "archive", streams=tmp_path / "streams",
                        streamers=tmp_path / "streamers.json")
    (tmp_path / "streamers.json").write_text(json.dumps({"streamers": [
        {"name": "DSP (DarkSydePhil)", "slug": "dsp", "channels": {"youtube": "@DSPGaming"}}]}), encoding="utf-8")
    return cfg


def moment(mid, item, t, quote, ours=False, channel="DSP Meltdown", role="event"):
    return {"id": mid, "subject": "", "item": item, "source": "kick" if ours else "youtube", "channel": channel,
            "date": "2026-10-30", "title": f"{item} title", "url": f"https://www.youtube.com/watch?v={item[3:]}",
            "page": "", "ours": ours, "t": t, "link": "", "quote": quote, "role": role, "fit": "strong", "why": "x"}


class EditLLM:
    """The edit model: narration (v2) when the schema asks for it; a lead_in for the moments in leads."""
    def __init__(self, parts, leads=()):
        self.cost, self.parts, self.leads, self.user, self.schema = 0.0, parts, leads, "", None

    def json_call(self, model, system, user, schema, effort):
        assert schema in (story_edit.PLAN_SCHEMA, story_edit.NARRATED_SCHEMA)
        self.user, self.schema = user, schema
        narrated = schema is story_edit.NARRATED_SCHEMA
        out = {"title": "DSP's credit score", "topic": "DSP credit score", "summary": "s", "description": "d",
               "figures": ["DSP"], "parts": [{"moment": m, "chapter": f"Part {m}", "why": "w",
                                              **({"lead_in": f"October 30: {m}." if m in self.leads else ""}
                                                 if narrated else {})} for m in self.parts]}
        if narrated:
            out.update(tldr="DSP says his score is 590. Nobody buys it.", close="He hasn't shown the report.")
        return out, {}


@pytest.fixture
def planned(cfg, tmp_path):
    """A story with three moments: his video, our stream playing it, a reaction."""
    slug = story.new(cfg, "DSP says his credit score is 590", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    transcribed(d / "yt-aaaaaaaaaaa", QUOTE + " and it was 590", start=100)
    transcribed(d / "yt-bbbbbbbbbbb", "Phil is lying about the credit score again and everyone knows it now", start=50)
    ours = tmp_path / "streams" / "kick-9"
    transcribed(ours, QUOTE + " chat look at him", start=5000, channel="donflurgundy")
    (ours / "source.mkv").write_bytes(b"")
    save_catalog(d, {
        "yt-aaaaaaaaaaa": {"id": "yt-aaaaaaaaaaa", "source": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
        "yt-bbbbbbbbbbb": {"id": "yt-bbbbbbbbbbb", "source": "youtube", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
        "kick-9": {"id": "kick-9", "source": "kick", "url": "https://kick.com/donflurgundy", "dir": str(ours), "ours": True}})
    ms = [moment("M01", "yt-aaaaaaaaaaa", 100.0, QUOTE),
          moment("M02", "kick-9", 5000.0, QUOTE, ours=True, channel="donflurgundy"),
          moment("M03", "yt-bbbbbbbbbbb", 50.0, "Phil is lying about the credit score again", role="reaction",
                 channel="YouTube")]
    for m in ms:
        m["subject"] = d.name
    jsonstate.write(d / "moments.json", {"moments": ms})
    jsonstate.write(d / "dossier.json", {"dossier": {
        "summary": "He said 590.", "angles": [],
        "timeline": [{"date": "2026-10-30", "what": "DSP says 590 on stream.", "status": "shown", "cites": ["M01"]}],
        "conflicts": [{"what": "He says 590; chat says lower.", "cites": ["M01", "M03"]}]}})
    return slug


def test_plan_bounds_parts_and_drops_a_repeat(cfg, planned):
    with pytest.raises(ClipperError, match="which show"):
        story_edit.plan(cfg, planned, claude=EditLLM(["M01"]))
    cfg["story"]["show"] = "kino"
    llm = EditLLM(["M01", "M02", "M03", "M99"])
    data = story_edit.plan(cfg, planned, claude=llm)
    assert "M02 | event, strong | 2026-10-30 | donflurgundy (our show, Kino Casino)" in llm.user
    p1, p2, p3 = data["parts"]
    assert (p1["use"], p2["use"], p3["use"]) == (True, False, True)
    assert p2["note"] == "repeats P01 (DSP Meltdown)" and p2["channel"] == "Channel 404"
    assert p1["start"] < 100 and p1["end"] > 112 and p1["found"]   # the quote, 100-112 s, and room
    sdir = story_edit.stream_dir(cfg, planned)
    m = jsonstate.load(sdir / "meta.json")
    assert (m["kind"], m["channel"], m["show"]) == ("story", "kinocasinogaming", "kino")
    assert config.show_of_channel(cfg, config.stream_channel(sdir)) == "kino"
    # planned: printed again, not re-planned, until --force
    assert story_edit.plan(cfg, planned, claude=EditLLM(["M03"]))["parts"][0]["moments"] == ["M01"]
    story_edit.plan(cfg, planned, force=True, claude=EditLLM(["M03"]))
    assert len(list(sdir.glob("story.*.json"))) == 1


def test_shape_merges_overlaps_and_stops_at_the_length():
    def part(n, item, a, b, quote):
        return {"id": f"P0{n}", "subject": "s", "item": item, "start": a, "end": b, "quote": quote,
                "moments": [f"M0{n}"], "channel": "c", "use": True, "note": ""}

    parts = [part(1, "a", 0, 30, "one two three four five six"), part(2, "a", 25, 50, "seven eight"),
             part(3, "b", 0, 40, "nine ten"), part(4, "c", 0, 40, "eleven")]
    story_edit.shape(parts, 100)
    assert [p["use"] for p in parts] == [True, False, True, False]
    assert (parts[0]["start"], parts[0]["end"], parts[0]["moments"]) == (0, 50, ["M01", "M02"])
    assert parts[1]["note"] == "merged into P01" and parts[3]["note"] == "past [story] max_minutes"
    # a merge that would pass the length leaves the part out instead
    parts = [part(1, "a", 0, 30, "one two three four five six"), part(2, "a", 25, 50, "seven eight")]
    story_edit.shape(parts, 40)
    assert (parts[0]["end"], parts[1]["use"], parts[1]["note"]) == (30, False, "past [story] max_minutes")


def test_chapters_are_a_part_each_at_least_ten_seconds_apart():
    parts = [{"chapter": c} for c in "ABCD"]
    assert story_edit.chapters_of(parts, [0, 5, 40, 80], 120) == \
        [{"t": 0, "title": "A"}, {"t": 40, "title": "C"}, {"t": 80, "title": "D"}]
    assert story_edit.chapters_of(parts[:2], [0, 40], 80) == []
    assert story_edit.chapters_of(parts, [0, 20, 40, 60], 65) == \
        [{"t": 0, "title": "A"}, {"t": 20, "title": "B"}, {"t": 40, "title": "C"}]  # a 5 s last part folds in


def test_cut_writes_one_segment_with_its_words_chapters_and_sources(cfg, planned, monkeypatch):
    cfg["story"].update(show="kino", narrate=False)   # the clips alone (the narration: below)
    story_edit.plan(cfg, planned, claude=EditLLM(["M01", "M03", "M02"]))
    sdir = story_edit.stream_dir(cfg, planned)
    data = jsonstate.load(sdir / "story.json")
    data["parts"][2].update(use=True, note="")  # by hand: our stream too
    assert data["parts"][1]["channel"] == "?"    # the search said "YouTube"
    jsonstate.write(sdir / "story.json", data)
    made = []

    def fake_clip(cfg, idir, r, src, fonts, bug, bleeper, dst, label="THEN: ", seek=False):
        made.append((r["id"], src.name, seek))
        dst.write_bytes(b"x")
        return 1

    monkeypatch.setattr(receipts, "clip", fake_clip)
    monkeypatch.setattr(receipts, "first_font", lambda paths: ROOT)
    monkeypatch.setattr(receipts, "join", lambda parts, lst, dst: dst.write_bytes(b"v"))
    monkeypatch.setattr(story_edit, "fetch", lambda cfg, p, dst: dst.write_bytes(b"s") and "Phil Fan")
    monkeypatch.setattr(media, "duration", lambda p: 20.0)
    final = story_edit.cut(cfg, planned, review=False)
    assert [(i, seek) for i, _, seek in made] == [("P01", False), ("P02", False), ("P03", True)]
    assert made[2][1] == "source.mkv"   # our own stream: its source, read from the part's start
    assert [p["channel"] for p in jsonstate.load(sdir / "story.json")["parts"]] ==         ["DSP Meltdown", "Phil Fan", "Channel 404"]   # yt-dlp's uploader for the site's name
    seg = jsonstate.load(final.with_suffix(".json"))
    assert (seg["start_s"], seg["end_s"], seg["bleeps"]) == (0.0, 60.0, 3)
    assert seg["signature"][1][3] == "Phil Fan"   # saved after yt-dlp named the channel: the next cut matches
    assert [c["t"] for c in seg["chapters"]] == [0, 20, 40]
    words = [json.loads(x) for x in (sdir / "words.jsonl").read_text(encoding="utf-8").splitlines()]
    first = next(w for w in words if w["word"] == "Phil")    # P02 starts 20 s into the video
    assert 20 <= first["start"] < 30 and len({w["seg"] for w in words}) == 3
    m = jsonstate.load(sdir / "meta.json")
    assert [s["t"] for s in m["sources"]] == [0, 20, 40] and m["duration_s"] == 60
    assert jsonstate.load(sdir / "segments.json")["segments"][0]["file"] == final.name
    made.clear()
    story_edit.cut(cfg, planned, review=False)      # nothing changed: nothing rendered
    assert made == []
    calls = []
    monkeypatch.setattr(meta, "run", lambda sdir, cfg, missing=False: calls.append(("meta", missing)))
    from clipper import thumbs
    monkeypatch.setattr(thumbs, "run", lambda sdir, cfg, force=False, missing=False:
                        calls.append(("thumbs", force, missing)))
    monkeypatch.setattr(review, "publish", lambda sdir, cfg, force=False, only=None: calls.append(("review",)))
    story_edit.cut(cfg, planned)      # after --no-review: meta and thumbs still to do
    assert calls == [("meta", False), ("thumbs", True, False), ("review",)]
    calls.clear()
    story_edit.cut(cfg, planned)
    assert calls == [("meta", True), ("thumbs", False, True), ("review",)]
    # reordered by hand: the same length, a new video: rendered parts kept, meta and the cover redone
    data = jsonstate.load(sdir / "story.json")
    data["parts"][0], data["parts"][1] = data["parts"][1], data["parts"][0]
    data["parts"][2]["channel"] = "Channel 404 News"   # a new lower third renders that part again
    jsonstate.write(sdir / "story.json", data)
    made.clear()
    calls.clear()
    story_edit.cut(cfg, planned)
    assert [i for i, _, _ in made] == ["P03"]
    assert jsonstate.load(final.with_suffix(".json"))["bleeps"] == 3   # the kept parts' bleeps still count
    assert calls == [("meta", False), ("thumbs", True, False), ("review",)]
    # planned again under another topic: still one s00, the old one's files gone
    (sdir / "segments" / "s00_old-topic.json").write_text("{}", encoding="utf-8")
    story_edit.cut(cfg, planned, review=False)
    assert sorted(f.name for f in (sdir / "segments").iterdir()) == [final.with_suffix(".json").name, final.name]


def test_a_struck_sources_moments_are_left_out(cfg, planned):
    cfg["story"]["show"] = "kino"
    d = story.story_dir(cfg, planned)
    cat = jsonstate.load(d / "catalog.json")
    cat["yt-bbbbbbbbbbb"]["key"] = "yt:bbbbbbbbbbb"
    save_catalog(d, cat)
    dj = jsonstate.load(d / "dossier.json")
    dj["sources"] = [{"id": "S01", "key": "yt:bbbbbbbbbbb", "struck": True}]
    dj["dossier"]["timeline"].append({"date": "2026-10-31", "what": "Chat calls it a lie.", "status": "reported",
                                      "cites": ["M03", "S01"]})   # backed only by what was struck
    jsonstate.write(d / "dossier.json", dj)
    llm = EditLLM(["M01", "M03"])
    data = story_edit.plan(cfg, planned, claude=llm)
    assert "M03" not in llm.user and [p["moments"] for p in data["parts"]] == [["M01"]]
    assert "Chat calls it a lie" not in llm.user
    assert "DSP says 590 on stream." in llm.user and "He says 590; chat says lower." in llm.user   # M01 still backs them


def test_check_parts():
    assert story_edit.check_parts({"parts": [{"id": "P01", "use": True, "start": 5, "end": 2},
                                             {"id": "P01", "use": False, "start": 0, "end": 1}]}) == \
        ["P01: start 5 / end 2", "part id 'P01' (P01, P02 ..., each once)"]


def test_a_storys_credit_episode_and_retake(cfg, tmp_path):
    m = {"kind": "story", "started_at": "2026-11-03T20:00:00+00:00",
         "sources": [{"t": 0, "channel": "DSP Meltdown", "date": "2026-10-31", "link": "https://youtu.be/x&t=5s"},
                     {"t": 75.5, "channel": "Kino Casino", "date": "", "link": "https://kick.com/k at 1:00:00"}]}
    credit = meta.credit_line(m, cfg["meta"], cfg)
    assert credit.splitlines() == [cfg["story"]["credit"], "- DSP Meltdown, 2026-10-31: https://youtu.be/x&t=5s (at 0:00)",
                                   "- Kino Casino: https://kick.com/k at 1:00:00 (at 1:15)"]
    sdir = tmp_path / "streams" / "story-dsp"
    sdir.mkdir(parents=True)
    jsonstate.write(sdir / "meta.json", m)
    assert review.episode_name(sdir, cfg["review"]) == "2026-11-03 story-dsp"
    with pytest.raises(ClipperError, match="story cut dsp"):
        retake.trim_segment(cfg, sdir, {"id": "s00"}, 0, 10)


def test_a_storys_video_is_never_one_of_our_streams(cfg, tmp_path):
    from clipper import investigate

    for sid in ("kick-1", "story-dsp"):
        transcribed(tmp_path / "streams" / sid, "dsp credit score", channel="kinocasinogaming")
        jsonstate.write(tmp_path / "streams" / sid / "segments.json",
                        {"segments": [{"id": "s00", "title": "DSP credit score", "summary": ""}]})
    assert [f["channel"] for f in investigate.our_streams(cfg, ["credit"], 5)] == ["kick-1"]
    assert story.our_stream(cfg, "story-dsp") is None and story.our_stream(cfg, "kick-1")


def test_source_links():
    p = {"page": "", "url": "https://www.youtube.com/watch?v=HjJ", "ours": True, "source": "kick", "start": 581.0}
    assert story_edit.source_link(p) == "https://www.youtube.com/watch?v=HjJ&t=578s"   # our pre-show
    assert story_edit.source_link({**p, "url": "https://kick.com/donflurgundy"}) == "https://kick.com/donflurgundy"
    assert story_edit.source_link({**p, "ours": False, "source": "odysee", "url": "lbry://x",
                                   "page": "https://odysee.com/@a/x"}) == "https://odysee.com/@a/x at 0:09:38"


def test_plan_writes_the_narration_from_the_dossier(cfg, planned):
    cfg["story"]["show"] = "kino"
    llm = EditLLM(["M01", "M03"], leads=["M01"])
    data = story_edit.plan(cfg, planned, claude=llm)
    assert llm.schema is story_edit.NARRATED_SCHEMA and data["prompt_version"] == "v2"
    assert "Timeline:\n- 2026-10-30 [shown] DSP says 590 on stream." in llm.user
    assert "Conflicts:\n- He says 590; chat says lower." in llm.user
    assert data["tldr"].startswith("DSP says") and data["close"] == "He hasn't shown the report."
    assert [p["lead_in"] for p in data["parts"]] == ["October 30: M01.", ""]
    cfg["story"]["edit_prompt_version"] = "v1"     # the clips-only edit: the old schema, no timeline
    cfg["_resolved"].clear()                        # for_show caches each show's config
    data = story_edit.plan(cfg, planned, force=True, claude=llm)
    assert llm.schema is story_edit.PLAN_SCHEMA and "Timeline:" not in llm.user
    assert data["tldr"] == "" and story_edit.sequence(data, data["parts"], True) == \
        [{"part": p} for p in data["parts"]]


def test_cut_reads_the_narration_as_cards(cfg, planned, monkeypatch):
    from clipper import narrate

    cfg["story"].update(show="kino", voice_id="narrator1", tts_settings={"speed": 1.0})
    cfg["narrate"]["voice_id"] = "mother-moo"
    story_edit.plan(cfg, planned, claude=EditLLM(["M01", "M03"], leads=["M01"]))
    sdir = story_edit.stream_dir(cfg, planned)
    reads, stills = [], []

    def fake_read(nc, text, folder):
        reads.append((nc["voice_id"], nc["tts_settings"], text))
        return folder / "vo.wav"

    def fake_still(work, name, lines, rc, bug, dst, vo=None, min_s=0.0, extra=""):
        stills.append((name, lines[0][4], vo is not None, min_s))
        dst.write_bytes(b"c")

    def fake_clip(cfg, idir, r, src, fonts, bug, bleeper, dst, label="THEN: ", seek=False):
        dst.write_bytes(b"x")
        return 0

    monkeypatch.setattr(narrate, "read", fake_read)
    monkeypatch.setattr(receipts, "still", fake_still)
    monkeypatch.setattr(receipts, "clip", fake_clip)
    monkeypatch.setattr(receipts, "first_font", lambda paths: ROOT)
    monkeypatch.setattr(receipts, "join", lambda parts, lst, dst: dst.write_bytes(b"v"))
    monkeypatch.setattr(story_edit, "fetch", lambda cfg, p, dst: dst.write_bytes(b"s") and "")
    monkeypatch.setattr(media, "duration", lambda p: 4.0 if p.stem == "vo" else    # a read: shorter than its card
                        10.0 if p.stem.endswith(("tldr", "_lead", "close")) else 20.0)
    final = story_edit.cut(cfg, planned, review=False)
    # the short version, P01's lead-in, P01, P02 (no lead-in: its chapter starts at the clip), the close
    assert [n for n, *_ in stills] == ["tldr", "P01_lead", "close"]
    assert stills[0][1] == "THE SHORT VERSION" and stills[1][1] == "PART M01" and stills[0][2]
    # read to render, then again (from the cache) to time the captions
    assert {v for v, _, _ in reads} == {"narrator1"} and reads[0][1] == {"speed": 1.0}   # not Mother Moo
    assert [x for _, _, x in reads[:3]] == [x for _, _, x in reads[3:]]
    seg = jsonstate.load(final.with_suffix(".json"))
    assert seg["duration_s"] == 10 + 10 + 20 + 20 + 10
    assert [(c["t"], c["title"]) for c in seg["chapters"]] == \
        [(0, "The short version"), (10, "Part M01"), (40, "Part M03"), (60, "Where it stands")]
    m = jsonstate.load(sdir / "meta.json")
    assert [s["t"] for s in m["sources"]] == [20, 40]
    words = [json.loads(x) for x in (sdir / "words.jsonl").read_text(encoding="utf-8").splitlines()]
    tldr = [w for w in words if w["start"] < 10]
    assert " ".join(w["word"] for w in tldr) == "DSP says his score is 590. Nobody buys it."
    assert tldr[-1]["end"] == 4.0 and len({w["seg"] for w in tldr}) == 2   # over the read, not the card
    stills.clear()
    story_edit.cut(cfg, planned, review=False)      # nothing changed
    assert stills == []
    data = jsonstate.load(sdir / "story.json")
    data["tldr"] = "DSP says 590."                  # a line edited by hand: the video again
    jsonstate.write(sdir / "story.json", data)
    story_edit.cut(cfg, planned, review=False)
    assert [n for n, *_ in stills] == ["tldr", "P01_lead", "close"] and reads[-3][2] == "DSP says 590."
    cfg["story"]["voice_id"] = ""                   # no narrator: silent cards, up long enough to read
    cfg["_resolved"].clear()
    stills.clear()
    story_edit.cut(cfg, planned, review=False)
    assert stills and all(not voiced and min_s > 2 for _, _, voiced, min_s in stills)
    cfg["story"]["read_wpm"] = 100                  # a slower pace: the cards again
    cfg["_resolved"].clear()
    stills.clear()
    story_edit.cut(cfg, planned, review=False)
    assert len(stills) == 3
    cfg["bleep"].update(mode="bleep", patterns=["part"], profanity=False)   # a listed word on a card: masked
    cfg["_resolved"].clear()
    stills.clear()
    story_edit.cut(cfg, planned, review=False)      # a new pattern alone re-cuts the cards
    assert stills[1][1] == "P*** M01"
    cfg["bleep"]["patterns"] = ["report"]           # ... in a read: refused, rewritten by hand
    cfg["_resolved"].clear()
    with pytest.raises(ClipperError, match="narration of close"):
        story_edit.cut(cfg, planned, review=False)
    assert not any(w["start"] < 10 for w in
                   map(json.loads, (sdir / "words.jsonl").read_text(encoding="utf-8").splitlines()))


def test_card_lines_and_reading_time():
    lines = story_edit.card_lines((ROOT, ROOT), {"header": "THE SHORT VERSION", "title": "A title",
                                                 "text": "word " * 40})
    assert [ln[1] for ln in lines] == [64, 80, 46] and [ln[3] for ln in lines] == sorted(ln[3] for ln in lines)
    assert all(len(row) <= 62 for row in lines[2][4].split("\n"))
    rows = lines[2][4].count("\n") + 1
    bottom = lines[2][3] + rows * (46 + 14) - 14
    assert abs(lines[0][3] - (receipts.H - bottom)) <= 2          # the block is centred
    assert story_edit.reading_s({"text": "one two three four"}, 120) == 3.0
    words, n = story_edit.read_words("One two. Three!", 5.0, 3.0, 7)
    assert [w["seg"] for w in words] == [7, 7, 8] and n == 9
    assert words[0]["start"] == 5.0 and words[-1]["end"] == 8.0

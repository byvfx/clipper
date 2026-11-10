import re

import pytest
from test_story import FakeLLM as StoryLLM
from test_story import cfg, dossier_answer, transcribed  # noqa: F401  (cfg is a fixture)

from clipper import archive, archive_index, jsonstate, story, story_dig, swarm
from clipper.errors import ClipperError


def thread(name, links, queries=("marcus",), kind="person"):
    return {"name": name, "kind": kind, "what": f"{name} matters", "links": list(links), "queries": list(queries),
            "search": name}


class FakeLLM(StoryLLM):
    """The dig's calls by schema; the story's as test_story's."""

    def __init__(self, threads=(), dossiers=()):
        super().__init__(dossiers)
        self.threads = list(threads)

    def json_call(self, model, system, user, schema, effort):
        if schema is story_dig.THREADS_SCHEMA:
            self.users.setdefault(id(schema), []).append(user)
            return {"threads": self.threads.pop(0) if self.threads else []}, {}
        if schema is story_dig.JUDGE_SCHEMA:
            self.users.setdefault(id(schema), []).append(user)
            out = []
            for n, title in re.findall(r"^\[(\d+)\] [^|]*\|[^|]*\|[^|]*\| (.*)$", user, re.MULTILINE):
                label = "side" if "fight" in title else "hint" if "what happened" in title else None
                if label:
                    out.append({"n": int(n), "label": label, "thread": "T01", "why": f"{label}: Marcus"})
            return {"labels": out}, {}
        if schema is story_dig.MOMENTS_SCHEMA:
            self.users.setdefault(id(schema), []).append(user)
            blocks = {text.split()[0]: int(k) for k, text in re.findall(r"\[(\d+)\] [^\n]*\n([^\n]*)", user)}
            picks = []
            if "back" in blocks:
                picks += [{"n": blocks["back"], "thread": "T01", "label": "side", "role": "context", "fit": "strong",
                           "quote": "marcus said jer owes him money", "why": "the old fight"},
                          {"n": blocks["back"], "thread": "T01", "label": "hint", "role": "context", "fit": "maybe",
                           "quote": "marcus will sue him next week", "why": "made up"}]
            return {"picks": picks}, {}
        if schema is story.DOSSIER_DIG_SCHEMA:
            self.users.setdefault(id(schema), []).append(user)
            return self.dossiers.pop(0), {}
        return super().json_call(model, system, user, schema, effort)


def dig_dossier(cites):
    return {**dossier_answer(["M01"]), "angles": [{"angle": "the ban", "moments": ["M01"]}], "side_stories": [
        {"what": "Marcus and Jer fought over money in September.", "label": "side", "link": "Marcus", "cites": cites}]}


FOUND = [
    {"key": "yt:bbbbbbbbbbb", "source": "youtube", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
     "title": "Marcus and Jer fight", "channel": "Clips", "date": "2026-09-15", "duration_s": 600,
     "relevance": "strong", "why": "the old fight", "found_by": ["t001", "t002"]},
    {"key": "reddit.com/r/x/comments/1", "source": "reddit", "url": "https://www.reddit.com/r/x/comments/1",
     "title": "what happened with Marcus?", "channel": "r/x", "date": "2026-10-30", "relevance": "maybe",
     "why": "asks", "found_by": ["t003"]},
    {"key": "example.com/cars", "source": "web", "url": "https://example.com/cars", "title": "Marcus cars for sale",
     "relevance": "maybe", "why": "name", "found_by": ["t001"]},
    {"key": "yt:known111111", "source": "youtube", "url": "https://www.youtube.com/watch?v=known111111",
     "title": "Jer banned", "relevance": "strong", "why": "x", "found_by": ["t001"]},
    {"key": "archive jer:1:0", "source": "archive jer", "url": "", "title": "old stream", "relevance": "maybe",
     "why": "x", "found_by": ["t004"]},
]


@pytest.fixture
def dug(cfg, tmp_path, monkeypatch):  # noqa: F811
    """A story after `story run`: one transcribed video, M01, a dossier; the swarm and Modal faked."""
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    d = story.story_dir(cfg, slug)
    transcribed(d / "yt-aaaaaaaaaaa", "today I got banned from kick for the FDA call and Marcus knows why")
    archive.save_catalog(d, {"yt-aaaaaaaaaaa": {"id": "yt-aaaaaaaaaaa", "source": "youtube", "title": "Banned",
                                                "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                                                "date": "2026-11-01", "key": "yt:aaaaaaaaaaa"}})
    archive_index.build(cfg, d.name, rebuild=False)
    jsonstate.write(d / "swarm.json", {"runs": [{}], "findings": [
        {**FOUND[3], "date": "2026-11-01"}]})
    jsonstate.write(d / "moments.json", {"moments": [
        {"id": "M01", "subject": d.name, "item": "yt-aaaaaaaaaaa", "source": "youtube", "channel": "Jer",
         "date": "2026-11-01", "title": "Banned", "url": "u", "t": 101.0, "quote": "I got banned from kick",
         "role": "event", "fit": "strong", "why": "his words", "link": "l", "ours": False}]})
    jsonstate.write(d / "dossier.json", {"dossier": {**dig_dossier([]), "gaps": ["who Marcus is"]}})
    (d / "dossier.md").write_text("old dossier", encoding="utf-8")
    calls = []

    def fake_swarm(cfg, goal, trail_path, **kw):
        calls.append({"goal": goal, "trail": trail_path, **kw})
        return {"findings": [dict(f) for f in FOUND], "stopped": "", "usd": 0, "tasks": 3}

    def fake_transcribe(cfg, d, subject, todo, gpu, parallel, retry_on):
        for it in todo:
            transcribed(d / it["id"], "back in september marcus said jer owes him money for the fda thing")

    monkeypatch.setattr(swarm, "run", fake_swarm)
    monkeypatch.setattr(archive, "_transcribe_all", fake_transcribe)
    return slug, d, calls


def test_a_dig_round_from_threads_to_dossier(cfg, dug, capsys):  # noqa: F811
    slug, d, calls = dug
    llm = FakeLLM(threads=[[thread("Marcus", ["M01", "G01"]), thread("Nobody", ["M99"]),
                            thread("marcus", ["G01"])]],
                  dossiers=[dig_dossier(["D01", "S02"])])
    story_dig.run(cfg, slug, rounds=1, claude=llm)
    dig = jsonstate.load(d / "dig.json")

    # threads: what the model saw, what it made, and the one linking nothing kept as rejected
    seen = llm.users[id(story_dig.THREADS_SCHEMA)][0]
    assert "M01 | event" in seen and "G01 | gap | who Marcus is" in seen
    assert [(t["id"], t["name"], t["status"], t["depth"]) for t in dig["threads"]] == [
        ("T01", "Marcus", "searched", 1), ("T02", "Nobody", "rejected", 1)]
    assert dig["read"] == ["G01", "M01"]

    # search: the story's window back lookback_days; the story's own finding and archive hits aren't candidates
    assert calls[0]["since"] == "2026-07-30" and calls[0]["trail"] == d / "dig" / "T01.swarm.json"
    assert {s["kind"] for s in calls[0]["seeds"]} == {"web_search", "reddit_search", "youtube_search"}
    assert dig["subjects"] == ["jer"]
    cands = {c["key"]: c for c in dig["candidates"]}
    assert set(cands) == {"yt:bbbbbbbbbbb", "reddit.com/r/x/comments/1", "example.com/cars"}

    # judge: every candidate labelled (noise too, kept), scored, the video transcribed
    assert [cands[k]["label"] for k in cands] == ["side", "hint", "noise"]
    assert cands["example.com/cars"]["label_why"] == "the judge passed over it"
    assert cands["yt:bbbbbbbbbbb"]["item"] == "yt-bbbbbbbbbbb"
    assert cands["yt:bbbbbbbbbbb"]["score"] == 2.75   # side, strong, found twice, before the window
    assert cands["reddit.com/r/x/comments/1"]["score"] == 1.5   # hint, in the window
    assert "yt-bbbbbbbbbbb" in archive.load_catalog(d)

    # find: the threads' queries over the new transcript; a window at M01 isn't read again
    windows = llm.users[id(story_dig.MOMENTS_SCHEMA)][0]
    assert "back in september" in windows and "today I got banned" not in windows
    assert [(m["id"], m["label"], m["thread"], m["quote"]) for m in dig["moments"]] == [
        ("D01", "side", "T01", "marcus said jer owes him money")]
    assert dig["unverified"][0]["quote"] == "marcus will sue him next week"
    assert dig["rounds"][0]["step"] == "done" and dig["rounds"][0]["windows"][0]["picked"]

    # the dossier: the dig in it, the old one kept beside it
    user = llm.users[id(story.DOSSIER_DIG_SCHEMA)][0]
    assert "D01 | context, strong, dig side via T01" in user
    assert "S02 | video (transcribed), dig side via T01" in user and "S03 | reddit, dig hint via T01" in user
    assert "example.com/cars" not in user and "<threads>\nT01 | person | Marcus" in user
    assert (d / "dossier.before_dig.md").read_text(encoding="utf-8") == "old dossier"
    md = (d / "dossier.md").read_text(encoding="utf-8")
    assert "## Side stories and hints\n\n- [side] Marcus and Jer fought over money in September. Link: Marcus (D01, S02)" in md
    assert "- D01 [context, strong, dig side via T01]" in md

    g = jsonstate.load(d / "graph.json")
    assert {(e["from"], e["to"], e["kind"]) for e in g["edges"]} >= {
        ("M01", "brief", "event"), ("T01", "M01", "mentioned in"), ("D01", "T01", "side"),
        ("yt:bbbbbbbbbbb", "T01", "side")}
    assert "example.com/cars" not in {n["id"] for n in g["nodes"]}   # noise: in dig.json, not the graph

    # the next round reads only what is new (D01); nothing new to follow ends it
    llm = FakeLLM(threads=[[]])
    story_dig.run(cfg, slug, rounds=1, claude=llm)
    assert "D01 | context" in llm.users[id(story_dig.THREADS_SCHEMA)][0]
    assert "M01 |" not in llm.users[id(story_dig.THREADS_SCHEMA)][0]
    dig = jsonstate.load(d / "dig.json")
    assert dig["rounds"][1]["stopped"] == "nothing new to follow"
    assert "nothing new to follow" in capsys.readouterr().out
    story_dig.show(cfg, slug)
    out = capsys.readouterr().out
    assert "T02 [person, rejected, depth 1] Nobody" in out and "noise (1):" in out


def test_a_dry_run_stops_before_transcribing_and_the_next_run_goes_on(cfg, dug, capsys):  # noqa: F811
    slug, d, _ = dug
    story_dig.run(cfg, slug, rounds=1, dry_run=True, claude=FakeLLM(threads=[[thread("Marcus", ["M01"])]]))
    dig = jsonstate.load(d / "dig.json")
    assert dig["rounds"][0]["step"] == "ingest" and not (d / "yt-bbbbbbbbbbb").exists()
    assert "dry run: stopping before ingest" in capsys.readouterr().out
    assert not (d / "dossier.before_dig.md").exists()
    llm = FakeLLM(dossiers=[dig_dossier(["D01"])])
    story_dig.run(cfg, slug, rounds=0, claude=llm)    # finishes the open round, starts none
    dig = jsonstate.load(d / "dig.json")
    assert len(dig["rounds"]) == 1 and dig["rounds"][0]["step"] == "done" and dig["moments"]
    assert id(story_dig.THREADS_SCHEMA) not in llm.users


def test_a_dig_transcribes_only_its_own_picks(cfg, dug, capsys):  # noqa: F811
    slug, d, _ = dug
    cat = archive.load_catalog(d)
    cat["yt-ccccccccccc"] = {"id": "yt-ccccccccccc", "source": "youtube", "url": "u", "duration_s": 600}  # first pass's
    archive.save_catalog(d, cat)
    story_dig.run(cfg, slug, rounds=1, claude=FakeLLM(threads=[[thread("Marcus", ["M01"])]],
                                                       dossiers=[dig_dossier(["D01"])]))
    assert (d / "yt-bbbbbbbbbbb" / "words.jsonl").exists() and not (d / "yt-ccccccccccc").exists()
    out = capsys.readouterr().out
    assert "to transcribe: 1 items" in out and "not transcribing 1 first-pass pick(s) still waiting (yt-ccccccccccc)" in out


def test_a_failed_video_is_marked_and_the_dig_goes_on(cfg, dug, monkeypatch):  # noqa: F811
    slug, d, _ = dug

    def fails(cfg, d, subject, todo, gpu, parallel, retry_on):
        for it in todo:
            (d / it["id"]).mkdir(parents=True, exist_ok=True)
            (d / it["id"] / "error.txt").write_text("RuntimeError: gone", encoding="utf-8")

    monkeypatch.setattr(archive, "_transcribe_all", fails)
    story_dig.run(cfg, slug, rounds=1, claude=FakeLLM(threads=[[thread("Marcus", ["M01"])]],
                                                       dossiers=[dig_dossier(["S02"])]))
    c = next(c for c in jsonstate.load(d / "dig.json")["candidates"] if c["key"] == "yt:bbbbbbbbbbb")
    assert c["failed"] == "RuntimeError: gone"
    assert "yt-bbbbbbbbbbb" not in archive.load_catalog(d)   # `story run` isn't left waiting on it


def test_threads_keep_close_to_the_story():
    dig = {"threads": [], "moments": []}
    t1 = story_dig.merge_thread(dig, thread("Marcus", ["M01"]), {"M01"}, 2, 1)
    assert (t1["depth"], t1["status"]) == (1, "open")
    dig["moments"].append({"id": "D01", "thread": "T01"})
    t2 = story_dig.merge_thread(dig, thread("the money", ["D01"]), {"M01", "D01"}, 2, 2)
    assert (t2["depth"], t2["status"]) == (2, "open")
    dig["moments"].append({"id": "D02", "thread": "T02"})
    t3 = story_dig.merge_thread(dig, thread("his lawyer", ["D02"]), {"D02"}, 2, 3)
    assert (t3["depth"], t3["status"]) == (3, "too deep")
    # mentioned by the first pass too: as close as its closest mention
    assert story_dig.merge_thread(dig, thread("Kick", ["D02", "M01"]), {"M01", "D02"}, 2, 3)["depth"] == 1
    assert story_dig.merge_thread(dig, thread("the  Money's", ["M01"]), {"M01"}, 2, 3) is None   # same thread
    assert dig["threads"][1]["links"] == ["D01", "M01"]


def test_flags_choose_the_threads_and_win_the_score():
    dig = {"threads": [
        {"id": "T01", "status": "open", "depth": 2, "flag": ""},
        {"id": "T02", "status": "too deep", "depth": 3, "flag": "follow"},
        {"id": "T03", "status": "open", "depth": 1, "flag": ""},
        {"id": "T04", "status": "open", "depth": 1, "flag": "drop"},
        {"id": "T05", "status": "searched", "depth": 1, "flag": "follow"}]}
    assert [t["id"] for t in story_dig.next_threads(dig, 2)] == ["T02", "T03"]
    c = {"label": "noise", "relevance": "strong", "date": "2026-11-01"}
    assert story_dig.score(c, "2026-10-28", "") == 0
    assert story_dig.score({**c, "flag": "follow"}, "2026-10-28", "") == 10
    assert story_dig.score({**c, "label": "core", "flag": "drop"}, "2026-10-28", "") == 0
    assert story_dig.score({**c, "label": "core"}, "2026-10-28", "2026-10-31") == 3.5   # after the window


def test_dig_settings_merge_and_check(cfg):  # noqa: F811
    cfg["story"]["dig"] = {"rounds": 5, "swarm": {"max_tasks": 4}}
    s = story_dig.settings(cfg)
    assert s["rounds"] == 5 and s["max_threads"] == 4 and s["swarm"]["max_tasks"] == 4 and s["swarm"]["first_tasks"] == 4
    cfg["story"]["dig"] = {"roundz": 1}
    with pytest.raises(ClipperError, match="roundz"):
        story_dig.settings(cfg)


def test_no_dig_before_the_story_has_moments(cfg):  # noqa: F811
    slug = story.new(cfg, "Jer banned from Kick", since="2026-10-28")
    with pytest.raises(ClipperError, match="story run"):
        story_dig.run(cfg, slug, claude=FakeLLM())


def test_a_web_results_snippets_are_one_text():
    from clipper import investigate

    x = {"description": "<strong>He clearly can play</strong> the bass &amp; chose not to",
         "extra_snippets": ["Posted by u/a - 149 votes and 200 comments",
                            "He clearly can play the bass & chose not to", "Not Schneider, it was his friend."]}
    assert investigate.snippet_text(x) == ("He clearly can play the bass & chose not to … Posted by u/a - 149 votes "
                                           "and 200 comments … Not Schneider, it was his friend.")
    assert investigate.snippet_text({"description": "x" * 2000}) == "x" * investigate.SNIPPET_CHARS
    assert investigate.snippet_text({}) == ""

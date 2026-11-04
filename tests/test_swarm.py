import re
import time

import pytest
from conftest import ROOT

from clipper import jsonstate, swarm
from clipper.errors import ClipperError
from clipper.kick import RateLimiter


@pytest.fixture
def cfg(defaults):
    defaults["paths"]["prompts"] = ROOT / "prompts"
    defaults["swarm"].update(workers=1, rates=dict.fromkeys(defaults["swarm"]["rates"], 0))  # no waits
    return defaults


def vid(n):
    return {"source": "youtube", "title": f"video {n}", "channel": "Clips", "date": "2026-11-01",
            "url": f"https://www.youtube.com/watch?v={n:0>11}"}


class FakeLLM:
    """The planner's tasks, then each worker's answer from `answer(kind, target)`."""

    def __init__(self, plan, answer, usd=0.0):
        self.plan, self.answer, self.usd, self.cost, self.calls = plan, answer, usd, 0.0, []

    def json_call(self, model, system, user, schema, effort):
        self.cost += self.usd
        if schema is swarm.PLAN_SCHEMA:
            self.calls.append(("plan", ""))
            return {"tasks": self.plan}, {}
        kind, target = re.search(r"This search: (\w+) '([^']*)'", user).groups()
        self.calls.append((kind, target))
        return self.answer(kind, target), {}


def run(cfg, tmp_path, llm, tools, **kw):
    return swarm.run(cfg, "Jer's MRI grift", tmp_path / "swarm.json", claude=llm, tools=tools,
                     archives=["jer"], log=lambda s: None, **kw)


def test_targets_must_be_their_kinds():
    t = swarm.target_of
    assert t("youtube_channel", "@TheQuartering", []) == "https://www.youtube.com/@TheQuartering"
    assert t("youtube_channel", "https://www.youtube.com/@TheQuartering/videos", []) == \
        "https://www.youtube.com/@TheQuartering"
    assert t("youtube_channel", "https://www.youtube.com/watch?v=abcdefghijk", []) is None  # a video, not a channel
    assert t("youtube_channel", "https://evil.example/@x", []) is None
    assert t("kick_videos", "https://kick.com/KinoCasinoGaming", []) == "kinocasinogaming"
    assert t("kick_videos", "kino casino", []) is None
    assert t("odysee_channel", "https://odysee.com/@TheQuartering:a", []) == "@TheQuartering"
    assert t("archive_search", "jer: huck mri", ["jer"]) == "jer: huck mri"
    assert t("archive_search", "dsp: huck mri", ["jer"]) is None  # no such archive on file
    assert t("youtube_search", "  Jer   huck  MRI ", []) == "Jer huck MRI"
    assert t("youtube_search", "https://www.youtube.com/watch?v=abcdefghijk", []) is None
    assert t("nonsense", "x", []) is None


def test_private_details_are_never_searched():
    assert swarm.target_of("youtube_search", "jer home address", []) is None
    assert swarm.target_of("web_search", "where does jer live", []) is None
    assert swarm.target_of("reddit_search", "Jer's kids school", []) is None
    for q in ("where is jer right now", "jer residential address", "jer current location", "where is jer staying",
              "jer lives in which town"):
        assert swarm.target_of("web_search", q, []) is None, q
    assert swarm.target_of("youtube_search", "jer addresses the lawsuit", []) == "jer addresses the lawsuit"
    assert swarm.target_of("youtube_search", "where is jer going with this", []) == "where is jer going with this"


def test_one_video_is_one_finding_however_it_was_found():
    k = swarm.finding_key
    assert k({"url": "https://www.youtube.com/watch?v=abcdefghijk&t=30s"}) == k({"url": "https://youtu.be/abcdefghijk"})
    assert k({"url": "https://www.youtube.com/shorts/abcdefghijk"}) == "yt:abcdefghijk"
    a = {"source": "archive jer", "item": "yt-x", "t": 10.0, "url": "https://www.youtube.com/watch?v=abcdefghijk"}
    assert k(a) != k({**a, "t": 300.0}) != k({"url": a["url"]})   # two moments of one video are two findings
    assert k({"url": "https://www.Reddit.com/r/x/comments/1/t/?utm_source=share&context=3"}) == "reddit.com/r/x/comments/1/t"
    assert k({"url": "https://forum.example/viewtopic.php?t=123"}) != k({"url": "https://forum.example/viewtopic.php?t=456"})
    assert k({"url": "https://example.com/Story"}) != k({"url": "https://example.com/story"})   # paths keep their case
    assert k({"url": "kick-135553454 s08"}) == "kick-135553454 s08"   # one of our segments


def test_findings_merge_and_follow_ups_run_as_the_next_round(cfg, tmp_path):
    plan = [{"kind": "youtube_search", "target": "jer huck mri", "why": "clips"},
            {"kind": "kick_videos", "target": "jer", "why": "nonsense name"},  # fine: a Kick name
            {"kind": "youtube_channel", "target": "not a channel", "why": "dropped"}]

    def answer(kind, target):
        if target == "jer huck mri":
            return {"findings": [{"n": 0, "relevance": "maybe", "why": "a clip"}, {"n": 9, "relevance": "strong", "why": "x"}],
                    "followups": [{"kind": "youtube_channel", "target": "@ClipsChannel", "why": "keeps covering it"},
                                  {"kind": "youtube_search", "target": "JER  huck mri", "why": "same search again"},
                                  {"kind": "web_search", "target": "https://evil.example", "why": "a url"}]}
        return {"findings": [{"n": 0, "relevance": "strong", "why": "the original"}], "followups": []}

    tools = {"youtube_search": lambda q, n: [vid(1)], "youtube_channel": lambda u, n: [vid(1), vid(2)],
             "kick_videos": lambda ch, n: []}
    out = run(cfg, tmp_path, FakeLLM(plan, answer), tools)
    assert out["stopped"] == "no follow-ups left" and out["tasks"] == 3
    trail = jsonstate.load(tmp_path / "swarm.json")
    t1, t2, t3 = trail["tasks"]
    assert [t1["kind"], t2["kind"], t3["kind"]] == ["youtube_search", "kick_videos", "youtube_channel"]
    assert trail["planner_dropped"][0]["target"] == "not a channel"
    assert t3["parent"] == t1["id"] and t3["round"] == 1 and t1["followups"] == [t3["id"]]
    assert [d["target"] for d in t1["dropped"]] == ["https://evil.example"]  # the repeat is just not queued
    assert t1["findings"] == [{"n": 0, "relevance": "maybe", "why": "a clip"}]  # n 9 isn't a result
    (best,) = [f for f in out["findings"] if f["key"] == "yt:00000000001"]
    assert best["relevance"] == "strong" and best["found_by"] == [t1["id"], t3["id"]]
    assert out["findings"][0] is not None and len(out["findings"]) == 1   # vid 2 wasn't picked


def test_each_limit_stops_the_run(cfg, tmp_path):
    def forever(kind, target):  # every search finds a new one to follow
        n = int(target.split()[-1])
        return {"findings": [], "followups": [{"kind": "youtube_search", "target": f"jer {n + 1}", "why": "more"}]}

    tools = {"youtube_search": lambda q, n: [vid(int(q.split()[-1]))]}
    plan = [{"kind": "youtube_search", "target": "jer 0", "why": "start"}]
    cfg["swarm"].update(max_rounds=2)
    assert run(cfg, tmp_path / "a", FakeLLM(plan, forever), tools)["stopped"] == "max_rounds 2"
    cfg["swarm"].update(max_rounds=9, max_tasks=3)
    out = run(cfg, tmp_path / "b", FakeLLM(plan, forever), tools)
    assert out["stopped"] == "max_tasks 3" and out["tasks"] == 3
    cfg["swarm"].update(max_tasks=60, max_wall_s=-1)
    assert run(cfg, tmp_path / "c", FakeLLM(plan, forever), tools)["stopped"].startswith("wall time")
    cfg["swarm"].update(max_wall_s=600, budget_usd=0.025)
    out = run(cfg, tmp_path / "d", FakeLLM(plan, forever, usd=0.01), tools)
    assert out["stopped"].startswith("budget") and out["tasks"] == 2  # $0.01 a call: planner + 2 workers fit
    pending = [t for t in jsonstate.load(tmp_path / "d" / "swarm.json")["tasks"] if t["status"] == "pending"]
    assert len(pending) == 1   # the follow-up waits for a rerun


def test_a_rerun_resumes_the_trail(cfg, tmp_path):
    calls = []

    def search(q, n):
        calls.append(q)
        if q == "jer broken" and calls.count(q) == 1:
            raise RuntimeError("HTTP 429")
        return [vid(len(calls))]

    plan = [{"kind": "youtube_search", "target": t, "why": "w"} for t in ("jer a", "jer broken", "jer c")]
    none = lambda kind, target: {"findings": [{"n": 0, "relevance": "strong", "why": "w"}], "followups": []}
    cfg["swarm"].update(max_tasks=2)
    out = run(cfg, tmp_path, FakeLLM(plan, none), {"youtube_search": search})
    assert out["stopped"] == "max_tasks 2" and calls == ["jer a", "jer broken"]
    statuses = [t["status"] for t in jsonstate.load(tmp_path / "swarm.json")["tasks"]]
    assert statuses == ["done", "failed", "pending"]
    llm = FakeLLM(plan, none)
    out = run(cfg, tmp_path, llm, {"youtube_search": search})
    assert calls[2:] == ["jer broken", "jer c"] and ("plan", "") not in llm.calls   # no new plan, done kept
    assert out["stopped"] == "no follow-ups left" and len(out["findings"]) == 3


def test_seeds_and_known_findings(cfg, tmp_path):
    seeds = [{"kind": "kick_videos", "target": "kick.com/jer", "why": "his own"},
             {"kind": "youtube_search", "target": "jer huck", "why": "planner has it too"}]
    plan = [{"kind": "youtube_search", "target": "Jer Huck", "why": "dup of a seed"}]
    llm = FakeLLM(plan, lambda k, t: {"findings": [], "followups": []})
    known = [{"source": "youtube", "title": "given", "url": "https://youtu.be/abcdefghijk"}]
    out = swarm.run(cfg, "goal", tmp_path / "s.json", claude=llm, tools={"kick_videos": lambda c, n: [vid(1)],
                    "youtube_search": lambda q, n: [vid(2)]}, archives=[], seeds=seeds, known=known, log=lambda s: None)
    trail = jsonstate.load(tmp_path / "s.json")
    assert ("plan", "") in llm.calls and trail["planned"]   # seeds run beside the plan, not instead of it
    assert [t["target"] for t in trail["tasks"]] == ["jer", "jer huck"]   # the planner's repeat isn't queued
    assert out["findings"][0]["found_by"] == ["given"]


def test_settings_merge_rates_and_refuse_unknown_keys(defaults):
    defaults["swarm"]["rates"] = {"reddit": 0.2}   # clipper.toml naming one source keeps the others
    s = swarm.settings(defaults, {"budget_usd": 0.2, "rates": {"youtube": 0.1}})
    assert s["budget_usd"] == 0.2 and s["rates"] == {**s["rates"], "youtube": 0.1, "reddit": 0.2, "kick": 2.0}
    with pytest.raises(ClipperError, match="unknown swarm setting"):
        swarm.settings(defaults, {"budgte_usd": 1})


def test_a_source_rate_spaces_its_requests():
    lim = RateLimiter(20.0)
    t0 = time.monotonic()
    for _ in range(3):
        lim.wait()
    assert time.monotonic() - t0 >= 0.09


def test_a_busy_channel_is_cut_to_the_dates_and_the_goals_words():
    rs = [{**vid(k), "title": t, "date": d} for k, (t, d) in enumerate(
        [("Midterm chaos", "2026-11-03"), ("Huck MRI update", "2026-10-20"), ("Coffee for Huck", "2026-10-12"),
         ("Untitled stream", ""), ("Huck in 2025", "2025-06-01")])]
    kept = swarm.in_window(rs, "2026-10-01", "2026-11-01")
    assert [r["title"] for r in kept] == ["Huck MRI update", "Coffee for Huck", "Untitled stream"]  # undated stays
    assert [r["title"] for r in swarm.focus(rs, ["huck", "mri"], 2)] == ["Huck MRI update", "Coffee for Huck"]
    assert swarm.focus(rs, ["huck"], 9) is rs


def test_every_request_waits_on_its_source_and_searches_keep_the_dates(cfg, monkeypatch):
    from clipper import archive

    waits = []

    class Lim:
        def __init__(self, src):
            self.src = src

        def wait(self):
            waits.append(self.src)

    def flat(url, n, pace):
        pace()
        return [{**vid(1), "date": "2026-10-30"}, {**vid(2), "date": "2026-09-01"}]

    def odysee(method, params):
        if method == "resolve":
            return {"lbry://@jer": {"claim_id": "c" * 40, "value": {"title": "Jer"}}}
        return {"items": [{"claim_id": f"{params['page']:040d}", "name": "v", "timestamp": 1793750400, "value": {}}],
                "total_pages": 3}

    monkeypatch.setattr(swarm, "flat_youtube", flat)
    monkeypatch.setattr(archive, "odysee", odysee)
    tools = swarm.default_tools(cfg, days=7, since="2026-10-01", limits={s: Lim(s) for s in ("youtube", "odysee")})
    assert [r["date"] for r in tools["youtube_search"]("jer huck", 20)] == ["2026-10-30"]   # since, not just "this week"
    tools["youtube_channel"]("https://www.youtube.com/@x", 20)
    tools["odysee_channel"]("@jer", 20)
    assert waits == ["youtube"] * 3 + ["odysee"] * 4   # search, both channel tabs; resolve + 3 pages


def test_the_worker_sees_each_results_link_and_channel():
    text = swarm.listing([{**vid(1), "channel_url": "https://www.youtube.com/channel/UCabc"}])
    assert "https://www.youtube.com/watch?v=00000000001 | channel https://www.youtube.com/channel/UCabc" in text


def test_youtube_window_filters():
    assert swarm.yt_filter(0) == "" and swarm.yt_filter(7) == "EgQIAxAB"
    assert swarm.yt_filter(30) == "EgQIBBAB" and swarm.yt_filter(2000) == ""


def test_the_trail_prints_as_a_tree(cfg, tmp_path):
    plan = [{"kind": "youtube_search", "target": "jer huck", "why": "clips"}]

    def answer(kind, target):
        follow = [{"kind": "youtube_channel", "target": "@Clips", "why": "his channel"}] if kind == "youtube_search" else []
        return {"findings": [{"n": 0, "relevance": "strong", "why": "w"}], "followups": follow}

    run(cfg, tmp_path, FakeLLM(plan, answer), {"youtube_search": lambda q, n: [vid(1)],
                                               "youtube_channel": lambda u, n: [vid(2)]})
    lines = []
    swarm.print_trail(tmp_path / "swarm.json", lines.append)
    text = "\n".join(lines)
    assert "  t001 youtube_search 'jer huck'" in text and "    t002 youtube_channel" in text
    assert text.index("video 1") < text.index("t002") < text.index("video 2")

import json
import sqlite3
import tomllib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clipper import config, jsonstate, news, warroom
from clipper.errors import ClipperError

TOML = '''
[paths]
news = "news"
streams = "streams"

[news]
auto_watch = true

[shows.kino]
channels = ["kinocasino"]
[shows.kino.meta]
show_name = "Kino Casino"
cast = ["Andy", "PPP"]
[shows.kino.scoring]
known_figures = "Jer (The Quartering), Ethan Ralph, Rich (review tech), the detractors"

[shows.c404]
channels = ["channel404"]
[shows.c404.meta]
show_name = "Channel 404"
cast = ["Don"]
'''


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # paths resolve beside the config
    (tmp_path / "clipper.toml").write_text(TOML, encoding="utf-8")
    c = config.load(tmp_path / "clipper.toml", use_local=False)
    (tmp_path / "streams").mkdir()
    return c


def rss(*items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                   f"<pubDate>{p}</pubDate><source url='x'>{s}</source></item>" for t, u, d, p, s in items)
    return f"<?xml version='1.0'?><rss><channel><title>x</title>{body}</channel></rss>".encode()


def recent(hours=1):
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%a, %d %b %Y %H:%M:%S GMT")


ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Jer banned on Rumble?</title><link href="https://www.reddit.com/r/LivestreamFail/comments/abc/"/>
<id>t3_abc</id><updated>2026-11-02T10:00:00+00:00</updated>
<content type="html">&lt;p&gt;submitted by &lt;b&gt;someone&lt;/b&gt;&lt;/p&gt;</content></entry></feed>"""


def test_parse_figures_keeps_aliases_and_commas_inside_brackets():
    got = news.parse_figures("Jer (The Quartering, Jeremy), Ethan Ralph, the detractors")
    assert got == [("Jer", ["The Quartering", "Jeremy"]), ("Ethan Ralph", []), ("the detractors", [])]


def test_parse_rss_and_atom():
    items = news.parse_feed(rss(("Ethan Ralph &amp; Jer", "https://a.com/1", "&lt;b&gt;Big&lt;/b&gt; news",
                                 "Mon, 02 Nov 2026 10:00:00 GMT", "TMZ")), "Feed")
    assert items == [{"url": "https://a.com/1", "title": "Ethan Ralph & Jer", "snippet": "Big news",
                      "published": "2026-11-02T10:00:00+00:00", "source": "TMZ"}]
    a = news.parse_feed(ATOM, "r/LivestreamFail")
    assert a[0]["url"].startswith("https://www.reddit.com/") and a[0]["snippet"] == "submitted by someone"
    assert a[0]["source"] == "r/LivestreamFail" and a[0]["published"] == "2026-11-02T10:00:00+00:00"


def test_only_the_terms_are_watched_and_none_means_no_fetching(cfg, monkeypatch):
    cfg["news"]["auto_watch"] = False
    cfg["_root"] = None  # the test config is the root
    monkeypatch.setattr(news, "fetch", lambda *a, **k: pytest.fail("fetched with no terms"))
    assert news.watchlist(cfg) == {}
    assert news.check(cfg, judge=False, say=lambda m: None) == []
    cfg["news"]["watch"] = {"The Quartering": ["Jeremy Hambly"]}
    cfg["shows"]["kino"]["overrides"]["news"] = {"watch": {"Ethan Ralph": []}}
    w = news.watchlist(cfg)
    assert set(w) == {"the quartering", "ethan ralph"} and w["ethan ralph"].shows == ["kino"]
    assert w["the quartering"].shows == [] and w["the quartering"].search


def test_a_local_or_env_watch_clears_a_shows_terms(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clipper.toml").write_text('[shows.kino]\nchannels = ["kinocasino"]\n'
                                          '[shows.kino.news]\nwatch = { "Ethan Ralph" = [] }\n', encoding="utf-8")
    w = news.watchlist(config.load(tmp_path / "clipper.toml", use_local=False))
    assert set(w) == {"ethan ralph"} and w["ethan ralph"].shows == ["kino"]
    monkeypatch.setenv("CLIPPER_NEWS_WATCH", "{}")
    assert news.watchlist(config.load(tmp_path / "clipper.toml", use_local=False)) == {}


def test_watchlist_and_matching(cfg):
    cfg["news"]["watch"] = {"Ethan Klein": ["H3 Podcast"]}
    cfg["news"]["ignore"] = ["PPP"]
    w = news.watchlist(cfg)
    assert set(w) == {"kino casino", "andy", "jer", "ethan ralph", "channel 404", "don", "ethan klein"}
    assert w["jer"].terms == ["Jer", "The Quartering"] and w["jer"].shows == ["kino"]
    assert w["jer"].search and w["ethan klein"].search and not w["andy"].search  # "Andy" alone is everyone
    m = news.matcher(w)
    assert m("JER and jerky") == [] and m("Jer sued") == ["Jer"]       # one word: as written
    assert m("the h3 podcast is back") == ["Ethan Klein"]              # longer: any case
    assert m("Donald and Don't") == [] and m("Don’s return") == ["Don"]
    srcs = news.sources(cfg, w)
    google = [u for n, u, _ in srcs if n.startswith("Google News")]
    assert any("%22The%20Quartering%22" in u for u in google) and not any("Andy" in u for u in google)
    assert ("r/LivestreamFail", news.REDDIT.format(sub="LivestreamFail"), 20) in srcs


class FakeJudge:
    cost = 0.0

    def __init__(self, verdicts):
        self.verdicts, self.calls = verdicts, []

    def json_call(self, model, system, user, schema, effort):
        self.calls.append(user)
        return {"items": self.verdicts.pop(0)}, {}


def test_check_dedupes_judges_and_groups_into_stories(cfg, monkeypatch):
    cfg["news"].update(feeds=[{"name": "TMZ", "url": "https://tmz.test/rss"}], reddit=[], google_news=False)
    bodies = [rss(("Jer sued by Rumble - TMZ", "https://tmz.test/1", "", recent(), "TMZ"),
                  ("Jer sued by Rumble", "https://other.test/1", "", recent(), "Other"),   # same headline
                  ("Andy Warhol retrospective", "https://tmz.test/2", "", recent(), "TMZ"),
                  ("Weather today", "https://tmz.test/3", "", recent(), "TMZ"),
                  ("Old Jer story", "https://tmz.test/4", "", recent(24 * 5), "TMZ"))]
    monkeypatch.setattr(news, "fetch", lambda url, etag=None, modified=None: (bodies.pop(0), "e1", ""))
    judge = FakeJudge([[
        {"i": 1, "person": "Jer", "show": "kino", "rating": "strong", "story": 0,
         "headline": "Rumble sues Jer", "why": "A lawsuit."},
        {"i": 2, "person": "", "show": "", "rating": "no", "story": 0, "headline": "", "why": "A namesake."}]])
    ids = news.check(cfg, claude=judge, say=lambda m: None)
    assert len(ids) == 1 and len(judge.calls) == 1
    assert "Jer sued by Rumble - TMZ" in judge.calls[0] and "Weather" not in judge.calls[0]
    s = news.stories(cfg, ids=ids)[0]
    assert (s["headline"], s["person"], s["show"], s["rating"]) == ("Rumble sues Jer", "Jer", "kino", "strong")
    assert [x["source"] for x in s["sources"]] == ["TMZ", "Other"]  # the copy joins, judged once; the old one never stored
    # Fetched just now: not again within min_fetch_minutes.
    assert news.check(cfg, claude=judge, say=lambda m: None) == ids

    # A later item on the same event joins the open story; a "maybe" waits when post_maybe is off.
    db = news.connect(cfg)
    news.store(db, [{"url": "https://x.test/9", "title": "Jer responds to Rumble", "snippet": "",
                     "published": news.iso(news.now()), "source": "X"},
                    {"url": "https://x.test/10", "title": "Don teases a return", "snippet": "",
                     "published": news.iso(news.now()), "source": "X"}], "X", 48)
    db.execute("UPDATE items SET matched = ? WHERE url = 'https://x.test/9'", (json.dumps(["Jer"]),))
    db.execute("UPDATE items SET matched = ? WHERE url = 'https://x.test/10'", (json.dumps(["Don"]),))
    db.commit()
    judge.verdicts.append([
        {"i": 1, "person": "Don", "show": "c404", "rating": "maybe", "story": 0, "headline": "Don return?", "why": "?"},
        {"i": 2, "person": "Jer", "show": "kino", "rating": "maybe", "story": ids[0], "headline": "", "why": "More."}])
    news.judge_items(cfg, db, news.watchlist(cfg), judge, say=lambda m: None)
    assert f"- {ids[0]}: Rumble sues Jer (Jer)" in judge.calls[-1]
    assert db.execute("SELECT COUNT(*) FROM items WHERE story = ?", (ids[0],)).fetchone()[0] == 3  # with the copy
    assert db.execute("SELECT rating FROM stories WHERE id = ?", (ids[0],)).fetchone()[0] == "strong"
    assert len(news.to_post(cfg, db)) == 2
    cfg["news"]["post_maybe"] = False
    assert news.to_post(cfg, db) == ids
    db.close()

    news.mark(cfg, ids[0], posted_at=news.iso(news.now()), message_id="1")
    news.dismiss(cfg, [ids[0]])
    assert news.unposted(cfg) == [] and [s["person"] for s in news.stories(cfg)] == ["Don"]
    assert len(news.stories(cfg, everything=True)) == 2
    with pytest.raises(ClipperError):
        news.mark(cfg, ids[0], headline="x")
    with pytest.raises(ClipperError, match="no story"):
        news.dismiss(cfg, [999])


def test_same_event_in_one_batch_is_one_story_and_posts_are_capped(cfg):
    db = news.connect(cfg)
    t = news.iso(news.now())
    rows = [{"url": f"https://x.test/{i}", "title": f"Piker sues Jennings {i} - Outlet {i}", "snippet": "",
             "published": t, "source": "X"} for i in range(4)]
    news.store(db, rows, "X", 48)
    db.execute("UPDATE items SET matched = ?", (json.dumps(["Hasan Piker"]),))
    batch = db.execute("SELECT * FROM items ORDER BY url").fetchall()
    v = {"person": "Hasan Piker", "show": "", "rating": "strong", "story": 0, "why": "A suit."}
    news.apply_verdicts(db, batch, {1: {**v, "same_as": 0, "headline": ""}, 2: {**v, "same_as": 1, "headline": ""},
                                    3: {**v, "same_as": 2, "headline": ""}, 4: {**v, "same_as": 0, "headline": "Other"}},
                        set(), {"kino"})
    db.commit()
    got = db.execute("SELECT headline, (SELECT COUNT(*) FROM items WHERE story = stories.id) FROM stories "
                     "ORDER BY id").fetchall()
    assert [tuple(r) for r in got] == [("Piker sues Jennings 0", 3), ("Other", 1)]   # " - Outlet" dropped
    cfg["news"]["max_posts"] = 1
    assert len(news.to_post(cfg, db)) == 1
    db.close()
    s = news.stories(cfg)[0]
    s["sources"] = [{"title": "T]" * 60, "url": "https://news.google.com/" + "a" * 600, "source": "G"}] * 9
    text = news.post_text(cfg, s)
    assert len(text) < 2000 and text.count("https://") == 2 and "and 7 more" in text


def test_feed_errors_are_kept_not_raised(cfg, monkeypatch):
    cfg["news"].update(feeds=[{"name": "Bad", "url": "https://bad.test/rss"}], reddit=[], google_news=False)
    monkeypatch.setattr(news, "fetch", lambda *a, **k: (b"<html>not a feed", "", ""))
    assert news.check(cfg, judge=False, say=lambda m: None) == []
    assert news.feeds(cfg)[0]["error"].startswith("ParseError")


def story(cfg, headline="Rumble sues Jer", person="Jer", show="kino", rating="strong"):
    db = news.connect(cfg)
    t = news.iso(news.now())
    sid = db.execute("INSERT INTO stories (headline, person, show, rating, why, first_at, last_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (headline, person, show, rating, "A lawsuit.", t, t)).lastrowid
    db.commit()
    db.close()
    return sid


def stream(cfg, sid="kick-1", channel="kinocasino", started=None, toml=""):
    d = cfg["paths"]["streams"] / sid
    d.mkdir()
    jsonstate.write(d / "meta.json", {"channel": channel, "started_at": news.iso(started or news.now())})
    if toml:
        (d / config.STREAM_CONFIG).write_text(toml, encoding="utf-8")
    return d


def test_context_waits_for_the_shows_next_stream(cfg):
    sid = story(cfg)
    e = news.set_context(cfg, [sid])
    assert e["show"] == "kino" and "Rumble sues Jer" in e["text"]
    assert news.stories(cfg, ids=[sid])[0]["context_at"]
    other = stream(cfg, "kick-0", channel="channel404")
    assert news.apply_pending(cfg, other, say=lambda m: None) == 0              # another show's
    d = stream(cfg, toml='[scoring]\nstream_context = "Halloween night."\n[meta]\nsrt_max_s = 3\n')
    assert news.apply_pending(cfg, d, say=lambda m: None) == 1
    data = tomllib.loads((d / config.STREAM_CONFIG).read_text(encoding="utf-8"))
    assert data["scoring"]["stream_context"] == "Halloween night.\nIn the news: Rumble sues Jer. A lawsuit."
    assert data["meta"]["srt_max_s"] == 3
    assert list(d.glob("config.toml.before_news_*"))
    assert "Rumble sues Jer" in config.for_stream(cfg, d)["scoring"]["stream_context"]
    assert news.apply_pending(cfg, d, say=lambda m: None) == 0                  # once
    with pytest.raises(ClipperError, match="no show"):
        news.set_context(cfg, [story(cfg, person="Someone", show="")])


def test_a_note_waits_for_the_next_stream_however_late(cfg, monkeypatch):
    news.set_context(cfg, [story(cfg)])
    old = stream(cfg, "kick-0", started=news.now() - timedelta(days=2))         # an older stream redone
    assert news.apply_pending(cfg, old, say=lambda m: None) == 0
    monkeypatch.setenv("CLIPPER_SCORING_STREAM_CONTEXT", "set by hand")         # would hide the note
    monday = stream(cfg, "kick-1", started=news.now() + timedelta(days=3))
    with pytest.raises(ClipperError, match="clipper.local.toml"):
        news.apply_pending(cfg, monday, say=lambda m: None)
    monkeypatch.delenv("CLIPPER_SCORING_STREAM_CONTEXT")
    assert news.apply_pending(cfg, monday, say=lambda m: None) == 1             # still queued, then taken
    news.set_context(cfg, [story(cfg, "Old news")])
    path = cfg["paths"]["news"] / "context.json"
    with jsonstate.update(path, {}) as data:
        data["pending"][-1]["at"] = news.iso(news.now() - timedelta(days=8))
    assert news.apply_pending(cfg, stream(cfg, "kick-2"), say=lambda m: None) == 0
    assert jsonstate.read(path)["pending"][-1]["used"] == "expired"


def test_context_block_previews_exactly_what_is_queued(cfg):
    ids = [story(cfg, f"Story {i} " + "x" * 300) for i in range(6)]
    found = news.stories(cfg, ids=ids)
    text, used = news.context_block(found)
    assert len(text) <= news.CONTEXT_MAX and 0 < len(used) < 6
    e = news.set_context(cfg, ids)
    assert (e["text"], e["stories"]) == (text, used)


def test_toml_writer_round_trips(tmp_path):
    data = {"scoring": {"stream_context": 'He said "hi"\nand left \\ ok', "max_picks": 3, "x": 0.5, "on": True},
            "meta": {"tags": ["a", "b"], "t": {"k": "v"}}}
    p = tmp_path / "c.toml"
    news.write_toml(p, data)
    assert tomllib.loads(p.read_text(encoding="utf-8")) == data


def test_next_start_and_preshow_suggestions(cfg, monkeypatch):
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    at = datetime(2026, 11, 4, 17, 15, tzinfo=tz)  # a Wednesday
    assert news.next_start([(2, 18 * 60)], at) == datetime(2026, 11, 4, 18, 0, tzinfo=tz)
    assert news.next_start([(2, 17 * 60)], at) == datetime(2026, 11, 11, 17, 0, tzinfo=tz)
    assert news.next_start([(0, 60), (3, 0)], at) == datetime(2026, 11, 5, 0, 0, tzinfo=tz)
    monkeypatch.setattr(news, "show_slots", lambda cfg: {"kino": [(2, 18 * 60), (2, 18 * 60 + 30)],
                                                          "c404": [(2, 21 * 60)]})
    sid = story(cfg)
    story(cfg, "Unrelated", person="Don", show="c404")
    due = news.due_suggestions(cfg, at)
    assert [(d["show"], d["stories"]) for d in due] == [("kino", [sid])]       # c404 isn't within the hour
    n = news.mark_suggested(cfg, due[0]["slot"], "kino", due[0]["stories"])
    assert news.suggestion(cfg, n) == ("kino", [sid])
    assert news.due_suggestions(cfg, at) == []
    assert news.due_suggestions(cfg, at + timedelta(minutes=30)) == []          # 18:30 is the same day's show
    # Queued for c404 doesn't hide it from kino's suggestion (context is per show).
    news._SLOTS.clear()
    monkeypatch.setattr(news, "show_slots", lambda cfg: {"kino": [(3, 18 * 60)]})
    news.set_context(cfg, [sid], show="c404")
    assert [d["stories"] for d in news.due_suggestions(cfg, at + timedelta(days=1))] == [[sid]]
    news.set_context(cfg, [sid], show="kino")
    assert news.due_suggestions(cfg, at + timedelta(days=1)) == []


def test_show_slots_from_recent_streams(cfg):
    news._SLOTS.clear()
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    stream(cfg, "kick-1", started=datetime.now(tz).replace(hour=18, minute=4) - timedelta(days=7))
    stream(cfg, "kick-2", channel="nobody")
    late = datetime.now(tz).replace(hour=23, minute=50) - timedelta(days=2)    # rounds to the next day's 00:00
    stream(cfg, "kick-3", started=late)
    slots = news.show_slots(cfg)
    week_ago = datetime.now(tz) - timedelta(days=7)
    assert list(slots) == ["kino"]                                              # any folder order
    assert sorted(slots["kino"]) == sorted([(week_ago.weekday(), 18 * 60), ((late.weekday() + 1) % 7, 0)])
    news._SLOTS.clear()


def test_refusals_and_missing_verdicts_are_tried_again(cfg):
    db = news.connect(cfg)
    news.store(db, [{"url": f"https://x.test/{i}", "title": f"Jer item {i}", "snippet": "",
                     "published": news.iso(news.now()), "source": "X"} for i in range(2)], "X", 48)
    db.execute("UPDATE items SET matched = ?", (json.dumps(["Jer"]),))
    db.commit()

    class Refuses(FakeJudge):
        def json_call(self, *a):
            return None, {}
    assert news.judge_items(cfg, db, news.watchlist(cfg), Refuses([]), say=lambda m: None) == 0
    assert db.execute("SELECT COUNT(*) FROM items WHERE rating IS NULL AND claim IS NULL").fetchone()[0] == 2
    judge = FakeJudge([[{"i": 1, "person": "Jer", "show": "kino", "rating": "no", "story": 0, "same_as": 0,
                         "headline": "", "why": "No."}]])
    news.judge_items(cfg, db, news.watchlist(cfg), judge, say=lambda m: None)
    assert db.execute("SELECT COUNT(*) FROM items WHERE rating IS NULL").fetchone()[0] == 1   # item 2 left out
    db.close()


def test_dismissed_stories_still_gather_their_headlines(cfg):
    sid = story(cfg)
    news.dismiss(cfg, [sid])
    db = news.connect(cfg)
    news.store(db, [{"url": "https://x.test/1", "title": "Jer again", "snippet": "",
                     "published": news.iso(news.now()), "source": "X"}], "X", 48)
    db.execute("UPDATE items SET matched = ?", (json.dumps(["Jer"]),))
    db.commit()
    judge = FakeJudge([[{"i": 1, "person": "Jer", "show": "kino", "rating": "strong", "story": sid, "same_as": 0,
                         "headline": "", "why": "Same."}]])
    news.judge_items(cfg, db, news.watchlist(cfg), judge, say=lambda m: None)
    assert f"- {sid}: Rumble sues Jer" in judge.calls[0]
    assert news.to_post(cfg, db) == []                                          # joined, still dismissed
    db.close()


def test_a_feed_that_fails_to_parse_keeps_its_old_validators(cfg, monkeypatch):
    cfg["news"].update(feeds=[{"name": "F", "url": "https://f.test/rss"}], reddit=[], google_news=False,
                       min_fetch_minutes=0)
    monkeypatch.setattr(news, "fetch", lambda *a, **k: (b"<html>", "new-etag", "Mon"))
    news.check(cfg, judge=False, say=lambda m: None)
    db = news.connect(cfg)
    assert tuple(db.execute("SELECT etag, modified FROM feeds").fetchone()) == ("", "")
    db.close()


def test_cast_library_people_are_watched_without_a_kept_photo(cfg, tmp_path):
    cfg["thumbnail"]["photos"] = str(tmp_path / "photos")
    (tmp_path / "photos" / "Ethan Klein").mkdir(parents=True)
    jsonstate.write(tmp_path / "photos" / "Ethan Klein" / "cast.json", {"aliases": ["H3 Podcast"], "photos": {}})
    w = news.watchlist(cfg)
    assert w["ethan klein"].terms == ["Ethan Klein", "H3 Podcast"] and w["ethan klein"].search


def test_warroom_news_endpoints(cfg):
    sid = story(cfg)
    assert {"/api/news", "/api/news/search"} <= set(warroom.GET_API)
    out = warroom.news_get(cfg, "3")
    assert [s["id"] for s in out["stories"]] == [sid] and out["shows"]["kino"] == "Kino Casino"
    prev = warroom.news_context(cfg, {"ids": [sid], "preview": True})
    assert prev["show"] == "kino" and "Rumble" in prev["text"]
    assert not jsonstate.read(cfg["paths"]["news"] / "context.json", {})       # a preview queues nothing
    assert warroom.news_context(cfg, {"ids": [sid], "show": "c404"})["added"]["show"] == "c404"
    assert warroom.news_dismiss(cfg, {"ids": [sid]})["stories"][0]["dismissed"] == 1
    with pytest.raises(ClipperError):
        warroom.news_dismiss(cfg, {"ids": "1"})


def test_schema_is_idempotent(cfg):
    news.connect(cfg).close()
    db = news.connect(cfg)
    assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"feeds", "items", "stories"}
    assert isinstance(db, sqlite3.Connection)
    db.close()

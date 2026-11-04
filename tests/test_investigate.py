import json

import pytest

from clipper import investigate, jsonstate
from clipper.investigate import Budget, OverBudget

WORDS = ["so", "I", "told", "you", "the", "merch", "store", "made", "forty", "thousand", "dollars", "in", "October", "and", "that", "is", "real"]


def where_of(words):
    return investigate.word_index([{"word": w, "start": float(k)} for k, w in enumerate(words)])


def test_a_quote_is_found_where_it_was_said_and_a_paraphrase_is_not():
    starts, where = where_of(WORDS)
    at = investigate.locate(where, "the merch store made forty thousand dollars")
    assert starts[at] == 4.0
    assert investigate.locate(where, "the merch store made fourty thousand dollars") == at  # a word off is fine
    assert investigate.locate(where, "my shop earned a lot of money last month") is None
    assert investigate.locate(where, "merch store") is None  # too short to tell


def test_numbers_as_said():
    assert investigate.parse_value("$1.2 million") == 1_200_000
    assert investigate.parse_value("250k") == 250_000
    assert investigate.parse_value("40,000") == 40_000
    assert investigate.parse_value("") is None


def test_spellings_and_surnames_merge_into_the_common_name():
    from collections import Counter

    counts = Counter({"ethan klein": 9, "ethan kline": 2, "klein": 3, "hila": 4})
    merged = investigate.merge_aliases(counts, {"Hila Klein": "Hila"})
    assert merged["ethan kline"] == "ethan klein"
    assert merged["klein"] == "ethan klein"
    assert merged["hila klein"] == "hila"
    assert investigate.canon("The Quartering's") == "quartering"
    merged = investigate.merge_aliases(Counter({"coffee brand coffee": 5, "coffeebrandcoffee com": 2}))
    assert merged["coffeebrandcoffee com"] == "coffee brand coffee"


def test_his_own_channel_names_are_him_not_a_thread():
    facts = [fact("a", "2026-01-01", "v1", entities=["TheQuartering", "Acme"], channel="The Quartering")]
    investigate.add_keys(facts)
    assert facts[0]["keys"] == ["e:acme"]


def fact(fid, day, item, **kw):
    base = {"id": fid, "item": item, "date": day, "t": 1.0, "type": "claim", "about": "self",
            "entities": [], "statement": fid, "mode": "stated", "polarity": "neutral", "measure": "",
            "value": "", "quote": "q", "title": item, "channel": "c", "url": "", "source": "youtube"}
    return {**base, **kw}


@pytest.fixture
def ic(defaults):
    return dict(defaults["investigate"])


def keyed(facts):
    investigate.add_keys(facts)
    return investigate.groups(facts)


def test_a_name_talked_about_suddenly_is_a_trend(ic):
    facts = [fact(f"a{k}", f"2026-10-{10 + k:02d}", f"v{k}", entities=["Acme Corp"]) for k in range(4)]
    facts += [fact(f"b{k}", f"2026-0{k + 1}-01", f"w{k}", entities=["Other Guy"]) for k in range(6)]
    g = keyed(facts)
    newest = investigate.day("2026-10-13")
    found = investigate.trends(g, newest, investigate.day("2026-01-01"), ic)
    assert [c["key"] for c in found] == ["e:acme corp"]


def test_affirmed_then_denied_is_a_contradiction(ic):
    facts = [fact("a", "2026-01-01", "v1", type="business", entities=["Sponsor Co"], polarity="affirms"),
             fact("b", "2026-06-01", "v2", type="business", entities=["Sponsor Co"], polarity="denies"),
             fact("c", "2026-06-02", "v3", type="business", entities=["Sponsor Co"], polarity="denies",
                  mode="joke")]
    found = investigate.contradictions(keyed(facts), ic)
    assert len(found) == 1 and {f["id"] for f in found[0]["facts"]} == {"a", "b"}


def test_a_number_that_moves_is_a_shift(ic):
    facts = [fact(f"a{k}", f"2026-0{k + 1}-01", f"v{k}", measure="Merch revenue", value=v)
             for k, v in enumerate(["40k", "90k", "30k"])]
    found = investigate.shifts(keyed(facts), ic)
    assert found[0]["key"] == "m:merch revenue|self" and "up and down" in found[0]["why"]


def test_talk_then_silence_and_a_tease_never_paid_off_are_dropped_threads(ic):
    facts = [fact(f"a{k}", f"2026-0{k + 2}-01", f"v{k}", entities=["Big Deal"]) for k in range(3)]
    facts += [fact("t", "2026-09-01", "v9", type="tease", entities=["Secret Project"])]
    facts += [fact("n", "2026-10-30", "v10", entities=["Something Else"])]
    found = investigate.dropped(keyed(facts), investigate.day("2026-10-30"), ic)
    assert {c["key"] for c in found} == {"e:big deal", "e:secret project"}


def test_personal_remarks_close_together_converge(ic):
    facts = [fact(f"a{k}", f"2026-05-{k + 1:02d}", f"v{k}", type="legal", about="self") for k in range(3)]
    facts += [fact("z", "2024-01-01", "old", type="legal", about="self")]
    found = investigate.convergences(keyed(facts), ic)
    assert found[0]["key"] == "s:legal" and len(found[0]["facts"]) == 3


def test_about_narrows_to_the_focus(ic):
    facts = [fact("a", "2026-01-01", "v1", statement="Says the lawsuit is settled"),
             fact("b", "2026-02-01", "v2", statement="Says the lawsuit is ongoing"),
             fact("c", "2026-02-01", "v3", statement="Talks about coffee")]
    investigate.add_keys(facts)
    found = investigate.candidates(facts, ic, about="lawsuit")
    assert found[0]["kind"] == "about" and {f["id"] for f in found[0]["facts"]} == {"a", "b"}


def test_a_lead_keeps_only_receipts_from_its_own_verified_facts():
    cand = {"why": "w", "facts": [fact("a", "2026-01-01", "v1"), fact("b", "2026-02-01", "v2")]}
    out = {"keep": True, "reject": "", "headline": "H", "story": "S", "kind": "trend", "confidence": "medium",
           "confirm_or_kill": "c", "people": [], "timeline": ["b", "made-up", "a"], "search": "q"}
    lead = investigate.make_lead(out, cand)
    assert [r["fact"] for r in lead["receipts"]] == ["a", "b"]
    assert investigate.make_lead({**out, "timeline": ["a", "made-up"]}, cand) is None
    assert investigate.make_lead({**out, "keep": False}, cand) is None


def lead(ids, conf="medium", last="2026-01-01"):
    return {"confidence": conf, "last": last, "receipts": [{"fact": i} for i in ids]}


def test_leads_rank_by_confidence_and_drop_repeats():
    out = investigate.rank([lead("ab"), lead("abc", "high"), lead("xy", "low"), lead("abz")], 10)
    assert [len(x["receipts"]) for x in out] == [3, 2]
    assert [x["id"] for x in out] == ["L01", "L02"]


def test_the_budget_stops_a_call_that_would_go_over():
    class Spent:
        cost = 0.9

    b = Budget(1.0, Spent())
    b.reserve(0.05)
    with pytest.raises(OverBudget):
        b.reserve(0.06)
    b.release(0.05)
    b.reserve(0.06)


def test_coverage_terms_need_half_the_words():
    ts = investigate.terms("The Quartering lawsuit settlement")
    assert ts == ["quartering", "lawsuit", "settlement"]
    assert investigate.hit("The Quartering lawsuit explained", ts)
    assert not investigate.hit("The Quartering reacts to a movie", ts)


class FakeClaude:
    cost = 0.0

    def json_call(self, model, system, user, schema, effort):
        return {"facts": [
            {"type": "money", "about": "self", "entities": [], "statement": "s", "mode": "stated",
             "polarity": "affirms", "measure": "merch", "value": "40,000",
             "quote": "the merch store made forty thousand dollars"},
            {"type": "claim", "about": "self", "entities": [], "statement": "s", "mode": "stated",
             "polarity": "neutral", "measure": "", "value": "", "quote": "words he never said at all"}]}, {}


def subject(defaults, tmp_path):
    defaults["paths"]["archive"] = tmp_path
    d = tmp_path / "jer"
    (d / "v1").mkdir(parents=True)
    jsonstate.write(d / "catalog.json", {"v1": {"id": "v1", "title": "T", "duration_s": 60.0}})
    jsonstate.write(d / "v1" / "meta.json", {"date": "2026-10-01", "channel": "C", "url": "u", "source": "youtube"})
    jsonstate.write(d / "v1" / "transcript.json",
                    {"segments": [{"start": 0.0, "end": 16.0, "text": " ".join(WORDS)}]})
    (d / "v1" / "words.jsonl").write_text(
        "\n".join(json.dumps({"word": w, "start": float(k)}) for k, w in enumerate(WORDS)), encoding="utf-8")
    return d


def test_facts_keep_verified_quotes_and_are_cached_until_the_prompt_changes(defaults, tmp_path):
    d = subject(defaults, tmp_path)
    (it,) = investigate.items(defaults, "jer")
    rec = investigate.item_facts(defaults, "jer", it, FakeClaude(), "sys", Budget(10, FakeClaude()))
    assert rec["dropped"] == 1 and [f["t"] for f in rec["facts"]] == [4.0]
    (d / "investigate" / "facts").mkdir(parents=True)
    jsonstate.write(d / "investigate" / "facts" / "v1.json", rec)
    assert investigate.facts_todo(defaults, "jer", None, None, None, False) == ([], 1)
    assert investigate.facts_todo(defaults, "jer", "2026-11-01", None, None, False) == ([], 0)
    defaults["investigate"]["prompt_version"] = "v2"
    todo, _ = investigate.facts_todo(defaults, "jer", None, None, None, True)
    assert [x["id"] for x in todo] == ["v1"]


def test_leads_round_trip_and_the_dossier(defaults, tmp_path):
    subject(defaults, tmp_path)
    x = investigate.make_lead(
        {"keep": True, "reject": "", "headline": "H", "story": "S", "kind": "shift", "confidence": "low",
         "confirm_or_kill": "c", "people": ["P"], "timeline": ["a", "b"], "search": "q"},
        {"why": "w", "facts": [fact("a", "2026-01-01", "v1"), fact("b", "2026-02-01", "v2")]})
    data = {"subject": "jer", "about": "", "made_at": "2026-11-03T00:00:00+00:00", "model": "m",
            "candidates": 1, "leads": investigate.rank([x], 5)}
    investigate.save_leads(defaults, "jer", data)
    assert investigate.load_leads(defaults, "jer") == data
    md = (tmp_path / "jer" / "investigate" / "leads.md").read_text(encoding="utf-8")
    assert "## L01 H" in md and "> q" in md


class FakePrompts:
    def __init__(self):
        self.vars = {}

    def get(self, name):
        return name


class Scripted:
    """A model that answers every call with the same thing, and counts them."""
    cost = 0.0

    def __init__(self, out):
        self.out, self.calls = out, 0

    def json_call(self, model, system, user, schema, effort):
        self.calls += 1
        return self.out, {}


def write_facts(d, facts):
    (d / "investigate" / "facts").mkdir(parents=True, exist_ok=True)
    for f in facts:
        meta = {k: f[k] for k in ("item", "date", "title", "channel", "url", "source")}
        jsonstate.write(d / "investigate" / "facts" / f"{f['item']}.json", {**meta, "facts": [f]})


def test_leads_are_written_then_rejected_ones_kept_with_the_old_file_backed_up(defaults, tmp_path, monkeypatch):
    d = subject(defaults, tmp_path)
    defaults["investigate"]["parallel"] = 1
    write_facts(d, [fact(f"v{k}:0", f"2026-0{k + 1}-01", f"v{k}", measure="Merch revenue", value=v)
                    for k, v in enumerate(["40k", "90k", "30k"])])
    keep = {"keep": True, "reject": "", "headline": "Merch numbers move", "story": "S", "kind": "shift",
            "confidence": "medium", "confirm_or_kill": "c", "people": [], "search": "merch",
            "timeline": ["v0:0", "v1:0", "v2:0"]}  # receipts are the thread's own fact ids
    model = Scripted(keep)
    monkeypatch.setattr(investigate, "llm_setup", lambda cfg, m: (model, FakePrompts()))
    investigate.run_leads(defaults, "jer")
    data = investigate.load_leads(defaults, "jer")
    assert model.calls == data["candidates"] >= 1
    (lead,) = [x for x in data["leads"] if x["kind"] == "shift"]
    assert lead["id"].startswith("L") and len(lead["receipts"]) == 3

    model.out = {**keep, "keep": False, "reject": "a normal sales swing"}
    investigate.run_leads(defaults, "jer")
    data = investigate.load_leads(defaults, "jer")
    assert data["leads"] == [] and {r["reason"] for r in data["rejected"]} == {"a normal sales swing"}
    assert len(list((d / "investigate").glob("leads.*.json"))) == 1


def test_check_labels_coverage_keeps_only_real_matches_and_a_dead_search_is_unchecked(
        defaults, tmp_path, monkeypatch):
    d = subject(defaults, tmp_path)
    leads = [investigate.make_lead(
        {"keep": True, "reject": "", "headline": h, "story": "S", "kind": "shift", "confidence": "low",
         "confirm_or_kill": "c", "people": [], "timeline": [f"{h}a", f"{h}b"], "search": h},
        {"why": h, "facts": [fact(f"{h}a", "2026-01-01", f"{h}1"), fact(f"{h}b", "2026-02-01", f"{h}2")]})
        for h in ("covered one", "dead one")]
    investigate.save_leads(defaults, "jer", {"subject": "jer", "about": "", "made_at": "2026-11-03T00:00:00+00:00",
                                         "model": "m", "candidates": 2, "leads": investigate.rank(leads, 5)})
    result = {"source": "youtube", "channel": "C", "date": "", "title": "t", "url": "u"}
    monkeypatch.setattr(investigate, "findings", lambda cfg, s, lead: (
        ([result, {**result, "url": "u2"}], {"reddit": "429"}) if lead["headline"] == "covered one"
        else ([], {"youtube": "down"})))
    model = Scripted({"coverage": "partly covered", "note": "one video", "matches": [1, 9]})
    monkeypatch.setattr(investigate, "llm_setup", lambda cfg, m: (model, FakePrompts()))
    investigate.run_check(defaults, "jer")
    by = {x["headline"]: x for x in investigate.load_leads(defaults, "jer")["leads"]}
    assert by["covered one"]["coverage"]["label"] == "partly covered"
    assert [x["url"] for x in by["covered one"]["coverage"]["links"]] == ["u2"]
    assert by["dead one"]["coverage"]["label"] == "unchecked" and model.calls == 1
    trail = jsonstate.read(d / "investigate" / "swarm" / f"{by['covered one']['id']}.json", {})
    assert trail["errors"] == {"reddit": "429"} and len(trail["results"]) == 2

import json
from collections import Counter

import pytest

from clipper import investigate, jsonstate, people, story
from clipper.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path):
    p = tmp_path / "streamers.json"
    p.write_text(json.dumps({
        "streamers": [
            {"name": "The Quartering (Jer)", "slug": "the-quartering",
             "channels": {"youtube": "@TheQuartering", "youtube_live": "@QuarteringLive",
                          "rumble": "https://rumble.com/c/TheQuartering"}},
            {"name": "DSP (DarkSydePhil)", "slug": "dsp", "channels": {"kick": "dspgamingreal"}},
        ],
        "uploads_only": [{"name": "Chris Bores (Irate Gamer / Ghost Doctor)",
                          "channels": {"youtube": "@irategamer", "youtube_2": "@GhostDoctor"}}],
    }), encoding="utf-8")
    photos = tmp_path / "photos"
    for name, idx in {"Jer": {"aliases": ["Jeremy"], "channels": ["https://kick.com/Jer"]},
                      "Hasan Piker": {}}.items():
        (photos / name).mkdir(parents=True)
        jsonstate.write(photos / name / "cast.json", idx)
    (tmp_path / "archive" / "jer").mkdir(parents=True)
    (tmp_path / "archive" / "story-x").mkdir()
    defaults["paths"].update(streamers=p, archive=tmp_path / "archive")
    defaults["thumbnail"]["photos"] = str(photos)
    defaults["story"]["people"] = {"Don Flurgundy": {"kick": "https://kick.com/DonFlurgundy"}}
    return defaults


def test_names_take_brackets_slashes_aliases_and_the_id():
    assert people.names({"name": "The Quartering (Jer)", "aliases": ["Jeremy"]}, "the-quartering") == \
        {"quartering jer", "quartering", "jer", "jeremy"}
    assert {"irate gamer", "ghost doctor"} <= people.names({"name": "Chris Bores (Irate Gamer / Ghost Doctor)"})
    assert people.names({"name": "Al"}) == set()   # too short to match on


def test_channels_have_one_spelling():
    assert people.channel_id("youtube", "https://www.youtube.com/@TheQuartering/videos") == "@TheQuartering"
    assert people.channel_id("kick", "https://kick.com/DonFlurgundy") == "donflurgundy"
    assert people.channel_id("x", "@PoweroftheTruth") == "powerofthetruth"
    assert people.platform_of("youtube_live") == people.platform_of("youtube_2") == "youtube"
    assert people.platform_of("facebook") is None
    assert people.parse_channel("https://x.com/Andywarski") == ("x", "andywarski")
    with pytest.raises(ClipperError):
        people.parse_channel("myspace=tom")


def test_the_older_lists_fold_into_one_record_each(cfg):
    everyone = people.load(cfg)
    jer = everyone["the-quartering"]
    assert jer["channels"] == {"youtube": ["@TheQuartering", "@QuarteringLive"],
                               "rumble": ["https://rumble.com/c/TheQuartering"], "kick": ["jer"]}
    assert (jer["cast"], jer["subject"]) == ("Jer", "jer")        # the cast folder, the archive by name
    assert set(jer["aliases"]) == {"Jer", "Jeremy"}
    assert jer["from"] == ["streamers.json", "cast"]
    assert everyone["don-flurgundy"]["channels"] == {"kick": ["donflurgundy"]}
    assert everyone["hasan-piker"]["from"] == ["cast"]
    assert "chris-bores" in everyone and not any(r["subject"] == "story-x" for r in everyone.values())
    assert people.conflicts(everyone) == {}


def test_find_by_any_name(cfg):
    assert people.find(cfg, "Jeremy")[0] == "the-quartering"
    assert people.find(cfg, "the quartering")[0] == "the-quartering"
    assert people.find(cfg, "dsp")[0] == "dsp"
    assert people.find(cfg, "nobody") is None


def test_set_writes_the_whole_record_and_people_json_wins(cfg):
    pid = people.set_person(cfg, "Jer", new_name="Jer", aliases=["Jerry Hambly"], unaliases=["Jeremy"],
                            channels=["x=@TheQuartering", "https://odysee.com/@TheQuartering"],
                            notes="Rumble first")
    assert pid == "the-quartering"
    own = people.stored(cfg)[pid]
    assert own["name"] == "Jer" and "The Quartering (Jer)" in own["aliases"]   # the old name stays a name
    assert own["channels"]["x"] == ["thequartering"] and own["channels"]["odysee"] == ["https://odysee.com/@TheQuartering"]
    assert own["notes"] == "Rumble first" and "Jerry Hambly" in own["aliases"]
    rec = people.load(cfg)[pid]
    assert rec["from"][0] == "people.json" and rec["name"] == "Jer"
    assert people.find(cfg, "Jeremy")[0] == pid   # from the cast library: it comes back from there
    people.set_person(cfg, pid, unchannels=["x=thequartering"])
    assert "x" not in people.stored(cfg)[pid]["channels"]


def test_set_adds_a_new_person_and_checks_the_subject(cfg):
    assert people.set_person(cfg, "Ben Avery", channels=["youtube=@BenAvery"]) == "ben-avery"
    assert people.find(cfg, "ben avery")[1]["channels"] == {"youtube": ["@BenAvery"]}
    with pytest.raises(ClipperError, match="no archive subject"):
        people.set_person(cfg, "Ben Avery", subject="ben")


def test_import_writes_everyone(cfg, capsys):
    people.import_all(cfg, dry_run=True)
    assert not people.path(cfg).exists()
    people.import_all(cfg)
    assert set(people.stored(cfg)) == set(people.load(cfg))
    assert people.load(cfg)["the-quartering"]["from"] == ["people.json", "streamers.json", "cast"]


def test_a_shared_name_is_a_conflict_and_ambiguous(cfg):
    people.set_person(cfg, "Phil Burnell", aliases=["Phil"])
    people.set_person(cfg, "Phil Collins", aliases=["Phil"])
    assert people.conflicts(people.load(cfg))["phil"] == ["phil-burnell", "phil-collins"]
    with pytest.raises(ClipperError, match="could be"):
        people.find(cfg, "Phil")
    assert people.find(cfg, "Phil Collins")[0] == "phil-collins"
    assert "phil" not in people.alias_map(cfg, "jer")      # a shared name merges nothing


def test_story_roster_and_seeds_use_people(cfg):
    roster = story.roster(cfg)
    assert [p["name"] for p in story.named("Jeremy is back", roster)] == ["The Quartering (Jer)"]
    jer = next(p for p in roster if p["slug"] == "the-quartering")
    assert [(s["kind"], s["target"]) for s in story.seeds([jer])] == \
        [("youtube_channel", "@TheQuartering"), ("youtube_channel", "@QuarteringLive"), ("kick_videos", "jer")]
    old_brief = {"name": "Jer", "channels": {"youtube": "@TheQuartering", "youtube_live": "@QuarteringLive"}}
    assert len(story.seeds([old_brief])) == 2


def test_investigate_takes_everyones_names(cfg):
    m = people.alias_map(cfg, "jer")
    assert m["jeremy"] == m["quartering"] == "self"
    assert m["darksydephil"] == "dsp"
    facts = [{"entities": ["DarkSydePhil", "Jeremy"], "channel": "", "item": "a", "about": "other", "type": "claim",
              "measure": "", "value": ""},
             {"entities": ["DSP"], "channel": "", "item": "b", "about": "other", "type": "claim", "measure": "", "value": ""}]
    investigate.add_keys(facts, m)
    assert facts[0]["names"] == facts[1]["names"] == ["dsp"]
    assert investigate.merge_aliases(Counter({"darksydephil": 1}), {**m, "DarkSydePhil": "Phil"})["darksydephil"] == "phil"
    assert people.display_name(cfg, "jer") == "The Quartering (Jer)"
    assert people.display_name(cfg, "nobody") == "nobody"

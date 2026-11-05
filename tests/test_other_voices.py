from clipper import actions, notify, review
from clipper.transcript import outside_voice_s

CAST = ["Andy", "PPP"]


def turn(speaker, start, n, seg, step=1.0):
    """n one-second words by one speaker, one transcript line (seg); who as load_words sets it."""
    who = "?" if speaker and speaker.startswith("SPEAKER_") else speaker
    return [{"word": "w", "start": start + i * step, "end": start + i * step + 0.9, "seg": seg,
             "speaker": speaker, "who": who} for i in range(n)]


def test_outside_voice_counts_unnamed_and_other_names_not_the_cast_or_music():
    ws = (turn("PPP", 0, 5, 0) + turn("SPEAKER_01", 5, 6, 1) + turn("music", 11, 4, 2)
          + turn("Jer", 15, 3, 3) + turn("andy", 18, 4, 4))
    # [?] 5.0-10.9 and Jer 15.0-17.9; PPP, music and Andy (any case) don't count
    assert outside_voice_s(ws, 0, 30, CAST) == 8.8
    assert outside_voice_s(ws, 7, 30, CAST) == 6.8  # clipped to the window


def test_outside_voice_ignores_a_voice_change_of_a_word_or_two():
    line = turn("PPP", 0, 4, 0) + turn("SPEAKER_02", 4, 1, 0) + turn("PPP", 5, 4, 0)
    assert outside_voice_s(line, 0, 10, CAST) == 0.0  # one word: a turn edge, not a voice


def test_outside_voice_is_unknown_without_speaker_names_or_cast():
    ws = turn("SPEAKER_01", 0, 5, 0)
    assert outside_voice_s([{k: v for k, v in w.items() if k != "who"} for w in ws], 0, 9, CAST) is None
    assert outside_voice_s(ws, 0, 9, []) is None


def cfg(on=True, limit=5):
    return {"shorts": {"hold_source_audio": on}, "scoring": {"max_source_audio_s": limit}}


def test_source_hold_proposes_hold_over_the_limit_only():
    assert review.source_hold({"source_audio_s": 22.0}, cfg()) == {"Other voices": 22.0, "Hold": "true"}
    assert review.source_hold({"source_audio_s": 5.0}, cfg()) == {"Other voices": 5.0, "Hold": "false"}
    assert review.source_hold({"source_audio_s": 22.0}, cfg(on=False)) == {"Other voices": 22.0}
    assert review.source_hold({"source_audio_s": None}, cfg()) == {}
    assert review.source_hold({}, cfg()) == {}


def test_a_released_hold_stays_released_and_a_fixed_recut_releases_ours():
    # Kitsu column ids: f_hold. A hold the user released isn't put back on the next publish...
    assert review.columns_to_fill({"f_hold": "true"}, {"f_hold": "true"}, {"f_hold": "false"}, None, None) == {}
    # ...and one we set comes off once a re-cut is under the limit.
    assert review.columns_to_fill({"f_hold": "false"}, {"f_hold": "true"}, {"f_hold": "true"}, None, None) \
        == {"f_hold": "false"}
    # A hold set by hand on a clean Short stays.
    assert review.columns_to_fill({"f_hold": "false"}, {"f_hold": "false"}, {"f_hold": "true"}, None, None) == {}


def test_other_voices_text_only_over_the_limit():
    assert actions.other_voices("22.3", 5) == "22 s of other voices (limit 5 s)"
    assert actions.other_voices(4.0, 5) == ""
    assert actions.other_voices("", 5) == ""
    assert actions.other_voices(None, 5) == ""


def row(**kw):
    return {"kind": "short", "duration_s": 40, "score": "7", "category": "joke", "found_by": "chat",
            "hook": "", "decision": "pending", "status": "wfa", "scheduled": "", "uploaded": False,
            "comment": "", "hold": True, "title": "t", "kitsu_url": "https://k/x", "youtube_url": "",
            "short": "kick-1/v01", "date": "2026-11-04", "revision": 1, "dropped": "", "publish_at": "", **kw}


def test_card_says_why_a_short_is_held():
    embed, _ = notify.card(row(other_voices="22 s of other voices (limit 5 s)"))
    f = next(f for f in embed["fields"] if f["name"] == "Held: other voices")
    assert f["value"].startswith("22 s of other voices") and "Release the hold" in f["value"]
    embed, _ = notify.card(row(other_voices="22 s of other voices (limit 5 s)", hold=False))
    assert any(f["name"] == "Other voices" for f in embed["fields"])
    embed, _ = notify.card(row(other_voices=""))
    assert not any("voices" in f["name"] for f in embed["fields"])


def publish(column: str | None, before: dict, hold: str) -> tuple[str | None, dict]:
    """One review publish of a Short's Hold, as fill_columns does it: (column after, kept)."""
    write = review.columns_to_fill({"f_hold": hold}, {"f_hold": before["Hold"]} if "Hold" in before else {},
                                   {"f_hold": column}, None, None)
    column = write.get("f_hold", column)
    return column, review.proposals_to_keep({"Hold": hold}, before, {"Hold"} if write else set())


def test_a_hold_set_by_hand_survives_recuts_over_and_back_under_the_limit():
    col, kept = publish(None, {}, "false")        # clean Short: we write false
    assert (col, kept) == ("false", {"Hold": "false"})
    col = "true"                                  # the reviewer holds it by hand
    col, kept = publish(col, kept, "true")        # re-cut over the limit: nothing to write
    assert (col, kept) == ("true", {"Hold": "false"})  # and true isn't taken as ours
    col, kept = publish(col, kept, "false")       # re-cut under the limit
    assert col == "true"                          # the hand hold stays


def test_our_hold_comes_off_when_a_recut_is_under_the_limit():
    col, kept = publish(None, {}, "true")
    assert (col, kept) == ("true", {"Hold": "true"})
    col, kept = publish(col, kept, "false")
    assert (col, kept) == ("false", {"Hold": "false"})


def test_proposals_to_keep_leaves_other_columns_as_proposed():
    assert review.proposals_to_keep({"YouTube title": "new", "Hold": "true"}, {"YouTube title": "old"},
                                    {"YouTube title": "edited", "Hold": "true"}) == \
        {"YouTube title": "new", "Hold": "true"}


def test_a_released_hold_stays_released_through_recuts():
    col, kept = publish(None, {}, "true")          # ours
    col = "false"                                  # released by hand
    for hold in ("true", "false", "true"):         # re-cuts over, under, over
        col, kept = publish(col, kept, hold)
        assert col == "false"

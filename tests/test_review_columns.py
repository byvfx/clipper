from types import SimpleNamespace

import pytest

from clipper import review
from clipper.errors import ClipperError


def fake_gazu():
    written = []
    shot = SimpleNamespace(
        get_shot=lambda eid: {"data": {"f_title": "shot title"}},
        get_sequence=lambda eid: {"data": None},
        update_shot_data=lambda eid, d: written.append(("Shot", eid, d)),
        update_sequence_data=lambda eid, d: written.append(("Sequence", eid, d)))
    return SimpleNamespace(shot=shot), written


FMAP = {("Shot", "YouTube title"): "f_title", ("Sequence", "YouTube URL"): "f_url"}


def test_reads_and_writes_go_to_the_right_entity_type():
    gazu, written = fake_gazu()
    assert review.entity_data(gazu, "Shot", "s1") == {"f_title": "shot title"}
    assert review.entity_data(gazu, "Sequence", "q1") == {}
    review.write_data(gazu, "Sequence", "q1", {"f_url": "u"})
    assert written == [("Sequence", "q1", {"f_url": "u"})]


def test_set_columns_by_name():
    gazu, written = fake_gazu()
    # A column this production lacks is skipped, unless strict.
    assert review.set_columns(gazu, FMAP, "Sequence", "q1", {"YouTube URL": "u", "Rumble URL": "r"}) \
        == {"f_url": "u"}
    assert review.set_columns(gazu, FMAP, "Shot", "s1", {"Rumble URL": "r"}) == {}
    assert written == [("Sequence", "q1", {"f_url": "u"})]
    with pytest.raises(ClipperError, match="no Shot column"):
        review.set_columns(gazu, FMAP, "Shot", "s1", {"Rumble URL": "r"}, strict=True)


def test_columns_to_fill_keeps_edits():
    values = {"f_title": "new title", "f_tags": "a, b", "f_hook": "hook", "f_thumb": "NEW WORDS"}
    before = {"f_title": "old title", "f_tags": "a", "f_hook": "hook"}   # last proposed
    current = {"f_title": "old title",          # still ours: follows the new proposal
               "f_tags": "edited, by hand",     # edited in Kitsu: kept
               "f_hook": "",                    # empty: filled
               "f_thumb": "OLD WORDS"}
    assert review.columns_to_fill(values, before, current, "f_thumb", "OLD WORDS") == {
        "f_title": "new title", "f_hook": "hook", "f_thumb": "NEW WORDS"}
    # A reviewer's thumbnail text stays.
    assert "f_thumb" not in review.columns_to_fill(values, before, current, "f_thumb", "CLIPPER'S")
    assert review.columns_to_fill(values, before, dict(values), "f_thumb", "OLD WORDS") == {}

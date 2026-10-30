import pytest

from clipper import shorts


def words(*spans, seg_every=3):
    """One word per (start, end); a new transcript line every seg_every words."""
    return [{"word": f"w{i}.", "start": s, "end": e, "seg": i // seg_every}
            for i, (s, e) in enumerate(spans)]


@pytest.fixture
def sc(defaults):
    return {**defaults["shorts"], "min_s": 0, "max_s": 60, "pad_s": 0.2, "end_pause_s": 0}


def test_short_window_uses_the_short_window_and_snaps_to_word_gaps(sc):
    ws = words((9.0, 10.0), (10.5, 11.0), (20.0, 21.0), (30.0, 31.0))
    c = {"in_s": 5.0, "out_s": 40.0, "short_in_s": 10.2, "short_out_s": 20.5}
    assert shorts.short_window(c, {**sc, "window": "short"}, ws, [], 100.0) == (10.3, 21.2)
    assert shorts.short_window(c, {**sc, "window": "clip"}, ws, [], 100.0) == (8.8, 31.2)
    # never longer than max_s
    assert shorts.short_window(c, {**sc, "window": "clip", "max_s": 20}, ws, [], 100.0) == (8.8, 28.8)


def test_short_window_grows_inside_its_topic(sc):
    ws = words((0, 1), (2, 3), (4, 5), (6, 7), (8, 9), seg_every=1)
    c = {"in_s": 4.0, "out_s": 5.0}
    sc = {**sc, "window": "clip", "min_s": 4}
    # Two lines of setup for each one after, but not before the topic segment's start.
    assert shorts.short_window(c, sc, ws, [], 9.0) == (0.0, 5.2)
    assert shorts.short_window(c, sc, ws, [{"start_s": 2.0, "end_s": 9.5}], 9.0) == (1.8, 7.2)


LAYOUTS = {"watch": {}, "cams": {}, "center": {"pick": False}}


def choose(layout=None, rv=None, prev=None, again=False, layouts=LAYOUTS):
    return shorts.choose_layout(layout, rv or {}, prev or {}, layouts, 10.0, 40.0, again)


def test_choose_layout_order():
    assert choose("cams") == ([[0.0, 30.0, "cams"]], "cli")
    edited = {"fields": {"Layout": "cams"}, "edited": ["Layout"]}
    assert choose(rv=edited, prev={"layout": "watch"}) == ([[0.0, 30.0, "cams"]], "kitsu")
    # The column counts only when someone changed it from what clipper proposed.
    assert choose(rv={"fields": {"Layout": "cams"}}, prev={"layout": "watch"}) \
        == ([[0.0, 30.0, "watch"]], "model")
    # Set by hand: kept even with --rescene; chosen by the model: labelled again.
    assert choose(prev={"layout": "watch", "layout_by": "kitsu"}, again=True) \
        == ([[0.0, 30.0, "watch"]], "kitsu")
    assert choose(prev={"layout": "watch"}, again=True) == (None, "")
    assert choose() == (None, "")


def test_choose_layout_keeps_scenes_for_the_same_window():
    scenes = [[0.0, 12.0, "cams"], [12.0, 30.0, "watch"]]
    prev = {"layout": "cams 0:00 > watch 0:12", "scenes": scenes, "in_s": 10.0, "out_s": 40.0}
    assert choose(prev=prev) == (scenes, "model")
    assert choose(prev={**prev, "out_s": 41.0}) == (None, "")     # the window moved
    assert choose(prev=prev, again=True) == (None, "")
    assert choose(layouts={"watch": {}, "center": {"pick": False}}) == ([[0.0, 30.0, "watch"]], "only")


def test_signature_of_one_scene_is_the_old_single_layout_one(sc):
    layouts = {"watch": {"panels": [1]}, "cams": {"panels": [2]}}
    one = shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 30.0, "watch"]], layouts)
    assert one == shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 20.0, "watch"]], layouts)
    assert one != shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 30.0, "cams"]], layouts)
    two = shorts.short_signature(sc, "h264", 1.0, 31.0,
                                 [[0.0, 9.0, "cams"], [9.0, 30.0, "watch"]], layouts)
    assert two not in (one, shorts.short_signature(sc, "hevc", 1.0, 31.0,
                                                   [[0.0, 9.0, "cams"], [9.0, 30.0, "watch"]], layouts))

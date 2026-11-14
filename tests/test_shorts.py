import pytest

from clipper import shorts
from clipper.errors import ClipperError


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


def test_a_retake_trim_is_grown_to_min_s(tmp_path, defaults):
    """#77 L16: a retake that trims a Short under [shorts] min_s is widened by whole lines."""
    import json

    from clipper import retake
    ws = words(*[(t, t + 0.8) for t in range(40)], seg_every=1)
    (tmp_path / "words.jsonl").write_text("\n".join(json.dumps(w) for w in ws), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"duration_s": 40}), encoding="utf-8")
    (tmp_path / "shorts.json").write_text(json.dumps({"v01_t1": {"in_s": 0, "out_s": 30}}), encoding="utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"pass_a": [{"id": "t1", "in_s": 0, "out_s": 30}]}),
                                              encoding="utf-8")
    cfg = {**defaults, "shorts": {**defaults["shorts"], "min_s": 15, "max_s": 59, "pad_s": 0.2}}
    st = {"kind": "short", "id": "t1", "file": "shorts/v01_t1_x.mp4"}
    a, b = retake.snap_trim(cfg, tmp_path, st, {"start_s": 20, "end_s": 25})
    assert b - a >= 15 and a < 20 and b > 24
    a, b = retake.snap_trim(cfg, tmp_path, {**st, "kind": "pick"}, {"start_s": 20, "end_s": 25})
    assert b - a < 15  # horizontal clips keep the asked length


def test_a_retake_trim_over_min_s_with_little_speech_is_grown(tmp_path, defaults):
    """Copilot on #106: a 16 s trim holding 4 s of speech snaps to ~4 s; it must still grow."""
    import json

    from clipper import retake
    ws = words(*[(t, t + 0.8) for t in (*range(20), *range(30, 34), *range(44, 70))], seg_every=1)
    (tmp_path / "words.jsonl").write_text("\n".join(json.dumps(w) for w in ws), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"duration_s": 70}), encoding="utf-8")
    (tmp_path / "shorts.json").write_text(json.dumps({"v01_t1": {"in_s": 0, "out_s": 30}}), encoding="utf-8")
    cfg = {**defaults, "shorts": {**defaults["shorts"], "min_s": 15, "max_s": 59, "pad_s": 0.2}}
    st = {"kind": "short", "id": "t1", "file": "shorts/v01_t1_x.mp4"}
    a, b = retake.snap_trim(cfg, tmp_path, st, {"start_s": 22, "end_s": 38})
    assert b - a >= 15


def test_a_compilation_retake_trims_its_first_and_last_segments(tmp_path):
    """A compilation's ends are its first segment's start and last segment's end."""
    import json

    import pytest

    from clipper import retake
    from clipper.errors import ClipperError
    segs = [{"id": "s1", "start_s": 100.0, "end_s": 200.0, "title": "a", "file": "s1_x.mp4"},
            {"id": "s2", "start_s": 300.0, "end_s": 400.0, "title": "b", "file": "s2_x.mp4"},
            {"id": "s3", "start_s": 399.5, "end_s": 500.0, "title": "c", "file": "s3_x.mp4"}]
    (tmp_path / "segments.json").write_text(json.dumps({"segments": segs, "compilations": [
        {"topic": "X", "segments": ["s2", "s3"], "file": "x.mp4"}]}), encoding="utf-8")
    st = {"kind": "compilation", "id": "comp-x", "file": "compilations\\x.mp4"}
    assert retake.window(tmp_path, st) == (300.0, 500.0)
    s2, s3 = segs[1], segs[2]
    assert retake.comp_edits([s2, s3], 310.0, 500.0) == [(s2, 310.0, 400.0)]
    assert retake.comp_edits([s2, s3], 300.0, 480.0) == [(s3, 399.5, 480.0)]
    assert retake.comp_edits([s2, s3], 310.0, 480.0) == [(s2, 310.0, 400.0), (s3, 399.5, 480.0)]
    assert retake.comp_edits([s2], 310.0, 390.0) == [(s2, 310.0, 390.0)]
    assert retake.comp_edits([s2, s3], 300.1, 500.0) == []
    with pytest.raises(ClipperError):
        retake.comp_edits([s2, s3], 399.0, 500.0)


def test_a_segment_retake_snaps_only_the_end_it_moves(tmp_path, defaults):
    """A trimmed start lands in a word gap; the end the note didn't move stays put even
    with silence before it."""
    import json

    from clipper import retake
    ws = words(*[(t, t + 0.8) for t in range(10, 40)], seg_every=1)
    (tmp_path / "words.jsonl").write_text("\n".join(json.dumps(w) for w in ws), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"duration_s": 100}), encoding="utf-8")
    (tmp_path / "segments.json").write_text(json.dumps({"segments": [
        {"id": "s1", "start_s": 5.0, "end_s": 60.0}]}), encoding="utf-8")
    st = {"kind": "segment", "id": "s1", "file": "segments\\s1_x.mp4"}
    a, b = retake.snap_trim(defaults, tmp_path, st, {"start_s": 15.4, "end_s": 60.0})
    assert 14.8 <= a <= 15.0 and b == 60.0


def test_a_retake_trim_snapped_under_3_s_is_refused(tmp_path, defaults):
    """Copilot on #151: a 10 s trim holding one short word snaps to under a second."""
    import json

    import pytest

    from clipper import retake
    ws = words((25.0, 25.5), seg_every=1)
    (tmp_path / "words.jsonl").write_text(json.dumps(ws[0]), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"duration_s": 100}), encoding="utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"pass_a": [{"id": "t1", "in_s": 0, "out_s": 60}]}),
                                              encoding="utf-8")
    with pytest.raises(ValueError):
        retake.snap_trim(defaults, tmp_path, {"kind": "pick", "id": "t1"}, {"start_s": 20, "end_s": 30})


def test_trimming_both_ends_of_a_compilation_re_cuts_it_once(tmp_path, monkeypatch):
    """Copilot on #151: both segments are cut, then the compilation, once."""
    import json

    from clipper import cut, retake
    segs = [{"id": "s1", "start_s": 0.0, "end_s": 100.0, "file": "s1_x.mp4"},
            {"id": "s2", "start_s": 100.0, "end_s": 200.0, "file": "s2_x.mp4"}]
    (tmp_path / "segments.json").write_text(json.dumps({"segments": segs, "compilations": [
        {"topic": "X", "segments": ["s1", "s2"], "file": "x.mp4"}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(cut, "cut_segments", lambda sdir, cfg, ids: calls.append(("seg", sorted(ids))))
    monkeypatch.setattr(cut, "cut_compilations", lambda sdir, cfg, topics: calls.append(("comp", topics)))
    st = {"kind": "compilation", "id": "comp-x", "file": "compilations/x.mp4"}
    out = retake.trim_compilation({}, tmp_path, st, 10.0, 190.0)
    assert calls == [("seg", ["s1", "s2"]), ("comp", ["X"])]
    assert out == [("s1_x", "s1"), ("s2_x", "s2"), ("comp-x", "comp-x")]
    got = {s["id"]: (s["start_s"], s["end_s"]) for s in json.loads((tmp_path / "segments.json").read_text())["segments"]}
    assert got == {"s1": (10.0, 100.0), "s2": (100.0, 190.0)}


def test_panel_crops_keeps_every_panel_height_even():
    # Channel 404's "watch" (545 + 1375): odd 4:2:0 panels made vstack overrun its frame
    layout = {"panels": [{"src": [0.3542, 0.1389, 0.6094, 0.5463], "h": 545},
                         {"src": [0.0521, 0.2315, 0.2917, 0.6602], "h": 1375}]}
    crops = shorts.panel_crops(layout, 1920, 1080, 1080, 1920)
    assert [c[4] for c in crops] == [544, 1376]
    assert all(v % 2 == 0 for c in crops for v in c)
    with pytest.raises(ClipperError):
        shorts.panel_crops({"panels": [{"src": [0, 0, 1, 1], "h": 900}]}, 1920, 1080, 1080, 1920)


def test_a_face_panel_is_framed_on_the_face():
    # A 1.49:1 cam box (Chud's) into a 1080x1178 slot keeps 62% of its width.
    layout = {"panels": [{"src": [0.0, 0.0, 1.0, 0.4], "h": 742},
                         {"src": [0.7708, 0.6519, 0.2292, 0.2741], "focus": "face"}]}
    plain = shorts.panel_crops(layout, 1920, 1080, 1080, 1920)
    assert plain[1][2] == 270 and plain[1][0] == 1480 + int((440 - 270.6) * 0.5)  # no face: the middle
    on_face = shorts.panel_crops(layout, 1920, 1080, 1080, 1920, {1: (0.6, 0.6)})
    x, _, w, _, _ = on_face[1]
    assert w == 270 and abs(x + w / 2 - (1480 + 0.6 * 440)) <= 2  # centred on the face
    at_edge = shorts.panel_crops(layout, 1920, 1080, 1080, 1920, {1: (0.9, 0.6)})
    assert at_edge[1][0] + at_edge[1][2] >= 1918  # too far right to centre: the box's edge
    assert on_face[0] == plain[0]  # the other panel is untouched


def test_face_centers_take_the_faces_in_the_box_during_the_window():
    layout = {"panels": [{"src": [0.0, 0.0, 0.7, 1.0]},
                         {"src": [0.7, 0.6, 0.3, 0.4], "focus": "face"}]}
    face = lambda t, x, y: {"t": t, "box": [x - 0.05, y - 0.05, 0.1, 0.1]}
    faces = [face(10, 0.3, 0.5),    # in the video panel: not counted
             face(12, 0.91, 0.8), face(14, 0.85, 0.8), face(16, 0.88, 0.76),
             face(500, 0.76, 0.7)]  # another time: only a fallback
    got = shorts.face_centers(layout, faces, 10, 20)
    assert list(got) == [1] and got[1] == (0.6, 0.5)  # median of 0.5/0.6/0.7 of the box
    assert shorts.face_centers(layout, faces, 400, 600)[1] == (0.2, 0.25)
    assert shorts.face_centers(layout, [face(1, 0.3, 0.5)], 0, 5) == {}
    two = [face(12, 0.76, 0.7), face(14, 0.88, 0.7)]  # an even count: between the middle two
    assert shorts.face_centers(layout, two, 10, 20)[1] == (0.4, 0.25)
    assert shorts.face_centers({"panels": [{"src": [0.7, 0.6, 0.3, 0.4]}]}, faces, 10, 20) == {}


def test_signature_changes_with_the_face_and_not_without(sc):
    layouts = {"cam": {"panels": [{"src": [0.7, 0.6, 0.3, 0.4], "focus": "face"}]}}
    base = shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 30.0, "cam"]], layouts)
    assert base == shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 30.0, "cam"]], layouts, [{}])
    assert base != shorts.short_signature(sc, "h264", 1.0, 31.0, [[0.0, 30.0, "cam"]], layouts,
                                          [{0: (0.6, 0.5)}])

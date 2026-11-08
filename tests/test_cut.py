from pytest import approx

from clipper import cut, jsonstate
from clipper.cut import compilation_parts


def seg(id, start, end, cut_start=None):
    s = {"id": id, "title": id.upper(), "start_s": start, "end_s": end}
    if cut_start is not None:
        s["cut_start_s"] = cut_start
    return s


def test_back_to_back_part_ends_where_the_next_file_begins():
    # b's keyframe lead-in starts at 195, inside a: a stops there instead of at 200.
    segs = [seg("a", 100, 200, 98), seg("b", 200, 300, 195), seg("c", 500, 600, 497)]
    spans, chapters, total = compilation_parts(segs)
    assert spans == [(98, 195), (195, 300), (497, 600)]
    assert [c["start_s"] for c in chapters] == [0, 97, 202]
    assert [c["title"] for c in chapters] == ["A", "B", "C"]
    assert total == approx(305)


def test_parts_out_of_stream_order_keep_their_own_ends():
    segs = [seg("late", 500, 600, 497), seg("early", 100, 200)]
    spans, chapters, total = compilation_parts(segs)
    assert spans == [(497, 600), (100, 200)]
    assert [c["start_s"] for c in chapters] == [0, 103]
    assert total == approx(203)


def test_part_offsets_are_exact():
    from clipper.cut import part_offsets
    spans = [(98.0, 195.04), (195.04, 300.0), (497.0, 600.0)]
    assert part_offsets(spans) == [[98.0, 195.04, 0.0], [195.04, 300.0, 97.04], [497.0, 600.0, 202.0]]


def test_an_unchanged_pick_is_not_cut_again(tmp_path):
    dst = tmp_path / "03_t03_x.mp4"
    p, c = {"id": "t03"}, {"in_s": 10.0, "out_s": 40.0}
    assert not cut.unchanged_pick(dst, p, c)  # nothing cut yet
    dst.write_bytes(b"x")
    jsonstate.write(dst.with_suffix(".json"), {"id": "t03", "in_s": 10.0, "out_s": 40.0})
    assert cut.unchanged_pick(dst, p, c)
    assert not cut.unchanged_pick(dst, p, {"in_s": 12.0, "out_s": 40.0})  # a re-score moved it

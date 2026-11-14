"""The segments stage (clipper/segments.py): the pure parts around the model call."""
from clipper import segments


def test_slug_is_a_safe_file_name():
    assert segments.slug("The Quartering: Jer's Big Day!") == "the-quartering-jer-s-big-day"
    assert len(segments.slug("x" * 80)) == 50 and segments.slug("  --  ") == ""


def test_apply_filters_says_why_a_segment_is_skipped():
    cfg = {"source_share": ["none", "some"], "skip_kinds": ["ad"], "min_s": 120, "max_s": 3600}
    segs = [{"standalone": True, "kind": "topic", "source_share": "none", "duration_s": 600},
            {"standalone": False, "kind": "ad", "source_share": "most", "duration_s": 30},
            {"standalone": True, "kind": "topic", "source_share": "some", "duration_s": 4000}]
    segments.apply_filters(segs, cfg)
    assert segs[0]["cut"] and segs[0]["skip_reasons"] == []
    assert not segs[1]["cut"]
    assert segs[1]["skip_reasons"] == ["not standalone", "kind ad", "source_share most", "0 min"]
    assert not segs[2]["cut"] and segs[2]["skip_reasons"] == ["67 min"]

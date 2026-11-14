from pathlib import Path

from clipper.meta import compilation_chapters, draft, make_srt

MC = {"srt_line_chars": 42, "srt_max_s": 6}


def w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_srt_cues_per_part_on_the_video_timeline():
    words = [w("Hello", 100.0, 100.5), w("there.", 100.6, 101.0), w("Next", 101.1, 101.5),
             w("cut", 104.8, 105.2),      # runs past the part's end: left out
             w("Second", 200.5, 201.0), w("part", 201.1, 201.5)]
    parts = [(100, 105, 0), (200, 203, 5)]   # the second part starts 5 s into the video
    assert make_srt(words, parts, MC) == (
        "1\n00:00:00,000 --> 00:00:01,000\nHello there.\n\n"
        "2\n00:00:01,100 --> 00:00:01,500\nNext\n\n"
        "3\n00:00:05,500 --> 00:00:06,500\nSecond part\n\n")


def test_srt_breaks_at_pauses_and_masks():
    words = [w("one", 0.0, 0.3), w("two", 2.0, 2.3)]   # 1.7 s pause
    srt = make_srt(words, [(0, 10, 0)], MC, mask=str.upper)
    assert "\nONE\n" in srt and "\nTWO\n" in srt
    assert srt.count("-->") == 2


def test_compilation_chapters_from_its_segments():
    comp = {"kind": "compilation", "id": "comp-x", "segments": ["s1", "s2"],
            "segment_titles": {"s1": "One", "s2": "Two"}, "parts": [(100, 400, 0.0), (900, 1200, 300.0)]}
    chapters = {"s1": [{"t": 0.0, "title": "start"}, {"t": 60.0, "title": "later"}]}
    drafted = {"s2": {"chapters": [{"t": 0.0, "title": "s"}, {"t": 30.0, "title": "drafted"}]}}
    compilation_chapters([comp], chapters, drafted)
    assert chapters["comp-x"] == [{"t": 0.0, "title": "One"}, {"t": 60.0, "title": "later"},
                                  {"t": 300.0, "title": "Two"}, {"t": 330.0, "title": "drafted"}]


def test_draft_from_the_model_output():
    x = {"kind": "segment", "id": "s1", "video": Path("segments/s1_a.mp4"), "title": "Old",
         "body": "cut body", "parts": [(10.0, 70.0, 0.0)]}
    g = {"title": " New " + "x" * 100, "lead": " Lead ", "body": "", "hashtags": [" #a", "#b", "#c", "#d"],
         "tags": ["t"], "playlist": " Bits "}
    mc = {"model": "m", "prompt_version": "v9"}
    d = draft(x, g, [], "credit", "", mc, mask=str.upper)
    assert d["title"].startswith("NEW X") and d["title_original"] == "Old"
    assert d["description"] == "LEAD\n\nCUT BODY\n\nCREDIT\n\n#A #B #C"
    assert d["hashtags"] == ["#a", "#b", "#c"] and d["playlist"] == "Bits"
    assert d["duration_s"] == 60.0 and d["srt"] == "s1_a.srt"
    assert d["problems"] == ["title 104 chars"]
    assert draft(x, g, [], "credit", "Pre-shows", mc)["playlist"] == "Pre-shows"

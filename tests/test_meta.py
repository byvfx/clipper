from clipper.meta import make_srt

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

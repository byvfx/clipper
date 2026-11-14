"""The transcribe stage (clipper/transcribe.py): the pure fix-ups on WhisperX's words. The
GPU, Modal and ffmpeg paths are not run here."""
from clipper import transcribe


def test_fill_missing_times_spreads_unaligned_words_between_their_neighbours():
    words = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "7"}, {"word": "%"},
             {"word": "b", "start": 4.0, "end": 5.0}]
    assert transcribe.fill_missing_times(words) == 2
    assert [(w["start"], w["end"]) for w in words[1:3]] == [(1.0, 2.5), (2.5, 4.0)]
    assert words[1]["interpolated"] is True and words[1]["score"] is None
    assert "interpolated" not in words[0]


def test_fill_missing_times_at_the_ends():
    tail = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "9"}]   # nothing after: a zero-length word
    assert transcribe.fill_missing_times(tail) == 1 and (tail[1]["start"], tail[1]["end"]) == (1.0, 1.0)
    head = [{"word": "#"}, {"word": "a", "start": 2.0, "end": 3.0}]   # nothing before: it sits where a starts
    assert transcribe.fill_missing_times(head) == 1 and (head[0]["start"], head[0]["end"]) == (2.0, 2.0)
    assert transcribe.fill_missing_times([]) == 0

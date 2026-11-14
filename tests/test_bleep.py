from pytest import approx

from clipper.bleep import Bleeper


def bleeper(**over):
    bc = {"mode": "bleep", "pad_s": 0.1, "tone_hz": 1000, "tone_level": 0.25,
          "patterns": [r"retard(?:s|ed)?"], "profanity": False,
          "profanity_patterns": [r"(?:mother)?f+u+c+k+\w*"], **over}
    return Bleeper(bc)


def test_hit_matches_whole_words_and_their_forms():
    b = bleeper()
    assert b.hit("Retarded,")
    assert b.hit("retard's")
    assert not b.hit("retardant")
    assert not b.hit("fucking")  # swearing only with profanity on
    assert bleeper(profanity=True).hit("Fucking!")


def test_on_needs_a_mode_and_patterns():
    assert bleeper().on
    assert bleeper(mode="mute").on
    assert not bleeper(mode="off").on
    assert not bleeper(patterns=[]).on


def test_mask_keeps_the_first_letter():
    assert bleeper().mask("you retard, really") == "you r*****, really"
    assert bleeper(patterns=[]).mask("you retard") == "you retard"


def test_intervals_pad_merge_and_shift_to_the_seek_point():
    words = [{"word": "retard", "start": 10.0, "end": 10.4},
             {"word": "retards", "start": 10.45, "end": 10.8},  # overlaps once padded
             {"word": "fine", "start": 11.0, "end": 11.2},
             {"word": "retarded", "start": 20.0, "end": 20.5},
             {"word": "retard", "start": 40.0, "end": 40.4}]   # past the range
    spans = bleeper().intervals(words, a=10.0, b=30.0, t0=9.95)
    # The first span would start before t0 (the seek point): it's clipped to 0.
    assert spans == [(0, approx(0.95)), (approx(9.95), approx(10.65))]

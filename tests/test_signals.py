"""The signals stage (clipper/signals.py): the per-second maths that turns chat and loudness
into a heat curve and candidate windows. Pure functions; no audio or files here."""
import re

import numpy as np

from clipper import signals


def test_shift_earlier_moves_a_reaction_onto_its_cause():
    x = np.array([0, 0, 0, 5, 0.0])
    assert signals.shift_earlier(x, 2).tolist() == [0, 5, 0, 0, 0]
    assert signals.shift_earlier(x, 0) is x


def test_soft_cap_keeps_the_order_and_stays_under_the_cap():
    z = np.array([0.5, 2.0, 8.0, 40.0])
    c = signals.soft_cap(z, 4.0)
    assert np.all(np.diff(c) > 0) and c.max() < 4.0 and abs(c[0] - 0.5) < 0.02


def test_mad_is_zero_for_a_flat_signal_and_scales_a_spread():
    assert signals.mad(np.ones(10)) == 0.0
    assert abs(signals.mad(np.array([1, 2, 3, 4, 5.0])) - 1.4826) < 1e-9


def test_per_second_counts_reads_emotes_reactions_and_clip_calls():
    react, clip = re.compile(r"lol|kek", re.I), re.compile(r"clip (it|that)", re.I)
    msgs = [{"t": 1.2, "user": "a", "content": "LOL"},
            {"t": 1.9, "user": "b", "content": "[emote:1:KEKW] wow"},   # emote names count as words
            {"t": 3.0, "user": "a", "content": "clip that"},
            {"t": 3.5, "user": "a", "content": ""}]
    total, users, reacts, clips = signals.per_second_counts(msgs, 5, react, clip)
    assert total.tolist() == [0, 2, 0, 2, 0] and users.tolist() == [0, 2, 0, 1, 0]
    assert reacts.tolist() == [0, 2, 0, 0, 0] and clips.tolist() == [0, 0, 0, 1, 0]


def test_pick_windows_merges_neighbours_and_keeps_the_top_n():
    heat = np.zeros(300)
    heat[100], heat[108], heat[250], heat[20] = 5, 4, 3, 1   # 20 is under min_heat
    sc = {"min_heat": 2, "min_gap_s": 5, "top_n": 2, "window_pre_s": 10, "window_post_s": 10, "max_window_s": 60}
    windows = signals.pick_windows(heat, sc)
    assert [w["peak_s"] for w in windows] == [100, 250]   # strongest first; 108 joined 100's window
    assert (windows[0]["start_s"], windows[0]["end_s"], windows[0]["peaks"]) == (90, 118, [100, 108])
    assert (windows[1]["start_s"], windows[1]["end_s"]) == (240, 260)
    sc["max_window_s"] = 20   # too small to merge: the second peak gets no window of its own past top_n
    assert [w["peaks"] for w in signals.pick_windows(heat, sc)] == [[100], [108]]

from datetime import UTC, datetime
from types import SimpleNamespace

from clipper.record import (
    Part,
    Recording,
    Segment,
    parse_media,
    source_map,
    source_time,
)

T = datetime(2026, 10, 28, 20, 0, tzinfo=UTC).timestamp()

PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:2.000,live
before-any-date.ts
#EXT-X-PROGRAM-DATE-TIME:2026-10-28T20:00:00.000Z
#EXTINF:2.000,live
seg101.ts
#EXTINF:2.000,live
seg102.ts
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-10-28T20:00:10.000Z
#EXTINF:1.5,ad
seg103.ts
"""


def test_parse_media():
    segs, ended, target = parse_media(PLAYLIST, "https://cdn.example/a/playlist.m3u8")
    # A segment before any PROGRAM-DATE-TIME can't be placed, but still takes a sequence number.
    assert [s.seq for s in segs] == [101, 102, 103]
    assert [s.pdt - T for s in segs] == [0, 2, 10]
    assert [s.disc for s in segs] == [False, False, True]
    assert [s.title for s in segs] == ["live", "live", "ad"]
    assert segs[0].url == "https://cdn.example/a/seg101.ts"
    assert (ended, target) == (False, 2.0)
    assert parse_media(PLAYLIST + "#EXT-X-ENDLIST\n", "https://cdn.example/")[1]


def have(seg, last_seq, last_end):
    fake = SimpleNamespace(st=SimpleNamespace(last_seq=last_seq, last_end=last_end))
    return Recording._have(fake, seg)


def test_have_by_sequence_and_reset():
    end = T + 100
    assert have(Segment(50, end - 2, 2, "", "", False), 50, end)
    assert not have(Segment(51, end, 2, "", "", False), 50, end)
    # A lower sequence with a later clock is a new broadcast, not a repeat.
    assert not have(Segment(3, end + 60, 2, "", "", False), 50, end)
    assert have(Segment(3, end - 40, 2, "", "", False), 50, end)


def test_have_without_a_sequence_uses_the_clock():
    end = T + 100
    assert not have(Segment(1, end, 2, "", "", False), -1, 0.0)
    assert have(Segment(1, end - 2, 2, "", "", False), -1, end)
    assert not have(Segment(1, end - 1, 2, "", "", False), -1, end)


def test_chat_time_maps_across_parts():
    parts = [Part("live/part001.ts", wall_start=T, duration_s=60),
             Part("live/part002.ts", wall_start=T + 100, duration_s=30)]
    m = source_map(parts)
    assert [p["src_start"] for p in m] == [0, 60]
    assert source_time(m, T + 10, 5) == 10
    assert source_time(m, T + 63, 5) == 63        # chat trailing the video
    assert source_time(m, T + 70, 5) is None      # inside the gap
    assert source_time(m, T + 105, 5) == 65
    assert source_time(m, T + 135, 5) == 95
    assert source_time(m, T + 136, 5) is None
    assert source_time(m, T - 1, 5) is None       # before the recording

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import clipper.record.recording as recording_mod
from clipper import media
from clipper.record import (
    LIVE_CHAT,
    STATE,
    Part,
    Recording,
    Segment,
    State,
    chat_row,
    finalize,
    hls,
    on_finish_args,
    parse_media,
    pick_variant,
    source_map,
    source_time,
)
from clipper.record.finalize import (
    _uncovered,
    join,
    make_meta,
    place_chat,
    run_on_finish,
)

finalize_mod = importlib.import_module("clipper.record.finalize")  # the name is the function

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


# ---------- hls ----------

def seg(seq, pdt, dur=2.0, disc=False):
    return Segment(seq, pdt, dur, f"https://cdn.example/{seq}.ts", "live", disc)


def test_place_next_gap_and_reset():
    end = T + 100
    assert hls.place(seg(1, T), -1, 0.0) == (0, 0.0)               # first of the recording
    assert hls.place(seg(51, end), 50, end) == (0, 0.0)            # the next one
    assert hls.place(seg(51, end + 0.4), 50, end) == (0, 0.0)      # clock jitter is no gap
    assert hls.place(seg(54, end + 6), 50, end) == (3, 6.0)        # three lost
    assert hls.place(seg(0, end + 45), 50, end) == (-1, 45.0)      # the broadcast restarted
    assert hls.place(seg(0, end - 5), 50, end) == (-1, 0.0)
    assert hls.place(seg(7, end + 3), -1, end) == (-1, 3.0)        # state from before last_seq


MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080
1080p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p-low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=100000,CODECS="mp4a.40.2"
audio/index.m3u8
"""


def test_pick_variant():
    base = "https://cdn.example/x/master.m3u8"
    assert pick_variant(MASTER, base, 0) == "https://cdn.example/x/1080p/index.m3u8"
    assert pick_variant(MASTER, base, 720) == "https://cdn.example/x/720p/index.m3u8"
    with pytest.raises(LookupError):
        pick_variant(MASTER, base, 480)


def test_chat_row():
    ev = {"event": "App\\Events\\ChatMessageEvent",
          "data": json.dumps({"id": "m1", "created_at": "2026-10-28T20:00:05Z", "type": "message",
                              "content": "hi", "sender": {"username": "andy"}})}
    row = chat_row({"push": {"channel": "chatrooms.1.v2", "pub": {"data": ev}}})
    assert {k: row[k] for k in ("id", "ts", "user", "type", "content")} == \
        {"id": "m1", "ts": T + 5, "user": "andy", "type": "message", "content": "hi"}
    assert chat_row({"push": {"pub": {"data": {"event": "App\\Events\\PinnedMessage"}}}}) is None
    assert chat_row({"id": 2, "subscribe": {}}) is None


# ---------- the video thread ----------

class FakeKick:
    """The media playlists in turn (the last one repeats); every segment downloads. Asks
    the recording to stop at poll stop_after."""

    def __init__(self, playlists, stop_after):
        self.playlists, self.stop_after, self.polls, self.resolves = playlists, stop_after, 0, 0
        self.s = SimpleNamespace(get=lambda url, timeout: SimpleNamespace(status_code=200,
                                                                          content=b"ts"))
        self.rec = None

    def channel(self, slug):
        self.resolves += 1
        return {"playback_url": "https://cdn.example/master.m3u8"}

    def text(self, url, timeout=15):
        if url.endswith("master.m3u8"):
            return 200, MASTER
        self.polls += 1
        if self.polls >= self.stop_after:
            self.rec.stop_evt.set()
        return 200, self.playlists[min(self.polls, len(self.playlists)) - 1]


class Evt:
    """stop_evt without the waiting."""

    def __init__(self):
        self.flag = False

    def set(self):
        self.flag = True

    def is_set(self):
        return self.flag

    def wait(self, s=None):
        return self.flag


def recording(tmp_path, defaults, playlists, stop_after, **rc):
    st = State(stream_id="kick-1", channel="kino", channel_id=1, user_id=1, chatroom_id=1,
               sessions=[1], title="t", stream_started_at="", max_height=1080)
    alerts = []
    rec = Recording(tmp_path, st, defaults, lambda text, *a, **k: alerts.append(text),
                    {**defaults["record"], **rc})
    rec.kick = FakeKick(playlists, stop_after)
    rec.kick.rec = rec
    rec.stop_evt = Evt()
    return rec, alerts


def playlist(first_seq, n, at):
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first_seq}",
             f"#EXT-X-PROGRAM-DATE-TIME:{datetime.fromtimestamp(at, UTC).isoformat()}"]
    for i in range(n):
        lines += ["#EXTINF:2.000,live", f"seg{first_seq + i}.ts"]
    return "\n".join(lines) + "\n"


def test_video_writes_parts_and_gaps(tmp_path, defaults):
    # 10..12, a repeat, then 13 and 14 never show up: 15.. starts part 2 after a 4 s gap.
    rec, alerts = recording(tmp_path, defaults, [playlist(10, 3, T), playlist(11, 2, T + 2),
                                                 playlist(15, 2, T + 10)], 4, stall_alert_s=600)
    rec._video()
    st = rec.st
    assert [(p.file, p.segments, p.wall_start - T) for p in st.parts] == \
        [("live/part001.ts", 3, 0), ("live/part002.ts", 2, 10)]
    assert st.gaps[0]["missing_segments"] == 2 and st.gaps[0]["missing_s"] == 4
    assert (st.last_seq, st.last_end - T) == (16, 14)
    assert (tmp_path / "live/part001.ts").read_bytes() == b"ts" * 3
    assert alerts == ["kick-1: video gap of 4 s (2 segment(s) lost); part 2 starts"]
    assert State.load(tmp_path / STATE).parts[1].segments == 2   # saved when the thread ends


def test_video_stall_alert_on_a_playlist_that_stops_moving(tmp_path, defaults, monkeypatch):
    """#77 M10: a 200 playlist with nothing new alerts too, and the playback is resolved again."""
    rec, alerts = recording(tmp_path, defaults, [playlist(10, 2, T)], 4, stall_alert_s=60)
    rec.st.last_seq, rec.st.last_end = 11, T + 4      # written already, before a restart
    clock = iter(range(0, 10**6, 100))                # every look at the clock: 100 s later
    monkeypatch.setattr(recording_mod, "_now", lambda: T + next(clock))
    monkeypatch.setattr(recording_mod, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    rec._video()
    stalls = [a for a in alerts if "no video" in a]
    assert len(stalls) == 1                           # once, until video is back
    assert stalls[0].endswith(" s (the playlist has no new segments)")
    assert rec.kick.resolves >= 2


# ---------- finalize ----------

def stream(tmp_path, n_parts, **st):
    (tmp_path / "live").mkdir()
    parts = []
    for i in range(n_parts):
        f = f"live/part{i + 1:03d}.ts"
        (tmp_path / f).write_bytes(b"ts")
        parts.append(Part(f, wall_start=T + 100 * i, duration_s=60, segments=30, bytes=2))
    s = State(stream_id="kick-1", channel="kino", channel_id=1, user_id=1, chatroom_id=1,
              sessions=[1], title="t", stream_started_at="", max_height=1080, parts=parts,
              chat=[[T - 10, T + 1000]], status="paused", **st)
    s.save(tmp_path)
    with open(tmp_path / LIVE_CHAT, "w", encoding="utf-8") as f:
        for i, ts in enumerate([T + 10, T + 70, T + 105, T - 5]):
            f.write(json.dumps({"id": f"m{i}", "ts": ts, "content": "x"}) + "\n")
    return s


@pytest.fixture
def no_ffprobe(monkeypatch):
    monkeypatch.setattr(media, "try_duration", lambda *a, **k: None)


def test_finalize_one_ts_part_runs_again(tmp_path, defaults, no_ffprobe):
    """#77 L17: one .ts part is moved to source.ts; a second finalize used to find no parts
    and mark the recording failed."""
    stream(tmp_path, 1)
    cfg = {**defaults, "record": {**defaults["record"], "container": "ts",
                                  "extract_audio": False, "backfill_chat": False}}
    alerts = []
    for _ in range(2):
        meta = finalize(tmp_path, cfg, lambda text, *a, **k: alerts.append(text))
        assert meta and meta["source"] == "source.ts" and meta["duration_s"] == 60
        assert State.load(tmp_path / STATE).status == "done"
        assert not (tmp_path / "live/part001.ts").exists()
        assert (tmp_path / "source.ts").read_bytes() == b"ts"
    assert not [a for a in alerts if "nothing was recorded" in a]
    rows = [json.loads(x) for x in (tmp_path / "chat.jsonl").read_text("utf-8").splitlines()]
    assert [(r["id"], r["t"]) for r in rows] == [("m0", 10)]   # m1 is past the source's end


def test_join_reuses_a_source_once_its_parts_are_gone(tmp_path, no_ffprobe):
    st = stream(tmp_path, 3, joined=["live/part001.ts", "live/part002.ts"])
    (tmp_path / "source.mkv").write_bytes(b"joined")
    for p in st.parts:
        (tmp_path / p.file).unlink()
    assert [p.file for p in join(tmp_path, st, "mkv")] == ["live/part001.ts", "live/part002.ts"]
    (tmp_path / "source.mkv").unlink()      # never joined after all: nothing left to join
    assert join(tmp_path, st, "mkv") == []


def test_join_notes_its_parts_before_joining(tmp_path, no_ffprobe, monkeypatch):
    st = stream(tmp_path, 2)
    st.parts[1].segments = 0                 # opened, never written to
    ran = []

    def ffmpeg(args, check):
        ran.append(args)
        assert State.load(tmp_path / STATE).joined == ["live/part001.ts"]
        Path(args[-1]).write_bytes(b"joined")

    monkeypatch.setattr(finalize_mod.subprocess, "run", ffmpeg)
    assert [p.file for p in join(tmp_path, st, "mkv")] == ["live/part001.ts"]
    assert ran and ran[0][0] == "ffmpeg"
    assert (tmp_path / "source.mkv").read_bytes() == b"joined"
    assert (tmp_path / "live/concat.txt").read_text() == "file 'part001.ts'\nduration 60\n"


def test_place_chat_across_parts():
    parts = [Part("live/part001.ts", wall_start=T, duration_s=60),
             Part("live/part002.ts", wall_start=T + 100, duration_s=30)]
    seen = {i: {"id": i, "ts": ts} for i, ts in
            [("b", T + 105), ("a", T + 10), ("gap", T + 80), ("tail", T + 63), ("early", T - 1),
             ("late", T + 140)]}
    rows = place_chat(seen, source_map(parts), 5, 90)
    assert [(r["id"], r["t"]) for r in rows] == [("a", 10), ("tail", 63), ("b", 65)]
    # chat past the end of the joined source is dropped
    assert [r["id"] for r in place_chat(seen, source_map(parts), 5, 64)] == ["a", "tail"]


def test_uncovered_chat_spans():
    assert _uncovered([[0, 10], [12, 20]], 0, 30, 5) == [(20, 30)]
    assert _uncovered([[0, 10], [20, 30]], 0, 30, 5) == [(10, 20)]
    assert _uncovered([], 0, 30, 5) == [(0, 30)]
    assert _uncovered([[5, 40]], 0, 30, 1) == [(0, 5)]


def test_meta_spans_parts_and_gaps():
    parts = [Part("live/part001.ts", wall_start=T, duration_s=60),
             Part("live/part002.ts", wall_start=T + 100, duration_s=30)]
    st = State(stream_id="kick-1", channel="kino", channel_id=9, user_id=1, chatroom_id=1,
               sessions=[5, 6], title="t", stream_started_at="x", max_height=1080, parts=parts,
               gaps=[{"at_wall": "", "missing_s": 40.0, "missing_segments": -1}])
    m = make_meta(st, source_map(parts), "source.mkv", 90.2, 7, 2, [(T + 5, T + 8)])
    assert (m["livestream_id"], m["duration_s"], m["source"]) == (5, 90.2, "source.mkv")
    assert datetime.fromisoformat(m["started_at"]).timestamp() == T
    r = m["recording"]
    assert datetime.fromisoformat(r["ended_at"]).timestamp() == T + 130
    assert (r["missing_video_s"], r["chat_live_messages"], r["chat_backfilled"]) == (40, 7, 2)
    assert r["chat_holes"][0][1] == 3


# ---------- on_finish ----------

def test_on_finish_args_keep_spaces_and_skip_the_shell():
    """#77 L6: values go into arguments after the split, never through a shell."""
    tpl = 'python -m tool --dir {stream_dir} --note "{channel} done" {stream_id}'
    assert on_finish_args(tpl, stream_id="kick-1", stream_dir="/data/my streams/kick-1",
                          channel="kino; rm -rf /") == \
        ["python", "-m", "tool", "--dir", "/data/my streams/kick-1", "--note",
         "kino; rm -rf / done", "kick-1"]


def test_on_finish_problems_are_alerts(tmp_path, monkeypatch):
    st = State(stream_id="kick-1", channel="kino", channel_id=1, user_id=1, chatroom_id=1,
               sessions=[1], title="t", stream_started_at="", max_height=1080)
    alerts = []

    def say(text, *a, **k):
        alerts.append(text)

    run_on_finish(tmp_path, st, {"on_finish": "notify {nope}"}, say)
    run_on_finish(tmp_path, st, {"on_finish": 'notify "unclosed'}, say)
    monkeypatch.setattr(finalize_mod.subprocess, "run",
                        lambda args, check: SimpleNamespace(returncode=2))
    run_on_finish(tmp_path, st, {"on_finish": "notify {stream_id}"}, say)
    assert len(alerts) == 3 and alerts[2] == "kick-1: on_finish exited 2: notify kick-1"


def test_finalize_skips_a_chat_line_cut_short(tmp_path, defaults, no_ffprobe):
    """Audit 11-03 #1: a container killed mid-write leaves a half line in live/chat.jsonl;
    finalize used to raise on it and leave the stream stuck."""
    stream(tmp_path, 1)
    with open(tmp_path / LIVE_CHAT, "ab") as f:
        f.write('{"id": "m9", "ts": 12, "content": "Ã©'.encode()[:-1])  # cut inside a UTF-8 character
    cfg = {**defaults, "record": {**defaults["record"], "container": "ts",
                                  "extract_audio": False, "backfill_chat": False}}
    alerts = []
    meta = finalize(tmp_path, cfg, lambda text, *a, **k: alerts.append(text))
    assert meta and State.load(tmp_path / STATE).status == "done"
    assert [a for a in alerts if "1 unreadable line" in a]

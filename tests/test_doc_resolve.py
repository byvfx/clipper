"""clipper/doc_resolve.py against a stand-in for Resolve's scripting objects: what it asks
Resolve to do, not whether Resolve does it (that needs Resolve open; wiki Documentaries)."""
import pytest

from clipper import doc, doc_resolve
from clipper.errors import ClipperError


class Item:
    def __init__(self, path="", fps="30"):
        self.path, self.fps, self.props, self.fades = path, fps, {}, None

    def GetClipProperty(self, key):
        return {"File Path": self.path, "FPS": self.fps}[key]


class Placed:
    def __init__(self, start, n):
        self.start, self.n, self.props, self.fades = start, n, {}, None

    def GetStart(self):
        return self.start

    def GetDuration(self):
        return self.n

    refuse = False

    def SetProperty(self, key, value):
        self.props[key] = value
        return not self.refuse

    def SetFades(self, fades):
        self.fades = fades
        return True


class Folder:
    def __init__(self, clips):
        self.clips = clips

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return []


class Timeline:
    def __init__(self, name):
        self.name, self.tracks, self.names, self.markers = name, {"video": 1, "audio": 1}, {}, []
        self.items: dict[tuple, list[Placed]] = {}

    def GetName(self):
        return self.name

    def SetStartTimecode(self, tc):
        return True

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return max((p.start + p.n for ps in self.items.values() for p in ps), default=0)

    def GetTrackCount(self, kind):
        return self.tracks[kind]

    def AddTrack(self, kind, sub=None):
        self.tracks[kind] += 1
        return True

    def SetTrackName(self, kind, i, name):
        self.names[kind, i] = name

    def AddMarker(self, *m):
        self.markers.append(m)

    def GetItemListInTrack(self, kind, i):
        return self.items.get((kind, i), [])


class Pool:
    def __init__(self, project, clips=(), drop=0):
        self.project, self.root, self.imported, self.drop = project, Folder(list(clips)), [], drop

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imported += paths
        self.root.clips += [Item(p) for p in paths if "refused" not in p]

    def CreateEmptyTimeline(self, name):
        self.project.tls.append(Timeline(name))
        return self.project.tls[-1]

    def AppendToTimeline(self, rows):
        tl, out = self.project.current, []
        for r in rows[:len(rows) - self.drop]:
            p = Placed(r["recordFrame"], r["endFrame"] - r["startFrame"])
            kinds = {None: ("video", "audio"), 1: ("video",), 2: ("audio",)}[r.get("mediaType")]
            for kind in kinds:
                tl.items.setdefault((kind, r["trackIndex"]), []).append(p)
            out.append(p)
        return out


class Project:
    def __init__(self, name="Piece", timelines=(), clips=(), drop=0, rate="30"):
        self.name, self.tls, self.current, self.rate = name, [Timeline(n) for n in timelines], None, rate
        self.pool = Pool(self, clips, drop)

    def GetSetting(self, key):
        return {"timelineFrameRate": self.rate}[key]

    def GetName(self):
        return self.name

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.tls)

    def GetTimelineByIndex(self, i):
        return self.tls[i - 1]

    def SetCurrentTimeline(self, tl):
        self.current = tl


class Resolve:
    def __init__(self, project):
        self.project = project

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project

    def SaveProject(self):
        return True


def a_build():
    return {
        "title": "The Lighthouse", "fps": 30, "total": 300,
        "sync": [{"clip": "reel_a", "start": 300, "end": 420, "record": 0, "track": 1, "gain": 2.5}],
        "v1": [{"clip": "reel_b", "start": 0, "end": 180, "record": 120, "track": 1, "type": doc.VIDEO}],
        "v2": [], "v3": [],
        "audio": [{"clip": "vo_take", "start": 0, "end": 170, "record": 123, "track": doc.A_VO, "type": doc.AUDIO,
                   "gain": 5.0, "fade_in": 2, "fade_out": 6}],
        "markers": [{"frame": 0, "color": "Red", "name": "OPEN", "note": "Section start", "duration": 1}],
        "media": {"reel_a": "//nas/p/reels/reel_a.mp4", "reel_b": "//nas/p/reels/reel_b.mp4",
                  "vo_take": "//nas/p/vo/vo_take.wav", "unused": "//nas/p/reels/unused.mp4"},
    }


def test_versions_count_up_from_the_highest():
    assert doc_resolve.next_name("Piece", []) == "Piece v01"
    assert doc_resolve.next_name("Piece", ["Piece v01", "Piece v07", "Piece v07 copy", "Other v40"]) == "Piece v08"
    assert doc_resolve.next_name("A (b)", ["A (b) v02"]) == "A (b) v03"


def test_a_build_becomes_a_new_timeline(capsys):
    have = Item(r"\\NAS\p\reels\reel_a.mp4")     # in the pool already, under Windows' spelling
    project = Project(timelines=["The Lighthouse v01"], clips=[have])
    name = doc_resolve.build(Resolve(project), a_build(), "Piece")
    assert name == "The Lighthouse v02" and len(project.tls) == 2      # v01 is left alone
    tl = project.current
    assert sorted(p.replace("\\", "/") for p in project.pool.imported) == ["//nas/p/reels/reel_b.mp4", "//nas/p/vo/vo_take.wav"]
    assert tl.tracks == {"video": 3, "audio": 7} and tl.names["audio", 2] == "Narration"
    clip, = tl.items["audio", 1]
    assert clip.props == {"AudioVolume": 2.5} and clip.n == 120
    vo, = tl.items["audio", 2]
    assert vo.props == {"AudioVolume": 5.0} and vo.fades == {"FadeIn": 2, "FadeOut": 6}
    assert [p.start for p in tl.items["video", 1]] == [0, 120] and len(tl.markers) == 1
    assert "0 rows not where planned" in capsys.readouterr().out


def test_a_build_resolve_only_half_places_is_an_error():
    with pytest.raises(ClipperError, match="placed 0 of 1 sync rows"):
        doc_resolve.build(Resolve(Project(drop=1)), a_build())
    with pytest.raises(ClipperError, match="plan.toml's project is 'Other'"):
        doc_resolve.build(Resolve(Project()), a_build(), "Other")
    b = a_build()
    b["media"]["reel_b"] = "//nas/p/reels/refused.mp4"
    with pytest.raises(ClipperError, match="didn't import: reel_b"):
        doc_resolve.build(Resolve(Project()), b)


def test_a_project_at_another_frame_rate_is_refused_before_anything_is_placed():
    for rate in ("24", "29.97", "", None):
        project = Project(rate=rate)
        with pytest.raises(ClipperError, match="timeline frame rate is .*; the plan is built at 30 fps"):
            doc_resolve.build(Resolve(project), a_build())
        assert project.tls == [] and project.pool.imported == []
    doc_resolve.build(Resolve(Project(rate=30.0)), a_build())


def test_a_refused_gain_fails_the_build(monkeypatch):
    monkeypatch.setattr(Placed, "refuse", True)
    with pytest.raises(ClipperError, match="Resolve refused 2 gains or fades"):
        doc_resolve.build(Resolve(Project()), a_build())


def test_source_frames_follow_the_reels_own_rate():
    row = {"start": 300, "end": 420, "type": doc.VIDEO}
    assert doc_resolve.source_frames(row, 30, Item(fps="30")) == (300, 420)
    assert doc_resolve.source_frames(row, 30, Item(fps="60")) == (600, 840)
    assert doc_resolve.source_frames(row, 30, Item(fps="")) == (300, 420)
    assert doc_resolve.source_frames({**row, "type": doc.AUDIO}, 30, Item(fps="60")) == (300, 420)


def test_where_resolves_scripting_is_looked_for(monkeypatch):
    monkeypatch.delenv("RESOLVE_SCRIPT_API", raising=False)
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)
    assert doc_resolve.api_paths({"resolve_api": "X:/api", "resolve_lib": "X:/lib"}) == ("X:/api", "X:/lib")
    monkeypatch.setenv("RESOLVE_SCRIPT_API", "E:/env")
    assert doc_resolve.api_paths({"resolve_api": "X:/api", "resolve_lib": ""})[0] == "E:/env"
    with pytest.raises(ClipperError, match="scripting module isn't at"):
        doc_resolve.connect({"resolve_api": "", "resolve_lib": ""})

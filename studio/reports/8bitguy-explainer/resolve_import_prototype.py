# Import the explainer OTIO into the "8-Bit Guy explainer" project, check it, render a check copy.
import sys, time, DaVinciResolveScript as d
otio, media, outdir = sys.argv[1:4]
r = d.scriptapp("Resolve"); pm = r.GetProjectManager()
p = pm.GetCurrentProject()
if p.GetName() != "8-Bit Guy explainer":
    p = pm.LoadProject("8-Bit Guy explainer")
mp = p.GetMediaPool()
name = sys.argv[4] if len(sys.argv) > 4 else "8-Bit Guy explainer v1"
tl = mp.ImportTimelineFromFile(otio, {"timelineName": name, "importSourceClips": True, "sourceClipsPath": media})
print("imported:", bool(tl))
if not tl: sys.exit(1)
p.SetCurrentTimeline(tl)
print("frames", tl.GetStartFrame(), tl.GetEndFrame(), "res", tl.GetSetting("timelineResolutionWidth"), tl.GetSetting("timelineResolutionHeight"), tl.GetSetting("timelineFrameRate"))
for kind in ("video", "audio"):
    for i in range(1, tl.GetTrackCount(kind) + 1):
        its = tl.GetItemListInTrack(kind, i)
        off = [x.GetName() for x in its if not x.GetMediaPoolItem()]
        print(f" {kind} {i} {tl.GetTrackName(kind, i)!r}: {len(its)} items", "OFFLINE " + str(off) if off else "",
              [(x.GetStart(), x.GetDuration(), x.GetLeftOffset()) for x in its][:3])
print("markers", len(tl.GetMarkers()))
p.SetCurrentRenderFormatAndCodec("mp4", "H264")
print("render settings", p.SetRenderSettings({"SelectAllFrames": True, "TargetDir": outdir, "CustomName": sys.argv[5] if len(sys.argv) > 5 else "resolve_check",
                                               "FormatWidth": 1080, "FormatHeight": 1920, "AudioCodec": "aac", "AudioSampleRate": 48000}))
job = p.AddRenderJob(); print("job", job, p.StartRendering(job))
while p.IsRenderingInProgress(): time.sleep(1)
print(p.GetRenderJobStatus(job))
pm.SaveProject()

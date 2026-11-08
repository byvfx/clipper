import json

import pytest

from clipper import worker

SLOW = ["signals", "transcribe", "score", "segments", "cut", "meta", "thumbs", "shorts", "review"]


def cfg(fast_lane=True, stages=SLOW):
    return {"worker": {"stages": list(stages), "fast_lane": fast_lane, "notify": True}}


def test_plan_fast_lane(tmp_path):
    c = cfg()
    assert worker.plan(c, tmp_path, lane="fast") == [
        "signals", "transcribe", "score", "segments",
        "clips", "clips_meta", "clips_thumbs", "shorts", "clips_review"]
    assert worker.plan(c, tmp_path, lane="slow") == ["cut", "meta", "thumbs", "review"]
    assert worker.plan(c, tmp_path, ["meta", "cut"]) == ["cut", "meta"]


def test_faces_runs_after_the_fast_lane_before_the_slow_cut(tmp_path):
    with_faces = [*SLOW[:4], "faces", *SLOW[4:]]
    assert worker.plan(cfg(stages=with_faces), tmp_path, lane="slow") == ["faces", "cut", "meta", "thumbs", "review"]
    assert "faces" not in worker.plan(cfg(stages=with_faces), tmp_path, lane="fast")
    off = worker.plan(cfg(fast_lane=False, stages=with_faces), tmp_path)
    assert off.index("segments") < off.index("faces") < off.index("cut") < off.index("thumbs")


def test_plan_without_fast_lane_has_no_clips_stages(tmp_path):
    assert not [s for s in worker.plan(cfg(fast_lane=False), tmp_path) if s.startswith("clips")]


def test_plan_without_score_has_no_clips_stages(tmp_path):
    stages = [s for s in SLOW if s != "score"]
    assert "clips" not in worker.plan(cfg(stages=stages), tmp_path)


def test_preshow_uses_its_own_stages(tmp_path, defaults):
    (tmp_path / "meta.json").write_text(json.dumps({"kind": "preshow"}), encoding="utf-8")
    assert worker.plan(defaults, tmp_path) == defaults["preshow"]["stages"]


def test_stage_args():
    on, off = cfg()["worker"], cfg(fast_lane=False)["worker"]
    assert worker.stage_args("clips", "s", on) == ["cut", "s", "picks"]
    assert worker.stage_args("cut", "s", on) == ["cut", "s", "segments", "compilations"]
    assert worker.stage_args("cut", "s", off) == ["cut", "s"]
    assert worker.stage_args("clips_meta", "s", on) == ["meta", "s", "--missing"]
    assert worker.stage_args("meta", "s", off) == ["meta", "s"]
    assert worker.stage_args("transcribe", "s", on) == ["transcribe", "s", "--force"]
    assert worker.stage_args("clips_review", "s", on) == ["review", "publish", "s"]
    assert worker.stage_args("review", "s", {**on, "notify": False}) == [
        "review", "publish", "s", "--no-notify"]


def test_cut_before():
    on, off = cfg()["worker"], cfg(fast_lane=False)["worker"]
    assert worker.cut_before("clips_meta", on) == ("clips", ("clips",))
    assert worker.cut_before("shorts", on) == ("clips", ("clips",))
    assert worker.cut_before("meta", on) == ("cut", ("segments", "compilations"))
    assert worker.cut_before("meta", off) == ("cut", ("segments", "compilations", "clips"))
    assert worker.cut_before("transcribe", on) is None


def test_without_fast_lane_shorts_follow_the_cut(tmp_path):
    """B17: Shorts are cut from clips/, which only the slow cut makes then."""
    order = worker.plan(cfg(fast_lane=False), tmp_path)
    assert order.index("shorts") == order.index("cut") + 1
    assert order[-1] == "review"


def test_an_empty_cut_folder_is_not_done(tmp_path):
    """B14: cut makes segments/ before cutting anything; a crashed cut isn't done."""
    run = worker.Run(tmp_path, cfg()["worker"])
    (tmp_path / "segments").mkdir()
    (tmp_path / "clips").mkdir()
    assert not run.done("cut") and not run.done("clips")
    (tmp_path / "segments" / "s01_x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "clips" / "01_x_y.json").write_text("{}", encoding="utf-8")
    assert run.done("cut") and run.done("clips")


def wcfg():
    return {**cfg()["worker"], "stale_minutes": 10}


def test_heartbeat_survives_any_error(tmp_path, monkeypatch):
    """#77 L11: a failed beat is logged and the next one retries; the thread never dies."""
    run = worker.Run(tmp_path, wcfg())
    run.acquire()
    run._stop.set()  # no background beats: drive them by hand
    monkeypatch.setattr(worker, "write_json", lambda p, d: (_ for _ in ()).throw(RuntimeError("x")))
    assert run.beat() is True
    monkeypatch.undo()
    assert run.beat() is True
    run.release()
    assert not (tmp_path / ".run.lock").exists()


def test_release_after_a_takeover_leaves_the_new_holder_alone(tmp_path):
    """#77 L11: the old holder neither deletes the new lock nor saves over run.json."""
    old = worker.Run(tmp_path, wcfg())
    old.acquire()
    old._stop.set()
    (tmp_path / ".run.lock").write_text("other:1", encoding="utf-8")
    worker.write_json(tmp_path / "run.json", {"worker": "other:1", "stages": {}})
    assert old.beat() is False and old.lost
    assert old.save() is False  # a stage finishing after the takeover writes nothing either
    old.release()
    assert (tmp_path / ".run.lock").read_text(encoding="utf-8") == "other:1"
    assert worker.read_json(tmp_path / "run.json")["worker"] == "other:1"


def test_a_takeover_is_serialized(tmp_path, monkeypatch):
    """#77 L11 (Copilot on #106): the stale check, takeover and first heartbeat happen under
    the takeover lock, so a second taker finds a fresh heartbeat and gets Busy."""
    (tmp_path / ".run.lock").write_text("gone:1", encoding="utf-8")
    worker.write_json(tmp_path / "run.json", {"worker": "gone:1", "heartbeat": "2026-01-01T00:00:00+00:00"})
    seen = []
    real_read = worker.read_json
    monkeypatch.setattr(worker, "read_json",
                        lambda p: seen.append((tmp_path / ".run.lock.lock").exists()) or real_read(p))
    first = worker.Run(tmp_path, wcfg())
    seen.clear()
    first.acquire()
    first._stop.set()
    assert seen and all(seen)  # every read of the old heartbeat was under the takeover lock
    with pytest.raises(worker.Busy):
        worker.Run(tmp_path, wcfg()).acquire()
    assert first.holds() and not (tmp_path / ".run.lock.lock").exists()


def test_snapshot_retries_a_dict_changing_size(monkeypatch):
    calls = []
    real = worker.copy.deepcopy

    def flaky(d):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return real(d)
    monkeypatch.setattr(worker.copy, "deepcopy", flaky)
    assert worker.snapshot({"a": 1}) == {"a": 1} and len(calls) == 2


def test_every_stage_has_a_cli_command():
    from clipper import stages
    for st in stages.STAGES:
        for wc in ({"fast_lane": True, "notify": True}, {"fast_lane": False, "notify": False}):
            args = stages.argv(st.name, "S", wc)
            assert "S" in args and args[0] in {"signals", "transcribe", "score", "segments", "cut",
                                               "meta", "thumbs", "shorts", "review", "faces", "cast"}


def test_run_from_follows_the_planned_order():
    """`run --from cut` without the fast lane redoes Shorts (cut from cut's clips);
    `--from shorts` doesn't recut."""
    from clipper import stages
    off, on = cfg(fast_lane=False)["worker"], cfg()["worker"]
    assert stages.from_stage("cut", off)[:2] == ["cut", "shorts"]
    assert "cut" not in stages.from_stage("shorts", off)
    assert stages.from_stage("cut", on) == ["cut", "meta", "thumbs", "review"]
    assert stages.from_stage("shorts", on)[0] == "shorts" and "cut" in stages.from_stage("shorts", on)


def test_run_passes_a_show_pin_to_its_stages(tmp_path, defaults, monkeypatch):
    """`clipper --show c404 run <stream>`: each stage child gets --show c404 too."""
    from clipper import config
    cfg = {**defaults, "shows": {"c404": {"channels": ["don"], "overrides": {}}}}
    calls = []
    monkeypatch.setattr(worker.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(worker, "keep_awake", lambda on: None)
    assert worker.run_stream(config.pin_show(cfg, "c404"), tmp_path, stages=["signals"], force=True)
    assert calls[0][3:7] == ["--show", "c404", "signals", str(tmp_path)]
    calls.clear()
    worker.run_stream(cfg, tmp_path, stages=["signals"], force=True)
    assert "--show" not in calls[0]


def test_face_framed_shorts_render_again_after_the_face_scan(tmp_path, defaults, monkeypatch):
    """Copilot on #164: the fast lane's Shorts come before `faces`, so a focus = "face"
    panel kept its middle; the slow lane renders them again once faces.json is there."""
    cam = {"panels": [{"src": [0.7, 0.6, 0.3, 0.4], "focus": "face"}]}
    c = {**defaults, "worker": {**defaults["worker"], "fast_lane": True,
                                "stages": [*SLOW[:4], "faces", *SLOW[4:]]},
         "shorts": {**defaults["shorts"], "layouts": {"cam": cam}}}
    calls = []
    monkeypatch.setattr(worker.subprocess, "call", lambda cmd, **kw: calls.append(cmd[3]) or 0)
    monkeypatch.setattr(worker, "keep_awake", lambda on: None)
    (tmp_path / "shorts.json").write_text("{}")  # the fast lane rendered them
    assert worker.run_stream(c, tmp_path, lane="slow")
    assert calls[:2] == ["faces", "shorts"]
    calls.clear()
    assert worker.run_stream(c, tmp_path, lane="slow")  # faces done before: no second pass
    assert "shorts" not in calls
    plain = {**c, "shorts": {**c["shorts"], "layouts": {"cam": {"panels": [{"src": [0, 0, 1, 1]}]}}}}
    assert not worker.shorts_after_faces(plain, tmp_path, worker.default_stages(plain, tmp_path),
                                         worker.plan(plain, tmp_path, lane="slow"))

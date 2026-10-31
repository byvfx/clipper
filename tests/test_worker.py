import json

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


def test_every_stage_has_a_cli_command():
    from clipper import stages
    for st in stages.STAGES:
        for wc in ({"fast_lane": True, "notify": True}, {"fast_lane": False, "notify": False}):
            args = stages.argv(st.name, "S", wc)
            assert "S" in args and args[0] in {"signals", "transcribe", "score", "segments", "cut",
                                               "meta", "thumbs", "shorts", "review"}


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

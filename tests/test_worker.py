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

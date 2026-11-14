"""`--dry-run` estimates: score, segments, meta and thumbs (what a run would send, priced, nothing
called), and archive transcribe and investigate facts with their per-call overhead (#228)."""
import json

import pytest

from clipper import cli, jsonstate, llm, meta, segments
from clipper.prompts import Prompts
from clipper.thumbs import stage


def test_estimate_lines_price_by_the_table_and_say_when_a_model_is_not_in_it(defaults):
    rows = [("x", "claude-sonnet-5-5", 2, 100_000, 1_000), ("y", "openai:some-model", 1, 1_000, 10)]
    lines = llm.estimate_lines(defaults, rows)
    assert "~$ 0.21" in lines[0] and "2 calls" in lines[0]
    assert "counted $0" in lines[1]
    assert lines[-1].startswith("  total ~$0.21")


def test_thumbs_estimate_counts_frames_per_kind(defaults):
    tc = {**defaults["thumbnail"]}
    todo = [{"kind": "clip"}, {"kind": "segment"}]
    lines = stage.estimate(defaults, tc, todo, "classic")
    frames = tc["clip_frames"] + tc["even_frames"] + tc["heat_frames"]
    assert "covers" in lines[0] and "2 calls" in lines[0] and f"{frames} candidate frames" in lines[-1]
    assert stage.estimate(defaults, tc, [], "classic") == ["  every video has its cover (no --force): no model call"]


def test_meta_estimate_is_one_call_plus_chapters_minus_the_checkpoint(defaults, tmp_path):
    mc = defaults["meta"]
    prompts = Prompts(defaults)
    prompts.vars.update(show_name="The Show", min_chapters=mc["min_chapters"], max_chapters=mc["max_chapters"])
    words = [{"start": 0.0, "end": 1.0, "word": "hello", "seg": 0}]
    seg = {"id": "s01", "title": "t", "body": "b", "parts": [(0.0, 1.0, "x")]}
    assets = [seg, {"id": "c01"}]
    lines = meta.estimate(tmp_path, defaults, mc, prompts, assets, ["<asset id=s01/>"], [seg], words)
    assert "metadata" in lines[0] and "chapters" in lines[1] and lines[-1].startswith("  total")
    # A run that failed after its calls (#75) left them in the checkpoint: not paid again, not counted.
    (tmp_path / "metadata").mkdir()
    jsonstate.write(tmp_path / "metadata" / f".checkpoint_{mc['prompt_version']}.json",
                    {"ids": ["c01", "s01"], "gen": {"assets": []}, "chapters": {"s01": []}})
    lines = meta.estimate(tmp_path, defaults, mc, prompts, assets, ["<asset id=s01/>"], [seg], words)
    assert any("nothing to pay" in x for x in lines) and not any("~$" in x for x in lines)


def test_estimate_lines_never_go_online_for_a_price(defaults, monkeypatch):
    """An openrouter: model not in [llm] prices counts $0 instead of fetching OpenRouter's list."""
    monkeypatch.setattr(llm, "openrouter_prices", lambda: pytest.fail("the estimate went online"))
    lines = llm.estimate_lines(defaults, [("x", "openrouter:acme/some-model", 1, 1_000, 10)])
    assert "counted $0" in lines[0]


def test_segments_estimate_without_a_split_costs_nothing(defaults, tmp_path):
    lines = segments.estimate(tmp_path, defaults, Prompts(defaults), split=False)
    assert lines[0].startswith("  segments.json is kept") and "compile" not in lines[0]


@pytest.mark.parametrize("argv", [["segments", "kick-1", "--dry-run"], ["meta", "kick-1", "--dry-run"],
                                  ["thumbs", "kick-1", "--dry-run"], ["score", "kick-1", "--dry-run"]])
def test_the_four_stages_take_dry_run(argv):
    assert cli.build_parser().parse_args(argv).dry_run is True


# ---------- #228: archive transcribe and investigate facts, per-call overhead ----------

@pytest.fixture
def fake_modal(monkeypatch):
    """modal_app needs the modal package, which the tests don't install: its price tables only."""
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "clipper.modal_app", SimpleNamespace(
        SPEED={"L4": 24.5, "L40S": 46.5}, DIARIZE_SPEED=59.0,
        GPU_PRICES={"T4": 0.59, "L4": 0.8, "A10": 1.1, "L40S": 1.95, "A100": 2.1, "H100": 3.95}))


def test_each_call_costs_its_overhead_on_top_of_the_audio(fake_modal):
    from clipper import archive

    assert archive.estimate_usd(0, "L4", calls=60, overhead_s=60) == pytest.approx(0.8)   # an hour of overhead
    assert archive.estimate_usd(24.5, "L4") == pytest.approx(0.8)                         # an hour of work


def runs_log(defaults, tmp_path, subject, n, call_s, audio_s=60.0, gpu="NVIDIA L4"):
    defaults["paths"]["modal_runs"] = tmp_path / "modal_runs.jsonl"
    with open(defaults["paths"]["modal_runs"], "a", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"event": "end", "ok": True, "what": f"{subject}/ig-{i}", "call_s": call_s,
                                "audio_s": audio_s, "gpu": gpu}) + "\n")


def test_overhead_comes_from_the_subjects_own_runs_once_it_has_enough(defaults, tmp_path, fake_modal):
    from clipper import archive

    runs_log(defaults, tmp_path, "cecil", archive.OWN_RUNS, 5.0, gpu="NVIDIA L40S")   # another GPU: not these
    runs_log(defaults, tmp_path, "cecil", archive.OWN_RUNS - 1, 20.0, audio_s=49.0)
    assert archive.call_overhead_s(defaults, "cecil", "L4") == (archive.CALL_OVERHEAD_S, "typical")
    runs_log(defaults, tmp_path, "cecil", 1, 20.0, audio_s=49.0)                      # 2 s of work at 24.5x
    over, source = archive.call_overhead_s(defaults, "cecil", "L4")
    assert over == pytest.approx(20 + 30 - 2) and "cecil's last 20 runs on L4" in source


def test_a_run_is_matched_to_the_gpu_it_asked_for(fake_modal):
    """Copilot on #249: device names beyond "NVIDIA L4"; the logged sku wins when there is one."""
    from clipper import archive

    names = {"NVIDIA L4": "L4", "NVIDIA L40S": "L40S", "Tesla T4": "T4", "NVIDIA A10": "A10",
             "NVIDIA A100-SXM4-40GB": "A100", "NVIDIA H100 80GB HBM3": "H100", "Some New GPU": ""}
    assert {n: archive.gpu_sku({"gpu": n}) for n in names} == names
    assert archive.gpu_sku({"gpu": "NVIDIA L4", "sku": "L4"}) == "L4"


def test_the_newest_runs_count_even_after_a_backfill(defaults, tmp_path, fake_modal):
    """A backfill appends old runs after new ones: the newest by time are the ones used."""
    from clipper import archive

    defaults["paths"]["modal_runs"] = tmp_path / "modal_runs.jsonl"
    with open(defaults["paths"]["modal_runs"], "a", encoding="utf-8") as f:
        for i, (at, call_s) in enumerate([("2026-11-10T10:00:00", 10.0)] * 500 + [("2026-01-01T10:00:00", 99.0)] * 500):
            f.write(json.dumps({"event": "end", "ok": True, "what": f"s/ig-{i}", "call_s": call_s, "at": at,
                                "audio_s": 0.0, "gpu": "NVIDIA L4"}) + "\n")
    over, _ = archive.call_overhead_s(defaults, "s", "L4")
    assert over == pytest.approx(10 + 30)


def test_unknown_lengths_are_said_not_priced(defaults, tmp_path, fake_modal):
    from clipper import archive

    defaults["paths"]["modal_runs"] = tmp_path / "none.jsonl"
    defaults["archive"]["diarize"] = False
    line = archive.transcribe_estimate(defaults, "s", [{"duration_s": 0}] * 3, "L4", 2)
    assert "lengths unknown" in line and "no price" in line and "at least ~$0.04" in line
    line = archive.transcribe_estimate(defaults, "s", [{"duration_s": 3600}, {"duration_s": None}], "L4", 2)
    assert "1.0 h" in line and "1 more of unknown length, not priced" in line


def test_facts_estimate_counts_the_prompt_every_call(defaults, monkeypatch):
    from clipper import investigate

    monkeypatch.setattr(investigate, "sample_rate", lambda *a: 15.0)
    one, calls, usd = investigate.facts_estimate(defaults, "s", [{"duration_s": 60}])
    assert calls == 1 and one > 15 * 60 / 4 + investigate.STRUCTURED_TOKENS
    many, calls, usd2 = investigate.facts_estimate(defaults, "s", [{"duration_s": 60}] * 10)
    assert calls == 10 and many == pytest.approx(one * 10) and usd2 == pytest.approx(usd * 10)
    _, calls, _ = investigate.facts_estimate(defaults, "s", [{"duration_s": defaults["investigate"]["chunk_s"] * 2.5}])
    assert calls == 3

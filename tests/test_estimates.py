"""`--dry-run` on score, segments, meta and thumbs: what a run would send, priced, nothing called."""
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

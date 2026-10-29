"""The pipeline's stages, one row each: what `clipper run` and the worker run, in order.

A stage is a `clipper <command>` run on one stream. Clips first ([worker] fast_lane): cut,
meta, thumbs and review each have a fast-lane twin (clips, clips_meta, clips_thumbs,
clips_review) that does only the picks and their Shorts, so they reach review hours before
the long segments and compilations are cut. Without the fast lane the twins don't run and
the slow stages do everything.

Adding a stage: a row here (and its `clipper <command>` in cli.py). The worker, `clipper
run --from/--only`, the war room's pipeline panel and the done markers all read this table.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    cmd: tuple[str, ...]            # clipper argv; "{stream}" is the stream directory
    done: tuple[str, ...]           # outputs that show it ran before run.json existed (globs)
    fast: bool = False              # in the fast lane: picks and Shorts first
    twin_of: str = ""               # the slow stage this one is the fast-lane twin of
    after_cut: str = ""             # the cut stage whose videos it needs (skipped if it made none)
    fast_cmd: tuple[str, ...] = ()  # its argv when the fast lane is on, if different


S = "{stream}"
STAGES = (
    Stage("signals", ("signals", S), ("windows.json",), fast=True),
    Stage("transcribe", ("transcribe", S, "--force"), ("words.jsonl",), fast=True),  # run only when due
    Stage("score", ("score", S), ("candidates.json",), fast=True),
    Stage("segments", ("segments", S), ("segments.json",), fast=True),
    Stage("clips", ("cut", S, "picks"), ("clips/*.json",), fast=True, twin_of="cut"),
    Stage("clips_meta", ("meta", S, "--missing"), ("metadata/clips",), fast=True, twin_of="meta",
          after_cut="clips"),
    Stage("clips_thumbs", ("thumbs", S, "--missing"), ("clips/*.jpg",), fast=True, twin_of="thumbs",
          after_cut="clips"),
    # Shorts are cut from the clips: the fast lane's, or the slow cut's when it's off.
    Stage("shorts", ("shorts", S), ("shorts.json",), fast=True, after_cut="cut"),
    Stage("clips_review", ("review", "publish", S), ("review_state.json",), fast=True,
          twin_of="review", after_cut="clips"),
    # Only what has no draft/cover yet with the fast lane: the clips in review keep theirs.
    Stage("cut", ("cut", S), ("segments/*.json",),
          fast_cmd=("cut", S, "segments", "compilations")),
    Stage("meta", ("meta", S), ("metadata/segments",), after_cut="cut",
          fast_cmd=("meta", S, "--missing")),
    Stage("thumbs", ("thumbs", S), ("segments/*.jpg",), after_cut="cut",
          fast_cmd=("thumbs", S, "--missing")),
    Stage("review", ("review", "publish", S), ("review/s[0-9]*.mp4",), after_cut="cut"),
)
BY_NAME = {s.name: s for s in STAGES}
NAMES = [s.name for s in STAGES]
FAST_LANE = [s.name for s in STAGES if s.fast]
FAST_TWIN = {s.twin_of: s.name for s in STAGES if s.twin_of}   # slow stage -> its twin


def argv(name: str, stream: str, wc: dict) -> list[str]:
    """The `clipper` arguments that run stage `name` on `stream` ([worker] settings wc)."""
    st = BY_NAME[name]
    cmd = st.fast_cmd if wc["fast_lane"] and st.fast_cmd else st.cmd
    out = [stream if a == S else a for a in cmd]
    if out[:2] == ["review", "publish"] and not wc["notify"]:
        out.append("--no-notify")
    return out


def cut_before(name: str, wc: dict) -> tuple[str, tuple[str, ...]] | None:
    """The cut stage `name` waits on, and the folders that cut fills."""
    cut = BY_NAME[name].after_cut
    if name == "shorts" and wc["fast_lane"]:
        cut = "clips"
    if not cut:
        return None
    if cut == "clips":
        return cut, ("clips",)
    return cut, ("segments", "compilations") + (() if wc["fast_lane"] else ("clips",))


def order(names, wc: dict) -> list[str]:
    """`names` in pipeline order, each one after the cut it waits on (#B17: without the
    fast lane, Shorts come after the slow cut that makes their clips)."""
    out = [n for n in NAMES if n in names]
    for n in list(out):
        before = cut_before(n, wc)
        if before and before[0] in out and out.index(before[0]) > out.index(n):
            out.remove(n)
            out.insert(out.index(before[0]) + 1, n)
    return out

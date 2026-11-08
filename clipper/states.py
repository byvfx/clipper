"""Names the state files share, in one place: review decisions, the item kinds and what
each kind is in schedule.json and in Kitsu, schedule keys ("<stream id>/<item key>"), and
which stream folders are pre-shows grabbed from YouTube (yt-<id>)."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from . import jsonstate

PRESHOW_PREFIX = "yt-"   # streams/yt-<video id>: a pre-show or one-off from YouTube (clipper preshow)


class Decision(StrEnum):
    """review.json "decision", from the item's Kitsu task status (review.DECISIONS)."""
    APPROVED = "approved"
    CHANGES = "changes_requested"
    REJECTED = "rejected"
    PENDING = "pending"


# A review item's kind (review.json, review_state.json) -> its kind in schedule.json, and
# its Kitsu entity type. The same item is a "pick" in review and a "clip" once scheduled.
RELEASE_KIND = {"pick": "clip", "segment": "segment", "compilation": "compilation", "short": "short"}
ENTITY = {"pick": "Shot", "short": "Shot", "segment": "Sequence", "compilation": "Sequence"}


def split_key(key: str) -> tuple[str, str]:
    """A schedule key "kick-133423456/s04_baxter-bulletin" as (stream id, item key)."""
    stream, _, item = key.partition("/")
    return stream, item


def numbered_picks(picks: list[dict]) -> list[tuple[int, dict]]:
    """candidates.json pass_b picks with the number their keys carry (v03_t12, c03_t12,
    clips/03_t12_*): the "n" stored when the pick was made, else its place in the list
    (files from before numbers were stored, #77 L13)."""
    return [(p.get("n") or i, p) for i, p in enumerate(picks, 1)]


def number_history(pass_b: dict) -> dict[str, int]:
    """Every number a pick id has had in this stream: pass_b "numbers" (kept across
    re-scores, so a pick that drops out once gets its number back) plus the current picks'."""
    return {**pass_b.get("numbers", {}),
            **{p["id"]: n for n, p in numbered_picks(pass_b.get("picks", []))}}


def next_number(pass_b: dict) -> int:
    return max(number_history(pass_b).values(), default=0) + 1


def carry_numbers(old: dict, new: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """A re-score's picks, numbered so keys already in Kitsu and schedule.json still mean
    the same pick: an id that was ever picked keeps its number, a new one takes the next
    never-used one. Also returns the history to store as pass_b "numbers"."""
    had = number_history(old)
    nxt = next_number(old)
    out = []
    for p in new:
        if p["id"] not in had:
            had[p["id"]], nxt = nxt, nxt + 1
        out.append({**p, "n": had[p["id"]]})
    return out, had


def is_preshow(stream_dir: Path) -> bool:
    """A pre-show or one-off: its meta.json says kind "preshow" (one whole-video segment,
    the [preshow] stages instead of the worker's)."""
    try:
        meta = jsonstate.load(stream_dir / "meta.json")
    except (OSError, ValueError):
        return False
    return meta.get("kind") == "preshow"

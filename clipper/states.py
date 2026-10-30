"""Names the state files share, in one place: review decisions, the item kinds and what
each kind is in schedule.json and in Kitsu, and schedule keys ("<stream id>/<item key>")."""
from __future__ import annotations

from enum import StrEnum


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


def next_number(picks: list[dict]) -> int:
    return max((n for n, _ in numbered_picks(picks)), default=0) + 1


def carry_numbers(old: list[dict], new: list[dict]) -> list[dict]:
    """A re-score's picks, numbered so keys already in Kitsu and schedule.json still mean
    the same pick: an id that was picked before keeps its number, a new one takes the
    next unused one."""
    had = {p["id"]: n for n, p in numbered_picks(old)}
    nxt = next_number(old)
    out = []
    for p in new:
        n = had.get(p["id"])
        if n is None:
            n, nxt = nxt, nxt + 1
        out.append({**p, "n": n})
    return out

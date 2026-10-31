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

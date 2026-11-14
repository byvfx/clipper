import pytest

from clipper import receipts
from clipper.errors import ClipperError

DATA = {"subject": "jer", "show": "Kino Casino",
        "stories": [{"story": "The Lawsuit", "use": True}, {"story": "Skipped", "use": False},
                    {"story": "The Merch", "use": True}]}


def test_a_configured_title_leads(defaults):
    defaults["receipts"]["title"] = "The Receipts"
    title, _ = receipts.default_text(DATA, defaults)
    assert title == "The Receipts: The Lawsuit, The Merch"


def test_another_subject_without_a_title_is_named_after_itself(defaults):
    defaults["receipts"]["title"] = ""
    title, _ = receipts.default_text({**DATA, "subject": "ethan"}, defaults)
    assert title == "ethan receipts: The Lawsuit, The Merch"


def test_find_refuses_without_a_subject(defaults, tmp_path):
    """The default [receipts] is empty: `find` says what to set before it reads anything."""
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    with pytest.raises(ClipperError, match="subject is not set"):
        receipts.find(sdir, defaults)

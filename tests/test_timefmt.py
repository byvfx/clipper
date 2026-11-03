import pytest

from clipper.errors import ClipperError
from clipper.timefmt import clock, hms, length, parse_time


def test_hms_cuts_to_the_second():
    assert hms(0) == "0:00:00"
    assert hms(59.9) == "0:00:59"          # a chapter never starts after its moment
    assert hms(3725.4) == "1:02:05"
    assert hms(-3) == "0:00:00"


def test_clock_and_length():
    assert clock(65.9) == "1:05"
    assert clock(3600) == "1:00:00"
    assert length(65.6) == "1:06"          # lengths round
    assert length(3599.6) == "1:00:00"
    assert length(None) == "?"


@pytest.mark.parametrize("text, s", [
    ("1:02:03", 3723), ("4:13", 4 * 3600 + 13 * 60), ("4h13m", 15180), ("4h13m20s", 15200),
    ("253m", 15180), ("90", 90), ("90s", 90), ("1.5h", 5400),
])
def test_parse_time(text, s):
    assert parse_time(text) == s


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "h"])
def test_parse_time_refuses(text):
    with pytest.raises(ClipperError):  # the convention: one line for the user, no traceback
        parse_time(text)

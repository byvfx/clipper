"""Links into the docs, the GitHub wiki built from docs/wiki/, for messages that point at a
page. tests/test_wiki.py checks every `wiki.page(...)` call in clipper/ names a page (and
heading) that exists."""

from __future__ import annotations

URL = "https://github.com/byvfx/clipper/wiki"


def page(name: str, section: str = "") -> str:
    """`page("Speakers")` -> the page's address; `section` is a heading id ("watching-modal")."""
    return f"{URL}/{name}" + (f"#{section}" if section else "")

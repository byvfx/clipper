"""docs/wiki/ is pushed to the GitHub wiki (.github/workflows/wiki.yml): every page link must
resolve, and the sidebar must list every page."""
import re

from conftest import ROOT

WIKI = ROOT / "docs" / "wiki"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _pages():
    return {p.stem: p.read_text(encoding="utf-8") for p in WIKI.glob("*.md")}


def _no_blocks(text):
    return re.sub(r"^```.*?^```", "", text, flags=re.DOTALL | re.MULTILINE)


def _prose(text):
    """The text without code blocks and inline code, where `](` is not a link."""
    return re.sub(r"`[^`\n]*`", "", _no_blocks(text))


def _anchors(text):
    """GitHub's heading ids: lowercase, punctuation dropped, spaces to hyphens."""
    heads = re.findall(r"^#+ (.+)$", _no_blocks(text), flags=re.MULTILINE)
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in heads}


def test_page_links_resolve():
    pages = _pages()
    assert "Home" in pages
    bad = []
    for name, text in pages.items():
        for target in LINK.findall(_prose(text)):
            if re.match(r"(https?|mailto):", target):
                continue
            page, _, anchor = target.partition("#")
            page = page or name
            if page not in pages:
                bad.append(f"{name}: {target} (no page {page})")
            elif anchor and anchor not in _anchors(pages[page]):
                bad.append(f"{name}: {target} (no heading #{anchor} in {page})")
    assert not bad, "\n".join(bad)


def test_sidebar_lists_every_page():
    pages = _pages()
    listed = {t.partition("#")[0] for t in LINK.findall(pages["_Sidebar"])}
    missing = sorted(p for p in pages if not p.startswith("_") and p not in listed)
    assert not missing, "not in docs/wiki/_Sidebar.md: " + ", ".join(missing)


def test_anchor_slugs():
    text = "## Cut on the NAS (#51)\n## The walkthrough: `clipper setup`\n```bash\n# a comment\n```\n"
    assert _anchors(text) == {"cut-on-the-nas-51", "the-walkthrough-clipper-setup"}


def test_code_links_resolve():
    """Every `wiki.page("Name", "heading")` in clipper/ names a page and heading that exist."""
    pages = _pages()
    call = re.compile(r"""wiki\.page\(\s*["']([^"']+)["'](?:\s*,\s*["']([^"']*)["'])?\s*\)""")
    bad, seen = [], 0
    for path in (ROOT / "clipper").rglob("*.py"):
        for name, section in call.findall(path.read_text(encoding="utf-8")):
            seen += 1
            if name not in pages:
                bad.append(f"{path.name}: no page {name}")
            elif section and section not in _anchors(pages[name]):
                bad.append(f"{path.name}: no heading #{section} in {name}")
    assert seen and not bad, "\n".join(bad)

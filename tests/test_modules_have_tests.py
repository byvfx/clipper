"""Every module in clipper/ is named by some test (imported from tests/*.py), so a new module
arrives with at least one test of its own, and the list of modules without one can only
shrink. The audit of 2026-11-08 found 21 such modules; STILL_UNTESTED is what is left."""
import ast

from conftest import ROOT

# Modal images: they run on Modal without the package and can't be imported here.
RUNS_ELSEWHERE = {"clipper.modal_app", "clipper.modal_lipsync"}
# Take a name off this set when you give the module a test. Never add one.
STILL_UNTESTED = {"clipper.cardsync", "clipper.modalmon", "clipper.oauth", "clipper.perform",
                  "clipper.rumble", "clipper.speakers", "clipper.tiktok"}


def modules() -> set[str]:
    out = set()
    for p in (ROOT / "clipper").rglob("*.py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out.add(".".join(parts))
    out.discard("clipper")
    return out


def named_by_tests() -> set[str]:
    """Every clipper module a test imports: `from clipper import x`, `from clipper.x import y`,
    `import clipper.x`; a package counts for its submodules."""
    named = set()
    for t in (ROOT / "tests").glob("*.py"):
        for n in ast.walk(ast.parse(t.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "clipper":
                named.add(n.module)
                named.update(f"{n.module}.{a.name}" for a in n.names)
            elif isinstance(n, ast.Import):
                named.update(a.name for a in n.names if a.name.split(".")[0] == "clipper")
    return named


def test_every_module_is_named_by_a_test():
    named = named_by_tests()

    def covered(m: str) -> bool:
        return (m in named or any(n.startswith(m + ".") for n in named)
                or any(m.startswith(n + ".") for n in named if n != "clipper"))

    missing = sorted(m for m in modules() - RUNS_ELSEWHERE - STILL_UNTESTED if not covered(m))
    assert not missing, f"no test names {missing}: add tests/test_<name>.py, or import it in a test that fits"
    stale = sorted(m for m in STILL_UNTESTED if m not in modules() or covered(m))
    assert not stale, f"{stale} have a test now (or are gone): take them off STILL_UNTESTED"

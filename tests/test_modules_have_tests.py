"""Every module in clipper/ is named by some test, so a new module arrives with at least one
test of its own, and the list of modules without one can only shrink. The audit of 2026-11-08
found 21 such modules; STILL_UNTESTED is what is left.

A test names a module by importing it (`from clipper import x`, `from clipper.pkg import x`,
`import clipper.pkg.x`) or, for a package such as warroom, by using something the package
re-exports (`warroom.faces_get(...)`, `warroom.CAST_JOB`): the name is traced back to the
submodule that defines it. A package import alone names nothing inside the package, so a new
panel module no test touches fails here (Copilot on #177)."""
import ast
import importlib
import inspect
import re

from conftest import ROOT

# Modal images: they run on Modal without the package and can't be imported here.
RUNS_ELSEWHERE = {"clipper.modal_app", "clipper.modal_lipsync"}
# Take a name off this set when you give the module a test. Never add one.
STILL_UNTESTED = {
    "clipper.cardsync", "clipper.modalmon", "clipper.oauth", "clipper.perform", "clipper.rumble",
    "clipper.speakers", "clipper.tiktok", "clipper.record.client", "clipper.record.watch",
    "clipper.thumbs.classic", "clipper.warroom.covers", "clipper.warroom.lowres", "clipper.warroom.search",
}


def modules() -> set[str]:
    out = set()
    for p in (ROOT / "clipper").rglob("*.py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out.add(".".join(parts))
    out.discard("clipper")
    return out


def reexports(pkg: str) -> dict[str, str]:
    """{name: submodule} for what a package's __init__ imports from its own modules."""
    init = ROOT / pkg.replace(".", "/") / "__init__.py"
    out = {}
    if not init.exists():
        return out
    for n in ast.walk(ast.parse(init.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module:
            for a in n.names:
                out[a.asname or a.name] = f"{pkg}.{n.module}"
    return out


def imports_in(tree: ast.AST, all_modules: set[str], packages: set[str]) -> tuple[set[str], dict[str, str]]:
    """The clipper modules one test file imports, and the packages it binds to a local name."""
    named, aliases = set(), {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "clipper":
            named.add(n.module)
            for a in n.names:
                full = f"{n.module}.{a.name}"
                if full in all_modules:
                    named.add(full)
                elif n.module in packages:   # from clipper.warroom import faces_get
                    named.add(reexports(n.module).get(a.name, n.module))
                if full in packages:
                    aliases[a.asname or a.name] = full
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "clipper":
                    named.add(a.name)
                    if a.name in packages:
                        aliases[a.asname or a.name.split(".")[-1]] = a.name
    return named, aliases


def used_through(src: str, local: str, pkg: str, all_modules: set[str]) -> set[str]:
    """The submodules behind `local.<attr>` uses of a package in one test file: a submodule
    named outright, a re-exported name, or an object whose __module__ is inside the package."""
    table, module, named = reexports(pkg), importlib.import_module(pkg), set()
    for attr in set(re.findall(rf"\b{re.escape(local)}\.(\w+)", src)):
        if f"{pkg}.{attr}" in all_modules:
            named.add(f"{pkg}.{attr}")
        elif attr in table:
            named.add(table[attr])
        else:
            obj = getattr(module, attr, None)
            mod = inspect.getmodule(obj) if obj is not None else None
            if mod and mod.__name__.startswith(pkg + "."):
                named.add(mod.__name__)
    return named


def named_by_tests(all_modules: set[str]) -> set[str]:
    named = set()
    packages = {m for m in all_modules if any(x.startswith(m + ".") for x in all_modules)}
    for t in (ROOT / "tests").glob("*.py"):
        src = t.read_text(encoding="utf-8")
        found, aliases = imports_in(ast.parse(src), all_modules, packages)
        named |= found
        for local, pkg in aliases.items():
            named |= used_through(src, local, pkg, all_modules)
    return named


def test_every_module_is_named_by_a_test():
    mods = modules()
    named = named_by_tests(mods)
    missing = sorted(m for m in mods - RUNS_ELSEWHERE - STILL_UNTESTED if m not in named)
    assert not missing, f"no test names {missing}: add tests/test_<name>.py, or use it from a test that fits"
    stale = sorted(m for m in STILL_UNTESTED if m not in mods or m in named)
    assert not stale, f"{stale} have a test now (or are gone): take them off STILL_UNTESTED"

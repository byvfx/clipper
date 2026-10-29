"""JSON files are read with jsonstate.load / jsonstate.read, not json.loads(path.read_text()):
a file another process is replacing (schedule.json, review.json, run.json, ...) is retried
instead of failing or being read as empty. JSONL and files with a BOM (utf-8-sig, written
by other tools) are read line by line or as text, so they are left alone."""
import re

from conftest import ROOT

RAW_READ = re.compile(r"json\.loads\([^)]*\.read_text\(")


def test_no_raw_json_reads():
    found = []
    for path in sorted((ROOT / "clipper").glob("*.py")):
        if path.name in ("jsonstate.py", "modal_app.py"):  # modal_app runs without the package
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RAW_READ.search(line) and "utf-8-sig" not in line:
                found.append(f"{path.name}:{n}")
    assert not found, "use jsonstate.load(path) or jsonstate.read(path, default): " + ", ".join(found)

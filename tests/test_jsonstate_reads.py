"""A ratchet on raw `json.loads(path.read_text())`. State files other processes replace
(schedule.json, review.json, run.json, ...) should be read with jsonstate.read, which
retries a file caught mid-replace instead of failing or reading it as empty. Each module
may not gain raw reads; when you convert some, lower its number here."""
import re

from conftest import ROOT

RAW_READ = re.compile(r"json\.loads\(.*\.read_text\(")

ALLOWED = {
    "actions": 1, "alerts": 3, "analytics": 1, "archive": 3, "archive_index": 2, "config": 1,
    "costs": 2, "cut": 5, "doctor": 2, "kick": 1, "meta": 6, "modalmon": 2, "notify": 2,
    "perform": 2, "pick": 1, "preshow": 3, "receipts": 5, "record": 1, "retake": 1,
    "review": 14, "rumble": 4, "schedule": 7, "scoring": 4, "segments": 1, "setup": 1,
    "shorts": 4, "signals": 2, "speakers": 9, "thumbs": 10, "tiktok": 4, "warroom": 1,
    "weekly": 2, "youtube": 14,
}


def test_no_new_raw_json_reads():
    over = []
    for path in sorted((ROOT / "clipper").glob("*.py")):
        n = len(RAW_READ.findall(path.read_text(encoding="utf-8")))
        if n > ALLOWED.get(path.stem, 0):
            over.append(f"{path.name}: {n} (allowed {ALLOWED.get(path.stem, 0)})")
    assert not over, "use jsonstate.read(path, default) for: " + ", ".join(over)

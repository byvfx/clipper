"""The investigations window (#108): the leads dossiers, rendered to read."""
from __future__ import annotations

from pathlib import Path

from .. import jsonstate


def investigations_get(cfg: dict, subject: str = "", run: str = "") -> dict:
    """Every archive subject with leads, and one subject's dossier (leads.md's markdown, built
    fresh from leads.json); the newest investigation when subject isn't one of them. run (from
    a Discord link): that leads run's dossier, from its backup when a later run replaced it."""
    from .. import investigate

    found = []
    for p in sorted(Path(cfg["paths"]["archive"]).glob("*/investigate/leads.json")):
        data = jsonstate.read(p, {})
        if data.get("leads") is not None:
            found.append((p.parent.parent.name, data))
    found.sort(key=lambda sd: sd[1].get("made_at", ""), reverse=True)
    subjects = [{"subject": s, "made_at": d.get("made_at", ""), "about": d.get("about", ""),
                 "kept": sum(x["keep"] for x in d["leads"])} for s, d in found]
    pick = next((sd for sd in found if sd[0] == subject), found[0] if found else None)
    if not pick:
        return {"subjects": subjects, "subject": "", "run": "", "markdown": "", "missing_run": False}
    data = pick[1]
    if run and run != investigate.run_id(data):
        data = investigate.load_run(cfg, pick[0], run)
    return {"subjects": subjects, "subject": pick[0], "run": investigate.run_id(data) if data else run,
            "current": investigate.run_id(pick[1]), "markdown": investigate.dossier(data) if data else "",
            "missing_run": data is None}
